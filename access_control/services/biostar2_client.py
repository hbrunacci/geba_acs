from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import os
import requests
from django.utils import timezone

from access_control.models.biostar_config import BioStar2Config


@dataclass(frozen=True)
class BioStar2Env:
    base_url: str
    username: str
    password: str
    verify_tls: bool
    timeout_seconds: int

    @classmethod
    def from_env(cls) -> "BioStar2Env":
        base_url = os.environ["BIOSTAR_BASE_URL"].rstrip("/")
        username = os.environ["BIOSTAR_USERNAME"]
        password = os.environ["BIOSTAR_PASSWORD"]
        verify_tls = os.getenv("BIOSTAR_VERIFY_TLS", "0") == "1"
        timeout_seconds = int(os.getenv("BIOSTAR_TIMEOUT_SECONDS", "15"))
        return cls(
            base_url=base_url,
            username=username,
            password=password,
            verify_tls=verify_tls,
            timeout_seconds=timeout_seconds,
        )


class BioStar2Client:
    """
    Cliente para BioStar 2 New Local API.

    - Mantiene bs-session-id en DB (BioStar2Config)
    - Reintenta login automáticamente si la sesión expira / el server devuelve 401/LOGIN REQUIRED
    """

    def __init__(self, cfg: BioStar2Config, env: BioStar2Env):
        self.cfg = cfg
        self.env = env
        self.session = requests.Session()

    @classmethod
    def from_db_and_env(cls) -> "BioStar2Client":
        cfg = BioStar2Config.get_solo()
        env = BioStar2Env.from_env()

        # Mantener cfg alineado con env (base_url/username/flags)
        changed = False
        if cfg.base_url.rstrip("/") != env.base_url:
            cfg.base_url = env.base_url
            changed = True
        if cfg.username != env.username:
            cfg.username = env.username
            changed = True
        if cfg.verify_tls != env.verify_tls:
            cfg.verify_tls = env.verify_tls
            changed = True
        if cfg.timeout_seconds != env.timeout_seconds:
            cfg.timeout_seconds = env.timeout_seconds
            changed = True
        if changed:
            cfg.save()

        return cls(cfg=cfg, env=env)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "accept": "application/json",
        }
        if self.cfg.bs_session_id:
            headers["bs-session-id"] = self.cfg.bs_session_id
        return headers

    def login(self) -> None:
        url = f"{self.env.base_url}/api/login"
        payload = {"User": {"login_id": self.env.username, "password": self.env.password}}

        resp = self.session.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", "accept": "application/json"},
            verify=self.env.verify_tls,
            timeout=self.env.timeout_seconds,
        )
        resp.raise_for_status()

        session_id = resp.headers.get("bs-session-id")
        if not session_id:
            raise RuntimeError("Login OK pero no vino header bs-session-id")

        self.cfg.set_session(session_id.strip())

    def request(self, method: str, path: str, *, json=None, params=None):
        if not path.startswith("/"):
            path = "/" + path

        if not self.cfg.bs_session_id:
            self.login()

        url = f"{self.env.base_url}{path}"

        resp = self.session.request(
            method=method.upper(),
            url=url,
            headers=self._headers(),
            json=json,
            params=params,
            verify=self.env.verify_tls,
            timeout=self.env.timeout_seconds,
        )

        if resp.status_code == 401:
            # sesión vencida o inválida
            self.login()
            resp = self.session.request(
                method=method.upper(),
                url=url,
                headers=self._headers(),
                json=json,
                params=params,
                verify=self.env.verify_tls,
                timeout=self.env.timeout_seconds,
            )

        resp.raise_for_status()
        return resp

    def list_devices(self) -> dict[str, Any]:
        """
        Lista dispositivos agregados en BioStar 2.
        Endpoint conocido: GET /api/devices. :contentReference[oaicite:6]{index=6}
        """
        return self.request("GET", "/api/devices").json()

    def list_users(self, *, limit: int = 200, offset: int = 0) -> dict:
        return self.request("GET", "/api/users", params={"limit": limit, "offset": offset}).json()

    def get_user(self, user_id: int) -> dict | None:
        """Trae el detalle en vivo de un usuario puntual. None si no existe en BioStar (404)."""
        try:
            return self.request("GET", f"/api/users/{user_id}").json()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    def list_device_groups(self) -> dict:
        """
        Lista grupos de dispositivos en BioStar 2
        """
        return self.request("GET", "/api/device_groups").json()

    def move_device_group(self, device_id: int, group_id: int) -> dict:
        """Mueve un dispositivo a otro grupo de dispositivos (device_group).

        No existe un endpoint dedicado "move_device_group" en esta instancia de
        BioStar 2 (probado, devuelve 400 "Request is not supported"). Lo que sí
        funciona es un PUT a /api/devices/{id} con el objeto Device parcial.
        """
        payload = {"Device": {"id": str(device_id), "device_group_id": {"id": str(group_id)}}}
        return self.request("PUT", f"/api/devices/{device_id}", json=payload).json()

    def list_device_users(self, device_id: int, *, limit: int = 1, offset: int = 0) -> dict:
        return self.request(
            "GET",
            f"/api/devices/{device_id}/users",
            params={"limit": limit, "offset": offset},
        ).json()

    def discover_device_userdata(self, device_id: int) -> dict:
        return self.request("GET", f"/api/devices/{device_id}/discover_userdata").json()

    def list_devices_by_group(self, group_id: int) -> list[int]:
        """IDs de los dispositivos que pertenecen a un device_group. Usa /api/v2/devices/search."""
        payload = {"limit": 500, "device_group_id": str(group_id)}
        data = self.request("POST", "/api/v2/devices/search", json=payload).json()
        rows = data.get("DeviceCollection", {}).get("rows", [])
        return [int(row["id"]) for row in rows]

    def add_user_to_device(
        self, user_ids: int | list[int], device_id: int, *, overwrite: bool = True
    ) -> dict:
        """Envía (agrega) uno o más usuarios a un dispositivo puntual, sin resincronizar todo.

        Endpoint: POST /api/users/export (ids de usuario separados por '+' en la query).
        """
        ids = user_ids if isinstance(user_ids, list) else [user_ids]
        id_param = "+".join(str(i) for i in ids)
        payload = {"DeviceCollection": {"rows": [{"id": str(device_id)}]}}
        return self.request(
            "POST",
            "/api/users/export",
            params={"overwrite": str(overwrite).lower(), "id": id_param},
            json=payload,
        ).json()

    def add_user_to_device_group(
        self, user_ids: int | list[int], group_id: int, *, overwrite: bool = True
    ) -> dict:
        """Envía uno o más usuarios a TODOS los dispositivos de un device_group, en una sola llamada."""
        device_ids = self.list_devices_by_group(group_id)
        ids = user_ids if isinstance(user_ids, list) else [user_ids]
        id_param = "+".join(str(i) for i in ids)
        payload = {"DeviceCollection": {"rows": [{"id": str(d)} for d in device_ids]}}
        return self.request(
            "POST",
            "/api/users/export",
            params={"overwrite": str(overwrite).lower(), "id": id_param},
            json=payload,
        ).json()

    def remove_user_from_device(self, device_id: int, user_id: int | str = "*") -> dict:
        """Borra un usuario puntual (o todos, con user_id='*') de la caché local de un dispositivo."""
        return self.request(
            "DELETE",
            f"/api/devices/{device_id}/users",
            params={"id": str(user_id)},
        ).json()

    def remove_user_from_device_group(self, group_id: int, user_id: int | str = "*") -> dict:
        """Borra un usuario puntual (o todos) de TODOS los dispositivos de un device_group.

        No existe un endpoint de borrado masivo por grupo; se itera dispositivo por
        dispositivo. Devuelve {device_id: respuesta} para poder ver qué falló.
        """
        device_ids = self.list_devices_by_group(group_id)
        results: dict[int, dict] = {}
        for device_id in device_ids:
            results[device_id] = self.remove_user_from_device(device_id, user_id)
        return results

    def search_users_v2(
        self,
        *,
        search_text: str,
        user_group_id: str = "1",
        limit: int = 50,
        offset: int = 0,
        order_by: str = "user_id:false",
    ) -> dict:
        payload = {
            "limit": limit,
            "offset": offset,
            "search_text": search_text,
            "user_group_id": user_group_id,
            "order_by": order_by,
        }
        return self.request("POST", "/api/v2/users/search", json=payload).json()

    # ------------------------------------------------------------------ #
    # Administración de equipos (hardware / operaciones)                  #
    # ------------------------------------------------------------------ #

    def get_device(self, device_id: int) -> dict:
        """Ficha completa de un dispositivo (red, firmware, tipo, grupo, capacidades)."""
        return self.request("GET", f"/api/devices/{device_id}").json()

    def rename_device(self, device_id: int, name: str) -> dict:
        """Renombra un dispositivo. PUT parcial del objeto Device (mismo patrón que move_device_group)."""
        payload = {"Device": {"id": str(device_id), "name": name}}
        return self.request("PUT", f"/api/devices/{device_id}", json=payload).json()

    def reboot_device(self, device_id: int) -> dict:
        """Reinicia el dispositivo.

        endpoint a confirmar en prueba en vivo: POST /api/devices/reboot con DeviceCollection.
        Si esta instancia no lo soporta, ajustar path/body en un solo lugar.
        """
        payload = {"DeviceCollection": {"rows": [{"id": str(device_id)}]}}
        return self.request("POST", "/api/devices/reboot", json=payload).json()

    def lock_device(self, device_id: int) -> dict:
        """Bloquea el dispositivo (deshabilita la autenticación).

        endpoint a confirmar en prueba en vivo: POST /api/devices/lock con DeviceCollection.
        """
        payload = {"DeviceCollection": {"rows": [{"id": str(device_id)}]}}
        return self.request("POST", "/api/devices/lock", json=payload).json()

    def unlock_device(self, device_id: int) -> dict:
        """Desbloquea el dispositivo (rehabilita la autenticación).

        endpoint a confirmar en prueba en vivo: POST /api/devices/unlock con DeviceCollection.
        """
        payload = {"DeviceCollection": {"rows": [{"id": str(device_id)}]}}
        return self.request("POST", "/api/devices/unlock", json=payload).json()

    def list_doors(self) -> dict:
        """Lista todas las puertas configuradas en BioStar 2."""
        return self.request("GET", "/api/doors").json()

    def door_status(self) -> dict:
        """Estado en tiempo real de todas las puertas (opened/unlocked/alarm por door_id)."""
        return self.request("POST", "/api/doors/status", json={}).json()

    DOOR_ACTIONS = ("open", "lock", "unlock", "release")

    def door_action(self, door_id: int, action: str) -> dict:
        """Ejecuta una acción manual sobre una puerta.

        action ∈ {open, lock, unlock, release}. endpoint a confirmar en prueba en vivo:
        POST /api/doors/{action} con DoorCollection.
        """
        if action not in self.DOOR_ACTIONS:
            raise ValueError(f"Acción de puerta inválida: {action!r}")
        payload = {"DoorCollection": {"rows": [{"id": str(door_id)}]}}
        return self.request("POST", f"/api/doors/{action}", json=payload).json()

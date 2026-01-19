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
        headers = {"Content-Type": "application/json"}
        if self.cfg.bs_session_id:
            headers["bs-session-id"] = self.cfg.bs_session_id
        return headers

    def login(self) -> None:
        """
        Login y persistencia del bs-session-id.

        Doc: login es POST /login, y el header 'bs-session-id' viene en la respuesta. :contentReference[oaicite:4]{index=4} :contentReference[oaicite:5]{index=5}
        """
        # En muchos entornos el endpoint es /api/login (swagger local suele mostrar /login).
        # Intentamos primero /api/login y si da 404 probamos /login.
        payload = {"User": {"login_id": self.env.username, "password": self.env.password}}

        for path in ("/api/login", "/login"):
            url = f"{self.env.base_url}{path}"
            resp = self.session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                verify=self.env.verify_tls,
                timeout=self.env.timeout_seconds,
            )
            if resp.status_code == 404:
                continue

            resp.raise_for_status()
            session_id = resp.headers.get("bs-session-id")
            if not session_id:
                raise RuntimeError("Login OK pero no vino header bs-session-id")

            self.cfg.set_session(session_id)
            return

        raise RuntimeError("No se encontró endpoint de login (/api/login ni /login)")

    def request(self, method: str, path: str, *, json: Any | None = None, params: dict[str, Any] | None = None) -> requests.Response:
        """
        Hace un request autenticado.
        Si la sesión no existe o expira, re-loguea una vez y reintenta.
        """
        if not path.startswith("/"):
            path = "/" + path

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

        if resp.status_code in (401, 403):
            # Reintento 1 vez: sesión expirada / LOGIN REQUIRED
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

from __future__ import annotations

from collections.abc import Callable
from ctypes import Array, Structure
from datetime import datetime
from socket import timeout as SocketTimeout
from typing import Any

from access_control.services.intelectron.api3000_wrapper.api3000 import (
    Api3000Client,
    Api3000Error,
    Api3000NativeError,
    ITKDateTime,
    ITKMarkInfo,
    ITKUserInfo,
)


class Api3000ServiceError(Exception):
    """Error base para operaciones del servicio API-3000."""


class Api3000ConnectionError(Api3000ServiceError):
    """Error de conectividad o timeout."""


class Api3000CommandError(Api3000ServiceError):
    """Error de comando inválido o no permitido."""


class Api3000GatewayError(Api3000ServiceError):
    """Error nativo controlado de API-3000."""


class Api3000Service:
    def __init__(self, *, timeout: int = 5000) -> None:
        self.timeout = int(timeout)

    def ping(self, *, ip: str, port: int = 3001, dest_node: int = 1) -> dict[str, Any]:
        conn_string = self._build_conn_string(ip=ip, port=port)
        try:
            with Api3000Client(conn_string=conn_string, timeout=self.timeout) as client:
                date_time = client.get_time(dest_node=dest_node)
            return {
                "status": "ok",
                "conn_string": conn_string,
                "dest_node": dest_node,
                "device_time": self.serialize(date_time),
            }
        except Api3000NativeError as exc:
            raise Api3000GatewayError(f"Error nativo API3000: {exc}") from exc
        except (TimeoutError, SocketTimeout) as exc:
            raise Api3000ConnectionError(f"Timeout de conexión API3000: {exc}") from exc
        except Api3000Error as exc:
            message = str(exc).lower()
            if "timeout" in message or "socket" in message or "conectar" in message:
                raise Api3000ConnectionError(f"No se pudo conectar al equipo: {exc}") from exc
            raise Api3000GatewayError(f"Error API3000: {exc}") from exc
        except OSError as exc:
            raise Api3000ConnectionError(f"Error de red API3000: {exc}") from exc

    def execute_command(
        self,
        *,
        ip: str,
        command: str,
        params: dict[str, Any] | None = None,
        port: int = 3001,
        dest_node: int = 1,
    ) -> dict[str, Any]:
        conn_string = self._build_conn_string(ip=ip, port=port)
        command_name = (command or "").strip()
        command_fn = self._allowed_commands().get(command_name)
        if command_fn is None:
            allowed = ", ".join(sorted(self._allowed_commands().keys()))
            raise Api3000CommandError(f"Comando no permitido: '{command_name}'. Permitidos: {allowed}")

        payload = dict(params or {})

        try:
            with Api3000Client(conn_string=conn_string, timeout=self.timeout) as client:
                result = command_fn(client, dest_node, payload)
            return {
                "status": "ok",
                "conn_string": conn_string,
                "dest_node": dest_node,
                "command": command_name,
                "result": self.serialize(result),
            }
        except Api3000CommandError:
            raise
        except Api3000NativeError as exc:
            raise Api3000GatewayError(f"Error nativo API3000: {exc}") from exc
        except (TimeoutError, SocketTimeout) as exc:
            raise Api3000ConnectionError(f"Timeout de conexión API3000: {exc}") from exc
        except Api3000Error as exc:
            message = str(exc).lower()
            if "timeout" in message or "socket" in message or "conectar" in message:
                raise Api3000ConnectionError(f"No se pudo conectar al equipo: {exc}") from exc
            raise Api3000GatewayError(f"Error API3000: {exc}") from exc
        except OSError as exc:
            raise Api3000ConnectionError(f"Error de red API3000: {exc}") from exc

    def _build_conn_string(self, *, ip: str, port: int) -> str:
        safe_ip = (ip or "").strip()
        if not safe_ip:
            raise Api3000CommandError("El campo 'ip' es obligatorio")
        return f"{safe_ip}:{int(port)}"

    def _allowed_commands(self) -> dict[str, Callable[[Api3000Client, int, dict[str, Any]], Any]]:
        return {
            "lib_version": self._cmd_lib_version,
            "get_time": self._cmd_get_time,
            "state_aux_input": self._cmd_state_aux_input,
            "get_numeric_config": self._cmd_get_numeric_config,
            "list_users": self._cmd_list_users,
            "list_marks": self._cmd_list_marks,
        }

    def _cmd_lib_version(self, client: Api3000Client, dest_node: int, params: dict[str, Any]) -> str:
        del dest_node, params
        return client.lib_version()

    def _cmd_get_time(self, client: Api3000Client, dest_node: int, params: dict[str, Any]) -> ITKDateTime:
        del params
        return client.get_time(dest_node=dest_node)

    def _cmd_state_aux_input(self, client: Api3000Client, dest_node: int, params: dict[str, Any]) -> int:
        del params
        return client.state_aux_input(dest_node=dest_node)

    def _cmd_get_numeric_config(self, client: Api3000Client, dest_node: int, params: dict[str, Any]) -> int:
        try:
            num_size = int(params["num_size"])
            num_id = int(params["num_id"])
        except KeyError as exc:
            raise Api3000CommandError("Falta parámetro requerido: num_size o num_id") from exc
        return client.get_numeric_config(dest_node=dest_node, num_size=num_size, num_id=num_id)

    def _cmd_list_users(self, client: Api3000Client, dest_node: int, params: dict[str, Any]) -> tuple[list[ITKUserInfo], int]:
        start_position = int(params.get("start_position", 0))
        records_to_list = int(params.get("records_to_list", 1))
        return client.list_users(
            dest_node=dest_node,
            start_position=start_position,
            records_to_list=records_to_list,
        )

    def _cmd_list_marks(self, client: Api3000Client, dest_node: int, params: dict[str, Any]) -> tuple[list[ITKMarkInfo], int]:
        start_position = int(params.get("start_position", 0))
        records_to_list = int(params.get("records_to_list", 1))
        return client.list_marks(
            dest_node=dest_node,
            start_position=start_position,
            records_to_list=records_to_list,
        )

    @classmethod
    def serialize(cls, value: Any) -> Any:
        if isinstance(value, ITKDateTime):
            return {
                "hour": int(value.hour),
                "minute": int(value.minute),
                "seconds": int(value.seconds),
                "year": int(value.year),
                "month": int(value.month),
                "day": int(value.day),
                "dayofweek": int(value.dayofweek),
                "iso_datetime": value.to_python_datetime().isoformat(),
            }

        if isinstance(value, ITKUserInfo):
            return {
                "mask_fields": int(value.mask_fields),
                "access_id": int(value.access_id),
                "password": int(value.password),
                "status": int(value.status),
                "access_ctl": int(value.access_ctl),
                "panic_code": int(value.panic_code),
                "bio_count": int(value.bio_count),
                "bio_level": int(value.bio_level),
                "sec_level": int(value.sec_level),
                "user_name": value.get_user_name(),
                "user_msg": value.get_user_msg(),
                "user_id": value.get_user_id(),
                "schedule_id": int(value.schedule_id),
                "anti_passback": int(value.anti_passback),
            }

        if isinstance(value, ITKMarkInfo):
            return {
                "mask_fields": int(value.mask_fields),
                "type": int(value.type),
                "access_id": int(value.access_id),
                "date_time": cls.serialize(value.date_time),
                "event_code": int(value.event_code),
                "source": int(value.source),
                "direction": int(value.direction),
                "supervisor_id": int(value.supervisor_id),
                "task_item_id": int(value.task_item_id),
                "job_order": value.get_job_order(),
            }

        if isinstance(value, datetime):
            return value.isoformat()

        if isinstance(value, tuple):
            return [cls.serialize(item) for item in value]

        if isinstance(value, list):
            return [cls.serialize(item) for item in value]

        if isinstance(value, dict):
            return {str(key): cls.serialize(item) for key, item in value.items()}

        if isinstance(value, Array):
            return [cls.serialize(item) for item in value]

        if isinstance(value, Structure):
            return {
                field_name: cls.serialize(getattr(value, field_name))
                for field_name, _field_type in value._fields_
            }

        return value

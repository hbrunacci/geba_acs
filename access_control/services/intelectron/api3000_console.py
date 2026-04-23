from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError


def _safe_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({field_name: "Debe ser numérico."}) from exc


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


COMMAND_CATALOG: dict[str, dict[str, Any]] = {
    "lib_version": {
        "label": "Versión de librería",
        "description": "Lee la versión de la librería ITKCom.",
        "requires_dest_node": False,
        "params": [],
    },
    "get_time": {
        "label": "Obtener hora",
        "description": "Consulta fecha/hora del nodo destino.",
        "requires_dest_node": True,
        "params": [],
    },
    "set_time": {
        "label": "Ajustar hora",
        "description": "Escribe fecha/hora en el nodo destino.",
        "requires_dest_node": True,
        "params": [
            {"name": "datetime", "type": "datetime-local", "required": True, "help": "Fecha/hora a enviar."},
        ],
    },
    "list_users": {
        "label": "Listar usuarios",
        "description": "Lista usuarios por rango de registros.",
        "requires_dest_node": True,
        "params": [
            {"name": "start_position", "type": "number", "required": True, "default": 0},
            {"name": "records_to_list", "type": "number", "required": True, "default": 10},
        ],
    },
    "add_user": {
        "label": "Alta de usuario",
        "description": "Agrega un usuario en el nodo destino.",
        "requires_dest_node": True,
        "params": [
            {"name": "record_index", "type": "number", "required": True, "default": 0},
            {"name": "user_id", "type": "text", "required": True},
            {"name": "user_name", "type": "text", "required": True},
            {"name": "access_id", "type": "number", "required": True},
            {"name": "status", "type": "number", "required": False, "default": 1},
            {"name": "password", "type": "number", "required": False, "default": 0},
            {"name": "schedule_id", "type": "number", "required": False, "default": 0},
        ],
    },
    "edit_user": {
        "label": "Editar usuario",
        "description": "Edita un usuario por índice de registro.",
        "requires_dest_node": True,
        "params": [
            {"name": "record_index", "type": "number", "required": True},
            {"name": "user_id", "type": "text", "required": True},
            {"name": "user_name", "type": "text", "required": True},
            {"name": "access_id", "type": "number", "required": True},
            {"name": "status", "type": "number", "required": False, "default": 1},
            {"name": "password", "type": "number", "required": False, "default": 0},
            {"name": "schedule_id", "type": "number", "required": False, "default": 0},
        ],
    },
    "delete_user": {
        "label": "Eliminar usuario",
        "description": "Elimina un usuario por identificador.",
        "requires_dest_node": True,
        "params": [
            {"name": "record_index", "type": "number", "required": True},
            {"name": "user_id", "type": "text", "required": True},
            {"name": "access_id", "type": "number", "required": True},
        ],
    },
    "list_marks": {
        "label": "Listar marcas",
        "description": "Lista marcas históricas por rango.",
        "requires_dest_node": True,
        "params": [
            {"name": "start_position", "type": "number", "required": True, "default": 0},
            {"name": "records_to_list", "type": "number", "required": True, "default": 10},
        ],
    },
}


@dataclass
class Api3000ConnectionConfig:
    ip: str
    port: int
    source_node: int

    @property
    def conn_string(self) -> str:
        return f"{self.ip}:{self.port}"


def validate_base_payload(payload: dict[str, Any], *, command: str | None = None) -> dict[str, Any]:
    errors: dict[str, str] = {}
    ip = _safe_str(payload.get("ip"))
    if not ip:
        errors["ip"] = "La IP es obligatoria."

    try:
        port = _safe_int(payload.get("port"), "port")
    except ValidationError:
        errors["port"] = "El puerto debe ser numérico."
        port = 0

    try:
        source_node = _safe_int(payload.get("source_node"), "source_node")
    except ValidationError:
        errors["source_node"] = "source_node debe ser numérico."
        source_node = 0

    dest_node = payload.get("dest_node")
    if command:
        meta = COMMAND_CATALOG.get(command)
        if not meta:
            errors["command"] = "Comando inválido."
        elif meta.get("requires_dest_node"):
            if dest_node in (None, ""):
                errors["dest_node"] = "dest_node es obligatorio para este comando."
            else:
                try:
                    dest_node = _safe_int(dest_node, "dest_node")
                except ValidationError:
                    errors["dest_node"] = "dest_node debe ser numérico."
        elif dest_node not in (None, ""):
            try:
                dest_node = _safe_int(dest_node, "dest_node")
            except ValidationError:
                errors["dest_node"] = "dest_node debe ser numérico."

    if errors:
        raise ValidationError(errors)

    return {
        "ip": ip,
        "port": port,
        "source_node": source_node,
        "dest_node": dest_node,
    }


def validate_command_params(command: str, params: dict[str, Any]) -> dict[str, Any]:
    meta = COMMAND_CATALOG.get(command)
    if not meta:
        raise ValidationError({"command": "Comando no soportado."})

    parsed: dict[str, Any] = {}
    errors: dict[str, str] = {}

    for spec in meta.get("params", []):
        name = spec["name"]
        value = params.get(name, spec.get("default"))
        if spec.get("required") and value in (None, ""):
            errors[name] = "Campo obligatorio."
            continue
        if value in (None, ""):
            continue

        if spec.get("type") == "number":
            try:
                parsed[name] = int(value)
            except (TypeError, ValueError):
                errors[name] = "Debe ser numérico."
        elif name == "datetime":
            try:
                parsed[name] = datetime.fromisoformat(str(value))
            except (TypeError, ValueError):
                errors[name] = "Formato inválido. Use YYYY-MM-DDTHH:MM."
        else:
            parsed[name] = _safe_str(value)

    if errors:
        raise ValidationError(errors)

    return parsed


def _serialize_user(user: Any) -> dict[str, Any]:
    return {
        "access_id": int(user.access_id),
        "user_id": user.get_user_id(),
        "user_name": user.get_user_name(),
        "status": int(user.status),
        "password": int(user.password),
        "schedule_id": int(user.schedule_id),
    }


def _serialize_mark(mark: Any) -> dict[str, Any]:
    return {
        "access_id": int(mark.access_id),
        "event_code": int(mark.event_code),
        "direction": int(mark.direction),
        "source": int(mark.source),
        "timestamp": {
            "year": int(mark.date_time.year),
            "month": int(mark.date_time.month),
            "day": int(mark.date_time.day),
            "hour": int(mark.date_time.hour),
            "minute": int(mark.date_time.minute),
            "seconds": int(mark.date_time.seconds),
        },
    }


def execute_command(*, command: str, base: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    try:
        from api3000 import Api3000Client, PacketProtocol
        from api3000.structs import ITKUserInfo
    except ImportError:
        from access_control.services.intelectron.api3000_wrapper.api3000 import Api3000Client, PacketProtocol
        from access_control.services.intelectron.api3000_wrapper.api3000.structs import ITKUserInfo

    with Api3000Client(
        source_node=base["source_node"],
        packet_protocol=PacketProtocol.NEXT,
        conn_string=f"{base['ip']}:{base['port']}",
    ) as client:
        dest_node = base.get("dest_node")
        if command == "lib_version":
            return {"command": command, "lib_version": client.lib_version()}
        if command == "get_time":
            dt = client.get_time_as_datetime(dest_node=dest_node)
            return {"command": command, "datetime": dt.isoformat()}
        if command == "set_time":
            client.set_time(dest_node=dest_node, value=params["datetime"])
            return {"command": command, "ok": True}
        if command == "list_users":
            users, count = client.list_users(
                dest_node=dest_node,
                start_position=params.get("start_position", 0),
                records_to_list=params.get("records_to_list", 10),
            )
            return {"command": command, "count": count, "users": [_serialize_user(item) for item in users]}
        if command in {"add_user", "edit_user", "delete_user"}:
            user = ITKUserInfo()
            user.access_id = params.get("access_id", 0)
            user.password = params.get("password", 0)
            user.status = params.get("status", 1)
            user.schedule_id = params.get("schedule_id", 0)
            if params.get("user_name"):
                user.set_user_name(params["user_name"])
            if params.get("user_id"):
                user.set_user_id(params["user_id"])

            if command == "add_user":
                affected = client.add_user(dest_node=dest_node, user=user, record_index=params.get("record_index", 0))
            elif command == "edit_user":
                affected = client.edit_user(dest_node=dest_node, user=user, record_index=params.get("record_index", 0))
            else:
                affected = client.delete_user(dest_node=dest_node, user=user, record_index=params.get("record_index", 0))
            return {"command": command, "affected": affected}

        if command == "list_marks":
            marks, count = client.list_marks(
                dest_node=dest_node,
                start_position=params.get("start_position", 0),
                records_to_list=params.get("records_to_list", 10),
            )
            return {"command": command, "count": count, "marks": [_serialize_mark(item) for item in marks]}

    raise ValidationError({"command": "Comando no soportado."})

"""Recálculo local de la lista blanca general.

Reutiliza la lógica de ``MSSQLAccessCheckService`` (reimplementación read-only de
``CP_SCA_RegistrarAcceso``) pero forzando la conexión dedicada de xSys, porque el
``_connection_string`` original no setea ``Encrypt=no`` y fallaría contra el 49331.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from access_control.services import MSSQLAccessCheckService

from .mssql import connect as xsys_connect


class XsysAccessCheckService(MSSQLAccessCheckService):
    """`MSSQLAccessCheckService` conectado por la config MSSQL_XSYS (Encrypt=no)."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config or getattr(settings, "MSSQL_XSYS", {}))

    def _connect(self):  # type: ignore[override]
        return xsys_connect(self.config)


def persist_whitelist(id_cliente: int, res: dict[str, Any]):
    """Upsert de la decisión de habilitación en ``XsysWhitelist`` (escritura local)."""
    from django.utils import timezone

    from xsys.models import XsysWhitelist

    now = timezone.now()
    obj, _ = XsysWhitelist.objects.update_or_create(
        id_cliente=id_cliente,
        defaults={
            "habilitado": res["habilitado"],
            "motivo_code": res.get("motivo_code"),
            "motivo": (res.get("motivo") or "")[:120],
            "detalle": (res.get("detalle") or "")[:120],
            "id_acceso": res.get("id_acceso"),
            "fecha_calculo": now,
            "synced_at": now,
        },
    )
    return obj


def whitelist_params() -> tuple[int, int | None]:
    cfg = getattr(settings, "MSSQL_XSYS", {})
    return cfg.get("WHITELIST_ACCESO", 22), cfg.get("WHITELIST_CONTROLADOR")


def compute_habilitacion(
    id_cliente: int,
    *,
    service: MSSQLAccessCheckService | None = None,
    id_acceso: int | None = None,
    id_controlador: int | None = None,
) -> dict[str, Any]:
    """Devuelve el resultado de habilitación de un socio para el acceso general.

    Retorna un dict con: ``habilitado`` (bool), ``motivo_code`` (int|None),
    ``motivo`` (str), ``detalle`` (str), ``id_acceso`` (int|None).
    """

    default_acceso, default_controlador = whitelist_params()
    if id_acceso is None:
        id_acceso = default_acceso
    if id_controlador is None:
        id_controlador = default_controlador
    service = service or XsysAccessCheckService()

    result = service.check_access(
        identifier_type="id_cliente",
        identifier_value=str(id_cliente),
        id_acceso=id_acceso,
        id_controlador=id_controlador,
    )

    if not result.get("found"):
        return {
            "habilitado": False,
            "motivo_code": None,
            "motivo": "no_encontrado",
            "detalle": "",
            "id_acceso": result.get("id_acceso", id_acceso),
        }

    return {
        "habilitado": bool(result.get("can_enter")),
        "motivo_code": result.get("motivo_code"),
        "motivo": result.get("motivo", ""),
        "detalle": result.get("detalle", ""),
        "id_acceso": result.get("id_acceso", id_acceso),
    }

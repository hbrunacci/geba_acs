"""Resolución de acceso local con re-verificación online opcional.

Punto de entrada interno: ``resolver_acceso(...)``. Resuelve si un socio puede
ingresar (por el momento SIN distinción de puerta) leyendo el espejo LOCAL
(``XsysWhitelist``). Si el resultado local es negativo y ``verificar_online`` está
activo, re-consulta en vivo la base xSys para ver si el parámetro que lo invalidó
cambió; si cambió, actualiza el espejo local (write-through) y devuelve el nuevo
resultado. La re-verificación online es best-effort: si xSys no está accesible,
se devuelve el resultado local con la nota correspondiente.

Otros recursos de la app deben importar y usar ``resolver_acceso`` directamente.
"""

from __future__ import annotations

import logging
from typing import Any

from xsys.models import XsysSocio, XsysWhitelist

from .mssql import XsysConnectionError
from .whitelist import compute_habilitacion, persist_whitelist

logger = logging.getLogger(__name__)


def resolver_socio(
    *, id_cliente=None, doc=None, credencial=None
) -> XsysSocio | None:
    """Resuelve el socio en el espejo local por id / documento / credencial."""
    if id_cliente:
        return XsysSocio.objects.filter(pk=id_cliente).first()
    if doc:
        return (
            XsysSocio.objects.filter(doc_nro=doc)
            .order_by("-activo", "-ult_cuota_paga")
            .first()
        )
    if credencial:
        cred = str(credencial).strip().upper()
        return (
            XsysSocio.objects.filter(credencial_nro__iexact=cred)
            .order_by("-activo", "-ult_cuota_paga")
            .first()
        )
    return None


def resolver_acceso(
    *,
    id_cliente=None,
    doc=None,
    credencial=None,
    verificar_online: bool = True,
) -> dict[str, Any]:
    """Determina si un socio puede ingresar (sin distinción de puerta).

    Devuelve un dict con al menos: ``found``, ``puede_ingresar``, ``origen``
    ("local" | "xsys_reverificado"), ``motivo``, ``motivo_code``, ``detalle``,
    ``id_cliente``. Cuando hubo re-verificación online agrega ``reverificacion``
    con ``realizada`` / ``disponible`` / ``cambio`` / ``motivo_previo`` / ``error``.
    """

    if not any([id_cliente, doc, credencial]):
        raise ValueError("Debe indicar id_cliente, doc o credencial.")

    socio = resolver_socio(id_cliente=id_cliente, doc=doc, credencial=credencial)
    if socio is None:
        return {
            "found": False,
            "id_cliente": None,
            "puede_ingresar": False,
            "origen": "local",
            "motivo_code": None,
            "motivo": "socio_no_encontrado",
            "detalle": "",
        }

    wl = XsysWhitelist.objects.filter(id_cliente=socio.id_cliente).first()
    local_ok = bool(wl and wl.habilitado)
    motivo_local = wl.motivo if wl else "sin_evaluar"

    result: dict[str, Any] = {
        "found": True,
        "id_cliente": socio.id_cliente,
        "razon_social": (f"{socio.apellido}, {socio.nombre}".strip(", ") or socio.razon_social),
        "puede_ingresar": local_ok,
        "origen": "local",
        "motivo_code": wl.motivo_code if wl else None,
        "motivo": motivo_local,
        "detalle": wl.detalle if wl else "",
        "fecha_calculo_local": wl.fecha_calculo if wl else None,
    }

    # Si local es positivo, o no se pide verificación online, se devuelve local.
    if local_ok or not verificar_online:
        return result

    # Negativo -> re-verificar en xSys si el parámetro que lo invalidó cambió.
    reverif: dict[str, Any] = {
        "realizada": True,
        "disponible": False,
        "cambio": False,
        "motivo_previo": motivo_local,
        "error": None,
    }
    try:
        res = compute_habilitacion(socio.id_cliente)
        reverif["disponible"] = True
        cambio = (res["habilitado"] != local_ok) or (res["motivo"] != motivo_local)
        reverif["cambio"] = cambio
        # Write-through: actualizar el espejo local con el estado fresco.
        persist_whitelist(socio.id_cliente, res)
        result.update(
            {
                "puede_ingresar": bool(res["habilitado"]),
                "origen": "xsys_reverificado",
                "motivo_code": res["motivo_code"],
                "motivo": res["motivo"],
                "detalle": res["detalle"],
            }
        )
    except XsysConnectionError as exc:
        reverif["error"] = str(exc)
    except Exception as exc:  # pragma: no cover - errores de datos/validación online
        logger.warning("re-verificación online falló para cliente %s: %s", socio.id_cliente, exc)
        reverif["error"] = str(exc)

    result["reverificacion"] = reverif
    return result

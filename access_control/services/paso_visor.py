"""Copia los pasos del molinete que decide geba_acs al visor de puerta.

El visor (``/xsys/puerta/``) lee los movimientos del espejo local de ``CD_ES``
(``ExternalAccessLogEntry``) por ``id_controlador``. El molinete que maneja
``intelektron_online`` NO escribe en xSys, así que sus pasos no llegan por esa
vía: se graban acá, en el mismo espejo, con un ``external_id`` propio que no
choca con los ``Id_ES`` de Clever (van por los 9 millones; estos arrancan en
10^12) y que queda por encima del ``Max(external_id)`` con el que el visor
detecta que hay algo nuevo. El espejo se purga a los 7 días como el resto; el
registro permanente es ``acs_paso_molinete`` y el historial del socio.
"""

from __future__ import annotations

from access_control.models.models import ExternalAccessLogEntry
from access_control.services import historial_socio

VISOR_ID_BASE = 10**12
ORIGEN_VISOR = "ACS"
TIPO_REGISTRO_VISOR = "geba_acs"


def fila_de_paso(paso, id_controlador: int | None = None, id_acceso: int | None = None) -> ExternalAccessLogEntry:
    """``PasoMolinete`` -> ``ExternalAccessLogEntry`` (sin guardar)."""
    return ExternalAccessLogEntry(
        external_id=VISOR_ID_BASE + int(paso.id),
        tipo=(paso.tipo or "E")[:4],
        origen=ORIGEN_VISOR,
        id_tarjeta=(paso.tag or "")[:64],
        id_cliente=paso.id_cliente or None,
        fecha=paso.fecha,
        resultado="S" if paso.accionado else "E",
        id_controlador=id_controlador or paso.id_controlador,
        id_acceso=id_acceso or paso.id_acceso,
        observacion=(paso.mensaje or "")[:255],
        tipo_registro=TIPO_REGISTRO_VISOR,
        id_cd_motivo=paso.id_motivo,
        flag_permite_paso="",
    )


def espejar_en_visor(paso, id_controlador: int | None = None, id_acceso: int | None = None) -> ExternalAccessLogEntry:
    """Graba (o actualiza) el paso en el espejo del visor y en el historial del socio."""
    obj = fila_de_paso(paso, id_controlador, id_acceso)
    campos = {
        f.name: getattr(obj, f.name)
        for f in ExternalAccessLogEntry._meta.fields
        if f.name not in ("id", "external_id", "synced_at", "conflicto_molinete")
    }
    obj, _ = ExternalAccessLogEntry.objects.update_or_create(external_id=obj.external_id, defaults=campos)
    historial_socio.registrar_movimientos([obj])
    return obj

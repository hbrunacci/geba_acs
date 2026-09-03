"""Alta de filas en el historial permanente de accesos por socio (``SocioAcceso``).

Se llama desde las DOS ingestas, porque el socio entra por dos caminos:

- ``xsys.services.sync.sync_movements`` — lo que xSys registra en ``CD_ES``
  (credencial, DNI, QR dinámico, barreras).
- ``access_control.services.biostar_events`` — lo que registran los faciales.

Es best-effort en los dos casos: si acá se rompe algo, el movimiento se guarda
igual en su espejo. Perder una fila del historial es un problema menor; perder
el movimiento, no.

Los nombres de puerta y molinete se resuelven ACÁ, en la ingesta, y quedan
guardados como texto. Cuesta lo mismo que resolverlos al mostrar y tiene una
ventaja: si mañana se rearma la puerta o se renombra un molinete, el historial
sigue diciendo por dónde pasó la persona ese día.
"""

from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

# Los faciales no muestran un motivo de xSys: conceden o deniegan.
MENSAJE_FACIAL_OK = "Acceso Concedido"
MENSAJE_FACIAL_NO = "Acceso Denegado"


def _mapa_puertas() -> dict:
    """``door_id -> nombre de la puerta`` de la configuración local."""
    from institutions.models import AccessDoor

    return dict(AccessDoor.objects.values_list("id", "name"))


def _mapa_accesos() -> dict:
    """``Id_Acceso -> descripción`` del espejo de ``CD_Accesos``.

    Es el respaldo para los movimientos que caen en un controlador que todavía
    no está asignado a ninguna puerta armada acá.
    """
    from xsys.models import XsysAcceso

    return dict(XsysAcceso.objects.values_list("id_acceso", "descripcion"))


def _mapa_controladores() -> dict:
    """``Id_Controlador -> descripción`` del espejo de ``CD_Controladores``.

    La mitad de las puertas del club todavía no está armada como molinetes acá
    (Acceso CS, Newbery, Aldao Mitre…). Para ésas el nombre que sabe xSys
    —"BICI-Ombues", "Aldao_Mitre_Mol1 (Mostrador)"— le dice algo a la oficina de
    Socios; un "Ctrl 65" no.
    """
    from xsys.models import XsysControlador

    return dict(XsysControlador.objects.values_list("id_controlador", "descripcion"))


def _mapa_motivos() -> dict:
    from xsys.models import XsysMotivo

    return {m.id_cd_motivo: m.mensaje_pantalla for m in XsysMotivo.objects.all()}


def _armar_contexto() -> dict:
    """Los índices que hacen falta para ubicar y rotular un lote de eventos."""
    from access_control.services import paso_pendiente as pp

    return {
        "molinetes": pp.mapa_molinetes(),
        "puertas": _mapa_puertas(),
        "accesos": _mapa_accesos(),
        "controladores": _mapa_controladores(),
        "motivos": _mapa_motivos(),
    }


# El poller de BioStar persiste de a UN evento, así que sin esto cada facial que
# pasa costaría cuatro consultas de catálogo. Son tablas chicas que cambian cuando
# alguien rearma una puerta o xSys agrega un motivo: un minuto de desfasaje no
# cambia nada, y el nombre del molinete se puede corregir después con el backfill.
_CACHE_SEGUNDOS = 60
_cache: dict = {"armado_en": None, "ctx": None}


def _contexto() -> dict:
    ahora = timezone.now()
    armado = _cache["armado_en"]
    if armado is None or (ahora - armado).total_seconds() > _CACHE_SEGUNDOS:
        _cache["ctx"] = _armar_contexto()
        _cache["armado_en"] = ahora
    return _cache["ctx"]


def contexto() -> dict:
    """Los catálogos, para quien procesa un lote largo y no quiere releerlos.

    El backfill camina cientos de miles de filas: le conviene armarlos una vez y
    pasarlos, en vez de depender de una caché que expira a mitad de camino.
    """
    return _contexto()


def invalidar_cache() -> None:
    """Fuerza a releer los catálogos en la próxima ingesta (lo usan los tests)."""
    _cache["armado_en"] = None
    _cache["ctx"] = None


def _ubicar(ctx: dict, *, id_controlador=None, device_id=None, id_acceso=None,
            device_name="") -> tuple[str, str]:
    """Devuelve ``(puerta, molinete)`` para un evento.

    Si el equipo está asignado a un molinete armado, manda ese nombre: es el que
    usa el visor y el que dice la gente. Si no lo está, ``resolver_molinete``
    devuelve un relleno ("Ctrl 65", "Facial 4242") y se prefiere el nombre real
    del equipo, que sí ubica a quien lee el historial.
    """
    from access_control.services import paso_pendiente as pp

    molinete = pp.resolver_molinete(
        ctx["molinetes"], id_controlador=id_controlador, device_id=device_id)
    armado = molinete.get("door_id") is not None

    puerta = ctx["puertas"].get(molinete.get("door_id")) or ""
    if not puerta and id_acceso is not None:
        puerta = ctx["accesos"].get(id_acceso) or ""

    nombre = molinete.get("nombre") or ""
    if not armado:
        propio = (ctx["controladores"].get(id_controlador) or "").strip() \
            if id_controlador is not None else (device_name or "").strip()
        nombre = propio or nombre
    return (puerta or "")[:80], nombre[:60]


def _guardar(filas) -> int:
    """Inserta ignorando los que ya estaban (``referencia`` es única)."""
    from access_control.models import SocioAcceso

    if not filas:
        return 0
    SocioAcceso.objects.bulk_create(filas, ignore_conflicts=True, batch_size=500)
    return len(filas)


def fila_de_movimiento(ev, ctx: dict):
    """``ExternalAccessLogEntry`` -> ``SocioAcceso`` (sin guardar)."""
    from access_control.models import SocioAcceso

    mensaje = ctx["motivos"].get(ev.id_cd_motivo) or (ev.observacion or "").strip()
    puerta, molinete = _ubicar(
        ctx, id_controlador=ev.id_controlador, id_acceso=ev.id_acceso)
    return SocioAcceso(
        id_cliente=ev.id_cliente,
        fecha=ev.fecha,
        origen=SocioAcceso.ORIGEN_CREDENCIAL,
        referencia=f"cdes:{ev.external_id}",
        permitido=(ev.resultado == "S"),
        resultado=(ev.resultado or "")[:4],
        mensaje=(mensaje or "")[:255],
        motivo_code=ev.id_cd_motivo,
        detalle=(ev.observacion or "").strip()[:255],
        puerta=puerta,
        molinete=molinete,
        id_acceso=ev.id_acceso,
        id_controlador=ev.id_controlador,
        conflicto_molinete=(ev.conflicto_molinete or "")[:60],
        creado_at=timezone.now(),
    )


def fila_de_facial(ev, ctx: dict):
    """``BiostarAccessEvent`` -> ``SocioAcceso`` (sin guardar)."""
    from access_control.models import SocioAcceso

    puerta, molinete = _ubicar(ctx, device_id=ev.device_id,
                               device_name=ev.device_name or "")
    return SocioAcceso(
        id_cliente=ev.id_cliente,
        fecha=ev.fecha,
        origen=SocioAcceso.ORIGEN_FACIAL,
        referencia=f"biostar:{ev.biostar_id}"[:48],
        permitido=bool(ev.permitido),
        resultado="",
        mensaje=MENSAJE_FACIAL_OK if ev.permitido else MENSAJE_FACIAL_NO,
        motivo_code=ev.event_code,
        detalle=(ev.event_name or "")[:255],
        puerta=puerta,
        molinete=molinete,
        device_id=ev.device_id,
        conflicto_molinete=(ev.conflicto_molinete or "")[:60],
        creado_at=timezone.now(),
    )


def registrar_movimientos(objs, ctx: dict | None = None) -> int:
    """Registra un lote de ``ExternalAccessLogEntry``. Nunca levanta excepción.

    Se saltean los eventos sin socio identificado (``Id_Cliente`` 0 o nulo): son
    lecturas de algo que xSys no reconoció, y este historial es *por socio*. Ese
    ruido sigue estando en ``ExternalAccessLogEntry`` y en el visor.
    """
    try:
        conocidos = [o for o in objs if o.id_cliente and o.fecha]
        if not conocidos:
            return 0
        ctx = ctx or _contexto()
        return _guardar([fila_de_movimiento(o, ctx) for o in conocidos])
    except Exception as exc:  # pragma: no cover - nunca romper la ingesta
        logger.warning("historial_socio: no se pudo registrar el lote de CD_ES: %s", exc)
        return 0


def registrar_faciales(objs, ctx: dict | None = None) -> int:
    """Registra un lote de ``BiostarAccessEvent``. Nunca levanta excepción."""
    try:
        conocidos = [o for o in objs if o.id_cliente and o.fecha]
        if not conocidos:
            return 0
        ctx = ctx or _contexto()
        return _guardar([fila_de_facial(o, ctx) for o in conocidos])
    except Exception as exc:  # pragma: no cover - nunca romper la ingesta
        logger.warning("historial_socio: no se pudo registrar el lote facial: %s", exc)
        return 0


def registrar_facial(obj) -> int:
    """Un solo evento facial: la ingesta de BioStar persiste de a uno."""
    return registrar_faciales([obj])

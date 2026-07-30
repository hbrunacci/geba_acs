"""Clasificación e ingesta de eventos de acceso de BioStar.

BioStar loguea de todo (altas/bajas de usuarios, enrolamiento, tamper, etc.).
Para el visor solo interesan los eventos de ACCESO: identificación/verificación
exitosa (concedido), fallida o denegada. Se clasifican por el NOMBRE del tipo de
evento (traído del catálogo ``/api/event_types``), no por código, para no
depender de números mágicos.
"""

from __future__ import annotations

from datetime import datetime, timezone as _tz

# Prefijos de nombre de tipo de evento (BioStar 2 New Local API).
_GRANTED_PREFIXES = ("VERIFY_SUCCESS", "IDENTIFY_SUCCESS")
_DURESS_PREFIXES = ("VERIFY_DURESS", "IDENTIFY_DURESS")  # concedido bajo coacción
_DENIED_PREFIXES = ("VERIFY_FAIL", "IDENTIFY_FAIL", "AUTH_FAILED", "ACCESS_DENIED")


def clasificar_evento(event_name: str | None) -> tuple[bool, bool]:
    """Devuelve (es_acceso, permitido) según el nombre del tipo de evento.

    - es_acceso=False → no es un evento de acceso (ruido: update/delete/enroll…),
      no se persiste.
    - permitido: True si concedido (success/duress), False si denegado/fallido.
    """
    name = (event_name or "").upper()
    if not name:
        return (False, False)
    if name.startswith(_GRANTED_PREFIXES) or name.startswith(_DURESS_PREFIXES):
        return (True, True)
    if name.startswith(_DENIED_PREFIXES):
        return (True, False)
    return (False, False)


def parse_server_datetime(value: str | None):
    """Parsea el ``server_datetime`` de BioStar (UTC real, ej. '2026-07-24T14:52:57.00Z').

    Devuelve un datetime timezone-aware en UTC, o None si no se puede parsear.
    El campo ``datetime`` del evento NO se usa: viene desfasado por la zona
    configurada en el equipo. Django lo mostrará en hora local (TIME_ZONE).
    """
    if not value:
        return None
    txt = value.strip().replace("Z", "").replace("z", "")
    # BioStar manda fracciones de 2 dígitos ('.00'); datetime espera 3 o 6.
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(txt, fmt).replace(tzinfo=_tz.utc)
        except ValueError:
            continue
    return None


def _int_or_none(value):
    try:
        if value in (None, "", "0"):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _store_event(event_types: dict, e: dict) -> bool:
    """Persiste UN evento si es de acceso. Devuelve True si creó una fila."""
    from access_control.models import BiostarAccessEvent  # import diferido

    code = (e.get("event_type_id") or {}).get("code")
    name = event_types.get(str(code), "")
    es_acceso, permitido = clasificar_evento(name)
    if not es_acceso:
        return False
    bid = str(e.get("id") or "")
    if not bid:
        return False
    fecha = parse_server_datetime(e.get("server_datetime")) or parse_server_datetime(e.get("datetime"))
    if fecha is None:
        return False
    dev = e.get("device_id") or {}
    _, created = BiostarAccessEvent.objects.get_or_create(
        biostar_id=bid,
        defaults={
            "device_id": _int_or_none(dev.get("id")) or 0,
            "device_name": dev.get("name") or "",
            "id_cliente": _int_or_none((e.get("user_id") or {}).get("user_id")),
            "fecha": fecha,
            "event_code": _int_or_none(code),
            "event_name": name,
            "permitido": permitido,
        },
    )
    return created


def current_max_event_id(client) -> int | None:
    """Id del evento más nuevo en BioStar (o None si no hay/error de forma).

    Sirve para detectar que BioStar reinició su secuencia de ids (limpieza o
    restauración de su base): si el high-water local quedó POR ENCIMA de este
    máximo, el poll incremental (``id > high-water``) no matchea nada más.
    """
    rows = client.events_search(limit=1, order_column="id", descending=True)
    if not rows:
        return None
    return _int_or_none(rows[0].get("id"))


def ingest_new_events(client, event_types: dict, last_id: int, *, limit: int = 200, max_pages: int = 50) -> tuple[int, int]:
    """Ingesta INCREMENTAL: trae solo eventos con ``id`` > ``last_id`` (todos los
    equipos), en orden de ``id`` ascendente, y persiste los de acceso.

    Devuelve ``(nuevos, nuevo_last_id)``. ``nuevo_last_id`` avanza con el id de
    TODOS los eventos vistos (incluido el ruido de sync), para no re-escanearlos.
    Pagina por el propio high-water (id > last) hasta agotar o ``max_pages``.
    """
    nuevos = 0
    cur = int(last_id or 0)
    for _ in range(max_pages):
        rows = client.events_search(after_id=cur, limit=limit, order_column="id", descending=False)
        if not rows:
            break
        # Garantizar orden por id ascendente (no depender solo del server).
        rows.sort(key=lambda r: _int_or_none(r.get("id")) or 0)
        for e in rows:
            eid = _int_or_none(e.get("id"))
            if eid is None or eid <= cur:
                continue
            if _store_event(event_types, e):
                nuevos += 1
            cur = eid
        if len(rows) < limit:
            break
    return nuevos, cur


def ingest_backfill(client, event_types: dict, *, limit: int = 1000) -> tuple[int, int]:
    """Arranque en frío: trae los ``limit`` eventos más nuevos (por id desc) y
    persiste los de acceso. Evita traer TODO el histórico la primera vez.
    Devuelve ``(nuevos, max_id)`` para sembrar el high-water."""
    rows = client.events_search(limit=limit, order_column="id", descending=True)
    nuevos = 0
    mx = 0
    for e in rows:
        eid = _int_or_none(e.get("id")) or 0
        if eid > mx:
            mx = eid
        if _store_event(event_types, e):
            nuevos += 1
    return nuevos, mx


def purge_old(days: int) -> int:
    """Borra eventos faciales fuera de la ventana de retención. Devuelve borrados."""
    from datetime import timedelta

    from django.utils import timezone as _dj_tz

    from access_control.models import BiostarAccessEvent

    limite = _dj_tz.now() - timedelta(days=days)
    borrados, _ = BiostarAccessEvent.objects.filter(fecha__lt=limite).delete()
    return borrados

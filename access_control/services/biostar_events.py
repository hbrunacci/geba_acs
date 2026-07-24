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


def ingest_device_events(client, event_types: dict, device_id, device_name: str = "", *, limit: int = 200) -> int:
    """Trae los eventos de un facial y persiste solo los de ACCESO. Devuelve nuevos guardados."""
    from access_control.models import BiostarAccessEvent  # import diferido

    rows = client.events_search(device_id=device_id, limit=limit)
    nuevos = 0
    for e in rows:
        code = (e.get("event_type_id") or {}).get("code")
        name = event_types.get(str(code), "")
        es_acceso, permitido = clasificar_evento(name)
        if not es_acceso:
            continue
        bid = str(e.get("id") or "")
        if not bid:
            continue
        fecha = parse_server_datetime(e.get("server_datetime")) or parse_server_datetime(e.get("datetime"))
        if fecha is None:
            continue
        dev = e.get("device_id") or {}
        _, created = BiostarAccessEvent.objects.get_or_create(
            biostar_id=bid,
            defaults={
                "device_id": _int_or_none(dev.get("id")) or _int_or_none(device_id) or 0,
                "device_name": dev.get("name") or device_name or "",
                "id_cliente": _int_or_none((e.get("user_id") or {}).get("user_id")),
                "fecha": fecha,
                "event_code": _int_or_none(code),
                "event_name": name,
                "permitido": permitido,
            },
        )
        if created:
            nuevos += 1
    return nuevos


def purge_old(days: int) -> int:
    """Borra eventos faciales fuera de la ventana de retención. Devuelve borrados."""
    from datetime import timedelta

    from django.utils import timezone as _dj_tz

    from access_control.models import BiostarAccessEvent

    limite = _dj_tz.now() - timedelta(days=days)
    borrados, _ = BiostarAccessEvent.objects.filter(fecha__lt=limite).delete()
    return borrados

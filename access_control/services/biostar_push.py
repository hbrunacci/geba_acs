"""Push incremental de altas hacia los lectores faciales de BioStar.

Tras cada sync de novedades de xSys, propaga a los equipos BioStar SOLO las
ALTAS de los socios afectados: los que quedaron ``habilitado=True`` en la
whitelist Y están enrolados en BioStar (existen en el espejo ``BioStarUser``,
``id_cliente`` == ``user_id``). Exporta a TODOS los devices.

Decisiones (2026-07-29):
- Solo altas: ``habilitado=False`` NO remueve del lector (para no arriesgar el
  acceso físico). Queda como pendiente futuro.
- Filtra por enrolados (espejo ``BioStarUser``) para no mandar ids inexistentes.

Es best-effort: cualquier fallo se loguea y NO rompe el sync xSys->local. El modo
se controla con la env ``BIOSTAR_PUSH_MODE`` = ``off`` | ``dryrun`` | ``on``
(default ``dryrun``: loguea a quién agregaría, sin ejecutar).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Sequence

logger = logging.getLogger(__name__)

PUSH_MODE_ENV = "BIOSTAR_PUSH_MODE"
DEFAULT_MODE = "dryrun"
VALID_MODES = ("off", "dryrun", "on")


def get_push_mode() -> str:
    mode = (os.getenv(PUSH_MODE_ENV, DEFAULT_MODE) or DEFAULT_MODE).strip().lower()
    return mode if mode in VALID_MODES else DEFAULT_MODE


def enabled_enrolled_ids(affected_ids: Sequence[int]) -> list[int]:
    """id_cliente afectados que están habilitados (whitelist) Y enrolados en BioStar."""
    from access_control.models import BioStarUser
    from xsys.models import XsysWhitelist

    ids = [int(i) for i in affected_ids if i is not None]
    if not ids:
        return []
    enabled = set(
        XsysWhitelist.objects.filter(id_cliente__in=ids, habilitado=True)
        .values_list("id_cliente", flat=True)
    )
    if not enabled:
        return []
    enrolled = set(
        BioStarUser.objects.filter(user_id__in=enabled, is_active=True)
        .values_list("user_id", flat=True)
    )
    return sorted(enabled & enrolled)


def push_enabled_affected(
    affected_ids: Sequence[int],
    *,
    mode: str | None = None,
    batch_size: int = 100,
    max_per_run: int = 1000,
) -> dict[str, Any]:
    """Empuja las altas de los socios afectados a todos los devices BioStar.

    No lanza excepciones: devuelve un dict con el resumen (para loguear en el sync).
    """
    mode = (mode or get_push_mode()).strip().lower()
    result: dict[str, Any] = {"mode": mode, "candidatos": 0, "enviados": 0, "errores": 0}

    if mode == "off":
        result["skipped"] = True
        return result

    try:
        to_push = enabled_enrolled_ids(affected_ids)
    except Exception as exc:  # pragma: no cover - defensivo
        logger.warning("biostar_push: fallo resolviendo candidatos: %s", exc)
        result["error"] = str(exc)[:200]
        return result

    result["candidatos"] = len(to_push)
    if not to_push:
        logger.info("biostar_push[%s]: sin socios habilitados+enrolados para empujar", mode)
        return result

    if len(to_push) > max_per_run:
        logger.warning(
            "biostar_push[%s]: %s candidatos > tope %s; se recorta esta corrida",
            mode, len(to_push), max_per_run,
        )
        to_push = to_push[:max_per_run]
        result["capado"] = True

    if mode == "dryrun":
        muestra = to_push[:20]
        logger.info(
            "biostar_push[DRYRUN]: agregaría %s socios a todos los devices. Muestra: %s%s",
            len(to_push), muestra, " ..." if len(to_push) > len(muestra) else "",
        )
        result["dryrun_ids"] = to_push
        return result

    # mode == "on": ejecuta las altas reales, en lotes, best-effort.
    try:
        from access_control.services.biostar2_client import BioStar2Client

        client = BioStar2Client.from_db_and_env()
    except Exception as exc:
        logger.warning("biostar_push[on]: no se pudo crear el cliente BioStar: %s", exc)
        result["error"] = str(exc)[:200]
        return result

    for i in range(0, len(to_push), batch_size):
        chunk = to_push[i : i + batch_size]
        try:
            client.add_user_to_all_devices(chunk)
            result["enviados"] += len(chunk)
        except Exception as exc:  # pragma: no cover - depende de red/BioStar
            result["errores"] += len(chunk)
            logger.warning(
                "biostar_push[on]: fallo lote %s..%s (%s): %s",
                chunk[0], chunk[-1], len(chunk), exc,
            )

    logger.info(
        "biostar_push[on]: candidatos=%s enviados=%s errores=%s",
        result["candidatos"], result["enviados"], result["errores"],
    )
    return result

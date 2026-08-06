"""Deshabilitar en BioStar a los socios enrolados que NO pueden ingresar,
manteniendo su rostro (reconocer pero denegar), y rehabilitar a los que vuelven.

Es el complemento de ``biostar_push`` (que solo hace ALTAS): aquí se cierra el
otro lado, SIN borrar el enrolamiento facial. Un socio moroso queda enrolado
pero con el acceso vencido/deshabilitado → el equipo lo reconoce (queda registro
de que ESE socio intentó pasar) pero no le abre.

Fuente de verdad de "puede entrar" = ``XsysWhitelist.habilitado`` (que a su vez
delega a las funciones de cuota de xSys). Enrolados = espejo ``BioStarUser``.

Best-effort: cualquier fallo se loguea y NO rompe el sync. Gobernado por env:
- ``BIOSTAR_DISABLE_MODE`` = off | dryrun | on   (default dryrun)
- ``BIOSTAR_DISABLE_METHOD`` = expiry | disabled  (default expiry)

**Riesgo (acceso físico):** en modo ``on`` esto NIEGA el paso a quien la whitelist
marque como no-habilitado. Un error en el cálculo de cuota dejaría gente afuera.
Por eso el default es ``dryrun`` (loguea a quién tocaría, sin ejecutar). Validar
con la prueba controlada (un socio de prueba) que: (1) el rostro se preserva tras
el PUT, y (2) el equipo genera el evento "reconocido + denegado", ANTES de ``on``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Sequence

logger = logging.getLogger(__name__)

DISABLE_MODE_ENV = "BIOSTAR_DISABLE_MODE"
DEFAULT_MODE = "dryrun"
VALID_MODES = ("off", "dryrun", "on")

DISABLE_METHOD_ENV = "BIOSTAR_DISABLE_METHOD"
DEFAULT_METHOD = "expiry"
VALID_METHODS = ("expiry", "disabled")


def get_disable_mode() -> str:
    m = (os.getenv(DISABLE_MODE_ENV, DEFAULT_MODE) or DEFAULT_MODE).strip().lower()
    return m if m in VALID_MODES else DEFAULT_MODE


def get_disable_method() -> str:
    m = (os.getenv(DISABLE_METHOD_ENV, DEFAULT_METHOD) or DEFAULT_METHOD).strip().lower()
    return m if m in VALID_METHODS else DEFAULT_METHOD


def resolve_state_targets(ids: Sequence[int] | None = None) -> dict[str, list[int]]:
    """Separa los enrolados en BioStar según deban quedar habilitados o no.

    Si ``ids`` es None, considera TODOS los enrolados (backfill). Si viene una
    lista (incremental), solo esos.

    Devuelve {to_disable: [...], to_enable: [...]} — ambos sobre socios ENROLADOS
    (existen en BioStar), porque no tiene sentido tocar el estado de quien no está.
    """
    from access_control.models import BioStarUser
    from xsys.models import XsysWhitelist

    enrolled_q = BioStarUser.objects.filter(is_active=True)
    if ids is not None:
        clean = [int(i) for i in ids if i is not None]
        if not clean:
            return {"to_disable": [], "to_enable": []}
        enrolled_q = enrolled_q.filter(user_id__in=clean)
    enrolled = set(enrolled_q.values_list("user_id", flat=True))
    if not enrolled:
        return {"to_disable": [], "to_enable": []}

    habilitados = set(
        XsysWhitelist.objects.filter(id_cliente__in=enrolled, habilitado=True)
        .values_list("id_cliente", flat=True)
    )
    to_enable = sorted(enrolled & habilitados)
    to_disable = sorted(enrolled - habilitados)  # enrolado y NO habilitado (o sin fila)
    return {"to_disable": to_disable, "to_enable": to_enable}


def _apply(client, ids: list[int], *, enabled: bool, method: str, result: dict, key_ok: str) -> None:
    for cid in ids:
        try:
            resp = client.set_user_access_state(cid, enabled=enabled, method=method)
            code = None
            try:
                code = str(resp.json().get("Response", {}).get("code"))
            except Exception:
                pass
            if getattr(resp, "status_code", None) == 200 and code == "0":
                result[key_ok] += 1
            else:
                result["errores"] += 1
                logger.warning("biostar_disable[on]: %s (enabled=%s) HTTP %s code %s",
                               cid, enabled, getattr(resp, "status_code", "?"), code)
        except Exception as exc:  # pragma: no cover - red/BioStar
            result["errores"] += 1
            logger.warning("biostar_disable[on]: %s (enabled=%s) falló: %s", cid, enabled, str(exc)[:120])


def push_access_state_affected(
    affected_ids: Sequence[int] | None,
    *,
    mode: str | None = None,
    method: str | None = None,
    reenable: bool = True,
    max_per_run: int = 2000,
) -> dict[str, Any]:
    """Sincroniza el estado (habilitado/denegado) de los socios enrolados afectados.

    ``affected_ids=None`` → backfill sobre todos los enrolados. ``reenable`` controla
    si además se rehabilita a los que están habilitados (útil en incremental para el
    que pagó y volvió; en backfill se puede desactivar para tocar solo a los morosos).
    No lanza excepciones: devuelve el resumen para loguear en el sync.
    """
    mode = (mode or get_disable_mode()).strip().lower()
    method = (method or get_disable_method()).strip().lower()
    result: dict[str, Any] = {
        "mode": mode, "method": method,
        "a_deshabilitar": 0, "a_rehabilitar": 0,
        "deshabilitados": 0, "rehabilitados": 0, "errores": 0,
    }

    if mode == "off":
        result["skipped"] = True
        return result

    try:
        targets = resolve_state_targets(affected_ids)
    except Exception as exc:  # pragma: no cover - defensivo
        logger.warning("biostar_disable: fallo resolviendo targets: %s", exc)
        result["error"] = str(exc)[:200]
        return result

    to_disable = targets["to_disable"][:max_per_run]
    to_enable = (targets["to_enable"][:max_per_run] if reenable else [])
    result["a_deshabilitar"] = len(to_disable)
    result["a_rehabilitar"] = len(to_enable)

    if not to_disable and not to_enable:
        return result

    if mode == "dryrun":
        logger.info(
            "biostar_disable[DRYRUN/%s]: deshabilitaría %s, rehabilitaría %s. "
            "Muestra deshab.: %s%s",
            method, len(to_disable), len(to_enable),
            to_disable[:20], " ..." if len(to_disable) > 20 else "",
        )
        result["dryrun_disable_ids"] = to_disable
        result["dryrun_enable_ids"] = to_enable
        return result

    # mode == "on"
    try:
        from access_control.services.biostar2_client import BioStar2Client

        client = BioStar2Client.from_db_and_env()
    except Exception as exc:
        logger.warning("biostar_disable[on]: no se pudo crear el cliente: %s", exc)
        result["error"] = str(exc)[:200]
        return result

    _apply(client, to_disable, enabled=False, method=method, result=result, key_ok="deshabilitados")
    _apply(client, to_enable, enabled=True, method=method, result=result, key_ok="rehabilitados")

    logger.info("biostar_disable[on]: %s", result)
    return result

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

MAX_DISABLE_ENV = "BIOSTAR_MAX_DISABLE_PER_RUN"
DEFAULT_MAX_DISABLE = 3000

DISABLE_METHOD_ENV = "BIOSTAR_DISABLE_METHOD"
DEFAULT_METHOD = "expiry"
VALID_METHODS = ("expiry", "disabled")


PROTECTED_IDS_ENV = "BIOSTAR_PROTECTED_USER_IDS"


def is_operator_account(payload: dict | None) -> bool:
    """¿Es una cuenta de operador de BioStar y no el enrolamiento de un socio?

    Hace falta porque los ids colisionan: el ``Administrator`` de BioStar tiene
    ``user_id=1`` y en xSys el ``Id_Cliente=1`` existe y es "CLIENTES VARIOS"
    (no habilitado). Filtrar sólo por "está en el padrón" NO lo protege: lo
    tomaría por un socio moroso y lo deshabilitaría.

    Los socios no tienen ``login_id`` ni permiso de operador; al 11-08-2026 el
    único usuario de los 16.445 que cumple esto es el Administrator, así que la
    regla también cubre a los operadores que se creen más adelante.
    """
    p = payload or {}
    if (p.get("login_id") or "").strip():
        return True
    perm = p.get("permission")
    if isinstance(perm, dict) and str(perm.get("id") or "0").strip() not in ("0", ""):
        return True
    return False


def biostar_permite(payload: dict | None) -> bool:
    """¿El estado actual en BioStar deja pasar a este usuario?

    BioStar devuelve los booleanos como strings ('true'/'false').
    """
    p = payload or {}

    def es_true(v) -> bool:
        return v is True or str(v).strip().lower() == "true"

    return not es_true(p.get("disabled")) and not es_true(p.get("expired"))


def protected_user_ids() -> set[int]:
    """Ids que nunca deben tocarse, por si hace falta forzar alguno desde el env."""
    raw = os.getenv(PROTECTED_IDS_ENV, "1") or ""
    out: set[int] = set()
    for parte in raw.replace(";", ",").split(","):
        parte = parte.strip()
        if parte.isdigit():
            out.add(int(parte))
    return out


def get_disable_mode() -> str:
    m = (os.getenv(DISABLE_MODE_ENV, DEFAULT_MODE) or DEFAULT_MODE).strip().lower()
    return m if m in VALID_MODES else DEFAULT_MODE


def max_disable_per_run() -> int:
    """Tope de denegaciones que una sola corrida puede propagar a los lectores.

    Válvula de seguridad: si un estado transitorio de xSys (un proceso nocturno a
    medio correr, una tabla vacía) hiciera caer la habilitación de media base, sin
    esto lo empujaríamos a los molinetes y dejaríamos a miles de socios afuera. Un
    corte de gracia mensual normal mueve ~1.200, así que el default deja pasar lo
    legítimo y frena lo catastrófico. 0 = sin tope.
    """
    try:
        return int(os.getenv(MAX_DISABLE_ENV, str(DEFAULT_MAX_DISABLE)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_DISABLE


def get_disable_method() -> str:
    m = (os.getenv(DISABLE_METHOD_ENV, DEFAULT_METHOD) or DEFAULT_METHOD).strip().lower()
    return m if m in VALID_METHODS else DEFAULT_METHOD


def resolve_state_targets(
    ids: Sequence[int] | None = None,
    *,
    only_divergent: bool = False,
) -> dict[str, list[int]]:
    """Separa los enrolados en BioStar según deban quedar habilitados o no.

    Si ``ids`` es None, considera TODOS los enrolados (backfill). Si viene una
    lista (incremental), solo esos.

    Con ``only_divergent`` se descartan además los que YA están como deben según
    el espejo (``disabled``/``expired``), y quedan sólo los realmente
    desincronizados. Eso hace la sincronización **convergente**: empujar "todos"
    pasa a ser barato y, sobre todo, repara los que quedaron mal por un PUT
    fallido. Empujando sólo a los que cambian, un fallo puntual no se reintenta
    nunca y ese socio queda desincronizado para siempre.

    Devuelve {to_disable: [...], to_enable: [...]} — ambos sobre socios ENROLADOS
    (existen en BioStar), porque no tiene sentido tocar el estado de quien no está.
    """
    from access_control.models import BioStarUser
    from xsys.models import XsysWhitelist

    enrolled_q = BioStarUser.objects.filter(is_active=True)
    if ids is not None:
        clean = [int(i) for i in ids if i is not None]
        if not clean:
            return {"to_disable": [], "to_enable": [], "ignorados": []}
        enrolled_q = enrolled_q.filter(user_id__in=clean)

    protegidos = protected_user_ids()
    enrolled = set()
    estado_actual: dict[int, bool] = {}
    for uid, payload in enrolled_q.values_list("user_id", "raw_payload"):
        if uid in protegidos or is_operator_account(payload):
            logger.info("biostar_disable: se protege la cuenta de operador %s", uid)
            continue
        enrolled.add(uid)
        estado_actual[uid] = biostar_permite(payload)
    if not enrolled:
        return {"to_disable": [], "to_enable": [], "ignorados": []}

    # SÓLO se tocan usuarios que son socios (tienen fila en la whitelist). En
    # BioStar conviven cuentas que no son socios — la primera es el propio
    # ``Administrator`` (user_id=1). Sin este filtro, "enrolados − habilitados"
    # los tomaba como morosos y el modo ``on`` habría deshabilitado la cuenta de
    # administración del sistema.
    conocidos = dict(
        XsysWhitelist.objects.filter(id_cliente__in=enrolled)
        .values_list("id_cliente", "habilitado")
    )
    ajenos = sorted(enrolled - set(conocidos))
    if ajenos:
        logger.info("biostar_disable: %s usuarios de BioStar no son socios, se ignoran: %s",
                    len(ajenos), ajenos[:20])

    if only_divergent:
        # Sólo los que hoy están al revés de lo que corresponde.
        to_enable = sorted(c for c, h in conocidos.items() if h and not estado_actual.get(c, True))
        to_disable = sorted(c for c, h in conocidos.items() if not h and estado_actual.get(c, True))
    else:
        to_enable = sorted(c for c, h in conocidos.items() if h)
        to_disable = sorted(c for c, h in conocidos.items() if not h)
    return {"to_disable": to_disable, "to_enable": to_enable, "ignorados": ajenos}


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
    only_divergent: bool = False,
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
        "deshabilitados": 0, "rehabilitados": 0, "errores": 0, "ignorados": 0,
    }

    if mode == "off":
        result["skipped"] = True
        return result

    try:
        targets = resolve_state_targets(affected_ids, only_divergent=only_divergent)
    except Exception as exc:  # pragma: no cover - defensivo
        logger.warning("biostar_disable: fallo resolviendo targets: %s", exc)
        result["error"] = str(exc)[:200]
        return result

    result["ignorados"] = len(targets.get("ignorados") or [])
    to_disable = targets["to_disable"][:max_per_run]
    to_enable = (targets["to_enable"][:max_per_run] if reenable else [])
    result["a_deshabilitar"] = len(to_disable)
    result["a_rehabilitar"] = len(to_enable)

    if not to_disable and not to_enable:
        return result

    tope = max_disable_per_run()
    if mode == "on" and tope and len(to_disable) > tope:
        # No se deniega a nadie: es más seguro dejar de más que dejar afuera a
        # miles por un dato transitorio. Queda para revisión manual.
        logger.error(
            "biostar_disable: ABORTADO — la corrida quiere denegar a %s socios, por encima del "
            "tope de %s (%s). No se tocó BioStar; revisar la whitelist y, si el número es "
            "correcto, correr el backfill a mano o subir el tope.",
            len(to_disable), tope, MAX_DISABLE_ENV,
        )
        result["abortado_por_tope"] = {"a_deshabilitar": len(to_disable), "tope": tope}
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

"""Enrolamiento facial en BioStar 2 desde la foto de xSys.

Reemplaza el enrolamiento que hacía CleverSoft (detenido). El rostro se carga con
``PUT /api/users/{id}`` (existe) o ``POST /api/users`` (no existe) usando
``credentials.visualFaces[{index:0, template_ex_picture: <base64 JPEG>}]``.

Hallazgo clave: si la foto es grande, BioStar devuelve ``500 code 1000 "Ran out of
stack space trying to match the regular expression"``. La imagen se redimensiona
(600 → 480 → 360 px de lado mayor) hasta que entra. No hay parámetro de calidad en
la API: el resize ES la palanca.
"""

from __future__ import annotations

import base64
import logging
import os
import re

logger = logging.getLogger(__name__)

# Grupo de acceso de socios (Cuota Social) — mismo que usa diag_facial.
ACCESS_GROUP_SOCIOS = 2
# Tamaños de reintento (lado mayor en px) ante rechazo por foto grande.
RESIZE_STEPS = (600, 480, 360)
# code de la Response de BioStar cuando revienta la pila del regex por foto grande.
STACK_CODE = "1000"

# Modo de la sincronización automática de rostros (hook del sync + backfill).
FACE_SYNC_MODE_ENV = "BIOSTAR_FACE_SYNC_MODE"
DEFAULT_FACE_MODE = "off"
VALID_FACE_MODES = ("off", "dryrun", "on")


def get_face_sync_mode() -> str:
    m = (os.getenv(FACE_SYNC_MODE_ENV, DEFAULT_FACE_MODE) or DEFAULT_FACE_MODE).strip().lower()
    return m if m in VALID_FACE_MODES else DEFAULT_FACE_MODE


def resize_steps() -> tuple[int, ...]:
    """Tamaños de resize; el mayor sale de BIOSTAR_FACE_MAX_SIDE (default 600)."""
    try:
        top = int(os.getenv("BIOSTAR_FACE_MAX_SIDE", "600"))
    except (TypeError, ValueError):
        top = 600
    steps = tuple(s for s in (top, 480, 360) if s <= top)
    return steps or (top,)


def limpiar_nombre(n: str) -> str:
    """Sanea el nombre para BioStar (sin puntos/comillas; solo letras+espacios; ≤48)."""
    n = (n or "").replace("'", " ").replace(".", " ")
    n = re.sub(r"[^0-9A-Za-zÀ-ſ ]", " ", n)
    return " ".join(n.split())[:48]


def _resp_code(resp) -> tuple[str | None, str | None]:
    try:
        r = resp.json().get("Response", {})
        return str(r.get("code")), r.get("message")
    except Exception:
        return None, (resp.text or "")[:200]


def _is_stack(code: str | None, msg: str | None) -> bool:
    return code == STACK_CODE or "stack space" in (msg or "").lower()


def enroll_one(
    client,
    *,
    id_cliente: int,
    jpeg_bytes: bytes,
    name: str = "",
    access_group_ids=(ACCESS_GROUP_SOCIOS,),
    exists: bool = True,
    sizes=None,
) -> dict:
    """Enrola/actualiza el rostro de un socio, redimensionando ante rechazo por tamaño.

    Devuelve {id_cliente, action: enrolled|created|failed, reason, maxside}.
    """
    from xsys.services.images import resize_for_face

    sizes = sizes or resize_steps()

    created = not exists
    exists_now = exists

    # 1) Si no existe, crear el cascarón SIN access_groups (el POST con grupos da 131085).
    if not exists_now:
        cr = client.create_user(id_cliente, name=limpiar_nombre(name))
        code, msg = _resp_code(cr)
        if cr.status_code == 200 and code == "0":
            exists_now = True
        elif code == "202":  # ya existía
            exists_now = True
            created = False
        else:
            return {"id_cliente": id_cliente, "action": "failed",
                    "reason": f"create HTTP {cr.status_code} code {code}: {msg}"}

    # 2) Asignar grupo + rostro por PUT, redimensionando ante rechazo por tamaño.
    last = (None, None)
    for maxside in sizes:
        img = resize_for_face(jpeg_bytes, max_side=maxside)
        if not img:
            return {"id_cliente": id_cliente, "action": "failed", "reason": "no se pudo procesar la imagen"}
        b64 = base64.b64encode(img).decode("ascii")
        resp = client.enroll_face(id_cliente, b64, access_group_ids=access_group_ids)
        code, msg = _resp_code(resp)
        last = (code, msg)

        if resp.status_code == 200 and code == "0":
            return {"id_cliente": id_cliente, "action": "created" if created else "enrolled", "maxside": maxside}

        if _is_stack(code, msg):
            continue  # foto todavía grande: probar un tamaño menor

        # Otro rechazo (rostro no detectable, etc.): no sirve seguir achicando.
        return {"id_cliente": id_cliente, "action": "failed", "reason": f"HTTP {resp.status_code} code {code}: {msg}"}

    return {"id_cliente": id_cliente, "action": "failed",
            "reason": f"rechazo persistente por tamaño hasta {sizes[-1]}px (code {last[0]})"}


def build_candidates(cur, prefix: str) -> dict:
    """Arma la lista de trabajo consultando xSys + el linked server de BioStar.

    Universo = socios en ``CD_Lista_Blanca_Suprema`` (habilitados Cuota Social) con al
    menos una foto en ``Clientes_Fotos``. Se clasifican contra ``T_USR``/``T_CRDT``:
      - no existe en BioStar → crear + enrolar
      - existe sin rostro (o DEL='Y') → enrolar
      - existe con rostro → se excluye (ya está)
    Devuelve {universe, to_create:[...], to_enroll:[...]} (cada item {id_cliente,name,exists}).
    """
    from access_control.services.diag_facial import _rows

    universe: dict[int, str] = {}
    for r in _rows(
        cur,
        """SELECT LB.Id_Cliente AS cid,
                  LTRIM(RTRIM(C.Apellido)) + ' ' + LTRIM(RTRIM(C.Nombre)) AS nombre
           FROM CD_Lista_Blanca_Suprema LB
           JOIN Clientes C ON C.Id_Cliente = LB.Id_Cliente
           WHERE EXISTS (SELECT 1 FROM Clientes_Fotos F WHERE F.Id_Cliente = LB.Id_Cliente)""",
    ):
        try:
            universe[int(r["cid"])] = (r.get("nombre") or "").strip()
        except (TypeError, ValueError):
            continue

    rostros_by_uid: dict[int, int] = {}
    for r in _rows(cur, f"SELECT USRUID, COUNT(*) AS n FROM {prefix}.T_CRDT GROUP BY USRUID"):
        try:
            rostros_by_uid[int(r["USRUID"])] = int(r["n"])
        except (TypeError, ValueError):
            continue

    state: dict[int, dict] = {}
    for u in _rows(cur, f"SELECT USRID, USRUID, DEL FROM {prefix}.T_USR"):
        try:
            cid = int(u["USRID"])
        except (TypeError, ValueError):
            continue
        try:
            uid = int(u["USRUID"])
        except (TypeError, ValueError):
            uid = None
        state[cid] = {
            "rostros": rostros_by_uid.get(uid, 0),
            "del": str(u.get("DEL") or "").upper() == "Y",
        }

    to_create, to_enroll = [], []
    for cid, nombre in universe.items():
        st = state.get(cid)
        if st is None:
            to_create.append({"id_cliente": cid, "name": nombre, "exists": False})
        elif st["del"] or st["rostros"] == 0:
            to_enroll.append({"id_cliente": cid, "name": nombre, "exists": True})

    return {"universe": len(universe), "to_create": to_create, "to_enroll": to_enroll}


def fetch_photo(cur, id_cliente: int) -> bytes | None:
    """Última foto (más reciente) del socio desde Clientes_Fotos, como bytes crudos."""
    cur.execute(
        f"SELECT TOP 1 Foto FROM Clientes_Fotos WHERE Id_Cliente = {int(id_cliente)} ORDER BY Fecha DESC"
    )
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return bytes(row[0])


def push_faces_affected(changed_ids, *, mode: str | None = None, max_per_run: int = 200) -> dict:
    """Re-enrola el rostro de los socios cuya FOTO cambió (habilitados con imagen).

    Es el gemelo facial de ``biostar_push.push_enabled_affected``: se llama desde el
    sync incremental de xSys con los ids cuya foto cambió. Usa la imagen del espejo
    local (``XsysSocioFoto``), no MSSQL. Best-effort: no lanza excepciones. Gobernado
    por ``BIOSTAR_FACE_SYNC_MODE`` (off|dryrun|on, default off).
    """
    from xsys.models import XsysWhitelist, XsysSocio, XsysSocioFoto

    mode = (mode or get_face_sync_mode()).strip().lower()
    result = {"mode": mode, "candidatos": 0, "enrolados": 0, "creados": 0, "errores": 0}
    ids = [int(i) for i in (changed_ids or []) if i is not None]
    if mode == "off" or not ids:
        result["skipped"] = mode == "off"
        return result

    habil = set(
        XsysWhitelist.objects.filter(id_cliente__in=ids, habilitado=True)
        .values_list("id_cliente", flat=True)
    )
    fotos = {
        f.id_cliente: f
        for f in XsysSocioFoto.objects.filter(id_cliente__in=habil).exclude(imagen=None)
    }
    to_do = sorted(fotos.keys())[:max_per_run]
    result["candidatos"] = len(to_do)
    if not to_do:
        return result

    if mode == "dryrun":
        logger.info("biostar_face_sync[DRYRUN]: re-enrolaría %s rostro(s): %s%s",
                    len(to_do), to_do[:20], " ..." if len(to_do) > 20 else "")
        result["dryrun_ids"] = to_do
        return result

    from access_control.services.biostar2_client import BioStar2Client

    try:
        client = BioStar2Client.from_db_and_env()
    except Exception as exc:
        logger.warning("biostar_face_sync[on]: no se pudo crear el cliente: %s", exc)
        result["error"] = str(exc)[:200]
        return result

    nombres = {
        s.id_cliente: f"{(s.apellido or '').strip()} {(s.nombre or '').strip()}".strip()
        for s in XsysSocio.objects.filter(pk__in=to_do)
    }
    for cid in to_do:
        foto = fotos[cid]
        jpeg = bytes(foto.imagen) if foto.imagen else None
        if not jpeg:
            continue
        try:
            probe = client.request("GET", f"/api/users/{cid}", check=False)
            exists = probe.status_code == 200
            res = enroll_one(client, id_cliente=cid, jpeg_bytes=jpeg,
                             name=nombres.get(cid, ""), exists=exists)
            action = res.get("action")
            if action == "enrolled":
                result["enrolados"] += 1
            elif action == "created":
                result["creados"] += 1
            else:
                result["errores"] += 1
        except Exception as exc:  # pragma: no cover - red/BioStar
            result["errores"] += 1
            logger.warning("biostar_face_sync[on]: %s falló: %s", cid, str(exc)[:120])

    logger.info("biostar_face_sync[on]: %s", result)
    return result

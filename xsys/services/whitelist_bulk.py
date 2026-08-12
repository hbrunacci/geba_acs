"""Cálculo MASIVO de la habilitación, con la misma lógica de ``check_access``.

Por qué existe
--------------
``compute_habilitacion`` (socio por socio) hace 5-8 round-trips contra el SQL
2014 por cada socio. Para los ~52.000 socios eso son ~300.000 idas y vueltas:
~1 hora. Ese costo es la causa real de que la lista blanca nunca se recalculara
entera y quedara con filas de 20 días de antigüedad (un socio que se ponía al
día, o que dejaba de estarlo, sin generar novedad, quedaba mal para siempre).

Acá se evalúa **la misma cascada** pero de a lotes, en UNA query por lote: las
funciones ``CF_SCA_*`` siguen siendo las de xSys (no se reimplementa ninguna
regla del club), lo único que se mueve a SQL es el ORDEN de la cascada, que ya
vivía en nuestro Python. Eso elimina la latencia de red, no la lógica.

Riesgo asumido y cómo se controla
---------------------------------
Duplicar el orden de la cascada en SQL puede divergir de ``check_access`` si
alguien toca uno de los dos lados. Por eso ``verify_bulk_against_single()``
compara ambos caminos sobre una muestra y el comando la corre en cada barrida:
si aparece una sola discrepancia, la barrida aborta sin escribir.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

# Mismos códigos/motivos que MSSQLAccessCheckService.MOTIVOS, resueltos acá para
# no depender del orden interno de aquel dict.
MOTIVO_KEYS = (
    "persona_inactiva",
    "vencimiento",
    "ucp_obligatoria",
    "master",
    "ucp",
    "contrato",
    "categoria",
    "producto",
    "producto_titular",
    "sin_habilitacion",
)

# Sub-consulta de "producto comprado que habilita". Es la misma de
# ``MSSQLAccessCheckService._producto_habilita`` pero correlacionada al socio de
# la fila en vez de parametrizada, para poder resolverla en el mismo SELECT.
_PRODUCTO_SQL = """
    SELECT TOP 1 CI.Id_Producto, ISNULL(PR.Descripcion_Resumida, CI.Id_Producto) AS Descr
    FROM Cbtes_Items CI
    JOIN Cbtes CB ON CI.Id_Trans = CB.Id_Trans
    JOIN Cbtes_Tipos CT ON CT.Id_Tipo_Cbte = CB.Id_Tipo_Cbte
    JOIN CD_Accesos_Prod CA ON CA.Id_Producto = CI.Id_Producto AND CA.Id_Acceso = @acc
    JOIN Productos PR ON PR.Id_Producto = CA.Id_Producto
    WHERE CI.Id_Cliente = {cliente_col}
      AND ((CT.Compromete_Factura = 1 AND CB.Id_Estado_Cbte IN (4,2))
           OR (CB.Id_Estado_Cbte IN (1) AND CI.Imp_Final = 0))
      AND dbo.CF_NC_A_FC(CB.Id_Trans) = 0
      AND ISNULL(CA.Valida_En_Titular,0) {titular}
      AND ISNULL(CA.Flag_Consumible,0) = 0
      AND (
            (ISNULL(PR.Flag_Mes,0) = 0 AND ISNULL(PR.Flag_Periodo,0) = 0)
         OR (ISNULL(PR.Flag_Mes,0) = 1 AND CONVERT(date, @f) <= DATEADD(DAY, ISNULL(CA.Dias_Gracia,0),
             DATEADD(DAY, 1, EOMONTH(DATEADD(MONTH, ISNULL(CA.Meses_Gracia,0), CONVERT(date, CI.Fecha_QA)), -1))))
         OR (ISNULL(PR.Flag_Periodo,0) = 1 AND CI.Fecha_QA < DATEADD(DAY, 1, CONVERT(date, @f))
             AND DATEADD(DAY, CA.Dias_Gracia, CI.Fecha_Venc) > DATEADD(DAY, -1, CONVERT(date, @f)))
      )
"""


def _bulk_sql(n_ids: int) -> str:
    """Arma la query del lote. ``n_ids`` sólo define la cantidad de placeholders."""
    marks = ",".join(["?"] * n_ids)
    prod = _PRODUCTO_SQL.format(cliente_col="C.Id_Cliente", titular="IN (0,1)")
    prod_tit = _PRODUCTO_SQL.format(cliente_col="C.Id_Cliente_Ref", titular="= 1")
    return f"""
DECLARE @acc INT = ?;
DECLARE @f DATETIME = ?;

SELECT
    C.Id_Cliente,
    ISNULL(C.Activo, 0)                                                   AS activo,
    ISNULL(C.Id_Cliente_Ref, 0)                                           AS id_ref,
    dbo.CF_SCA_ValidarVencimientosPersona(C.Id_Cliente, @acc, @f)         AS venc,
    dbo.CF_SCA_ValidarMaster(C.Id_Cliente)                                AS master,
    dbo.CF_SCA_ValidarUltCuotaPaga(C.Id_Cliente, @acc, @f)                AS ucp,
    dbo.CF_SCA_ValidarContratosTipos(C.Id_Cliente, @acc, @f)              AS contrato,
    dbo.CF_SCA_ValidarTipo(C.Id_Cliente, @acc, @f)                        AS tipo,
    P.Descr                                                               AS prod_desc,
    PT.Descr                                                              AS prod_tit_desc
FROM Clientes C
OUTER APPLY ({prod}) P
OUTER APPLY ({prod_tit}) PT
WHERE C.Id_Cliente IN ({marks});
"""


def _descr_vencimiento(cursor, id_venc) -> str:
    cursor.execute(
        "SELECT SUBSTRING(RTRIM(LTRIM(Descripcion)), 1, 16) FROM Clientes_Venc_Tipos WHERE Id_Tipo_Venc = ?",
        (id_venc,),
    )
    row = cursor.fetchone()
    return (row[0] or "").strip() if row else ""


def _descr_contrato(cursor, id_contrato) -> str:
    cursor.execute(
        "SELECT RTRIM(LTRIM(CT.Descripcion)) FROM Contratos_Tipos CT "
        "JOIN Contratos CO ON CT.Id_Tipo_Con = CO.Id_Tipo_Con WHERE CO.Id_Contrato = ?",
        (id_contrato,),
    )
    row = cursor.fetchone()
    return (row[0] or "").strip() if row else ""


def _descr_tipo(cursor, id_tipo) -> str:
    cursor.execute(
        "SELECT RTRIM(LTRIM(Descripcion)) FROM Clientes_Tipos WHERE Id_Tipo_Cli = ?",
        (id_tipo,),
    )
    row = cursor.fetchone()
    return (row[0] or "").strip() if row else ""


def get_acceso_flags(cursor, id_acceso: int) -> tuple[int, int, str]:
    """Devuelve (Flag_Ult_Cuota_Paga, Flag_Evento, Descripcion) del acceso."""
    cursor.execute(
        "SELECT ISNULL(Flag_Ult_Cuota_Paga,0), ISNULL(Flag_Evento,0), Descripcion, Activo "
        "FROM CD_Accesos WHERE Id_Acceso = ?",
        (id_acceso,),
    )
    row = cursor.fetchone()
    if not row or not row[3]:
        raise RuntimeError(f"El acceso {id_acceso} no existe o está inactivo.")
    return int(row[0]), int(row[1]), (row[2] or "").strip()


def compute_habilitacion_bulk(
    cursor,
    ids: Sequence[int],
    *,
    id_acceso: int,
    fecha: datetime | None = None,
    flag_ucp: int | None = None,
    descripciones: bool = True,
) -> dict[int, dict[str, Any]]:
    """Evalúa la cascada de habilitación para muchos socios en UNA query.

    Devuelve ``{id_cliente: {habilitado, motivo_code, motivo, detalle, id_acceso}}``
    con exactamente las mismas claves que ``compute_habilitacion``.
    """
    from access_control.services import MSSQLAccessCheckService

    ids = [int(i) for i in ids]
    if not ids:
        return {}
    fecha = fecha or datetime.now()
    if flag_ucp is None:
        flag_ucp, _flag_evento, _d = get_acceso_flags(cursor, id_acceso)

    motivos = MSSQLAccessCheckService.MOTIVOS

    cursor.execute(_bulk_sql(len(ids)), (id_acceso, fecha, *ids))
    filas = cursor.fetchall()

    # Cachés de descripciones: se repiten muchísimo entre socios (mismo tipo,
    # mismo contrato), y cada una es un round-trip. Sin esto volveríamos a lo
    # que veníamos a evitar.
    cache_venc: dict[Any, str] = {}
    cache_contrato: dict[Any, str] = {}
    cache_tipo: dict[Any, str] = {}

    out: dict[int, dict[str, Any]] = {}
    for row in filas:
        (cid, activo, _id_ref, venc, master, ucp, contrato, tipo, prod_desc, prod_tit_desc) = row
        cid = int(cid)

        def res(hab: bool, key: str, detalle: str = "") -> dict[str, Any]:
            code, desc = motivos[key]
            return {
                "habilitado": hab,
                "motivo_code": code,
                "motivo": desc,
                "detalle": detalle,
                "id_acceso": id_acceso,
            }

        # --- MISMA cascada que MSSQLAccessCheckService.check_access ---
        if not activo:
            out[cid] = res(False, "persona_inactiva")
            continue
        if venc:
            if descripciones and venc not in cache_venc:
                cache_venc[venc] = _descr_vencimiento(cursor, venc)
            out[cid] = res(False, "vencimiento", cache_venc.get(venc, ""))
            continue
        if flag_ucp == 2 and not ucp:
            out[cid] = res(False, "ucp_obligatoria")
            continue
        if master:
            out[cid] = res(True, "master")
            continue
        if flag_ucp == 1 and ucp:
            out[cid] = res(True, "ucp")
            continue
        if contrato:
            if descripciones and contrato not in cache_contrato:
                cache_contrato[contrato] = _descr_contrato(cursor, contrato)
            out[cid] = res(True, "contrato", cache_contrato.get(contrato, ""))
            continue
        if tipo:
            if descripciones and tipo not in cache_tipo:
                cache_tipo[tipo] = _descr_tipo(cursor, tipo)
            out[cid] = res(True, "categoria", cache_tipo.get(tipo, ""))
            continue
        if prod_desc:
            out[cid] = res(True, "producto", str(prod_desc).strip())
            continue
        if prod_tit_desc:
            out[cid] = res(True, "producto_titular", str(prod_tit_desc).strip())
            continue
        out[cid] = res(False, "sin_habilitacion")

    # Socios pedidos que no existen en Clientes: mismo contrato que el camino
    # de a uno, que devuelve motivo "no_encontrado" cuando no resuelve el id.
    for cid in ids:
        if cid not in out:
            out[cid] = {
                "habilitado": False,
                "motivo_code": None,
                "motivo": "no_encontrado",
                "detalle": "",
                "id_acceso": id_acceso,
            }
    return out


def verify_bulk_against_single(
    cursor,
    ids: Iterable[int],
    *,
    id_acceso: int,
    fecha: datetime | None = None,
) -> dict[str, Any]:
    """Compara el camino masivo contra el de a uno sobre una muestra.

    Es el control que hace auditable la barrida completa: si ambos caminos no
    coinciden al 100 %, no se escribe nada.
    """
    from .whitelist import XsysAccessCheckService, compute_habilitacion

    ids = [int(i) for i in ids]
    if not ids:
        return {"muestra": 0, "coinciden": 0, "difieren": 0, "detalle": []}

    fecha = fecha or datetime.now()
    masivo = compute_habilitacion_bulk(cursor, ids, id_acceso=id_acceso, fecha=fecha)

    service = XsysAccessCheckService()
    coinciden = 0
    difieren: list[dict[str, Any]] = []
    for cid in ids:
        uno = compute_habilitacion(cid, service=service, id_acceso=id_acceso, cursor=cursor)
        mas = masivo.get(cid, {})
        if bool(uno["habilitado"]) == bool(mas.get("habilitado")) and \
           (uno.get("motivo_code") == mas.get("motivo_code")):
            coinciden += 1
        else:
            difieren.append({
                "id_cliente": cid,
                "de_a_uno": {"habilitado": uno["habilitado"], "motivo": uno.get("motivo")},
                "masivo": {"habilitado": mas.get("habilitado"), "motivo": mas.get("motivo")},
            })
    return {
        "muestra": len(ids),
        "coinciden": coinciden,
        "difieren": len(difieren),
        "detalle": difieren[:20],
    }

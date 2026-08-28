"""Por qué esta persona entra o no entra: el diagnóstico completo, en un lugar.

Motivación
----------
Averiguar por qué a alguien no lo dejan pasar llevaba media hora de consultas
sueltas contra xSys, y el operador de la puerta no tiene cómo hacerlas. Peor:
las pantallas que sí tiene (visor, cuenta corriente) muestran datos que se
**contradicen entre sí** sin decirlo, y quien mira se queda con el que vio
primero. Los tres desencuentros que motivaron este módulo, con el caso real que
los expuso (socio 872811, agosto 2026):

1. **``Clientes.Ult_Cuota_Paga`` no se calcula de los pagos: se carga a mano.**
   Al 28/08/2026 hay 5.432 clientes con ese campo en el futuro, algunos en 2050
   y 2106. El visor lo mostraba como "Última cuota paga 1/12/2026" mientras el
   socio debía $374.000 y la puerta lo rechazaba. Acá el campo se muestra
   siempre etiquetado como manual, y se compara contra los comprobantes.

2. **La cuenta corriente muestra sólo lo impago**, así que "no veo esos
   comprobantes" es lo esperable cuando uno mira ahí: los pagados no figuran
   justamente porque están saldados. La puerta, en cambio, mira ``Cbtes_Items``
   entero y sólo acepta ``Id_Estado_Cbte IN (4,2)`` (PARCIAL o COMPLETO). Un
   cupón emitido y PENDIENTE no habilita. Acá se listan todos, con el estado.

3. **Cada producto tiene su propia gracia**, en ``CD_Accesos_Prod``
   (``Meses_Gracia`` / ``Dias_Gracia``), distinta de la de la cuota social en
   ``CD_Accesos``. La de CUOTA SOCIAL en el acceso 22 es de 2 meses + 10 días,
   contra los 40 días del estatuto. Acá se muestra la fecha límite ya calculada.

Y un cuarto, de identidad: **un mismo documento puede estar en muchos clientes**
(ese DNI estaba en 15, casi todos invitados dados de baja). Si el diagnóstico se
hace sobre el registro equivocado, todo lo demás es ruido. Por eso lo primero
que devuelve es la lista de candidatos y cuál eligió.

Todo se lee EN VIVO de xSys: es una consulta puntual a pedido de una persona, no
un proceso masivo, y el espejo local puede estar desactualizado — que el espejo
mienta es, precisamente, una de las cosas que hay que poder detectar.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from django.conf import settings

from xsys.services.mssql import connect as xsys_connect
from xsys.services.whitelist import whitelist_params
from xsys.services.whitelist_bulk import server_now

logger = logging.getLogger(__name__)

# Estados de comprobante que la puerta acepta como "pagado", según el WHERE de
# ``CP_SCA_ValidarProducVta``. Los nombres salen de ``Cbtes_Estados``.
ESTADOS_PAGO = {2: "COMPLETO", 4: "PARCIAL"}
ESTADO_PENDIENTE = 1

# Cuántas pasadas y cuántos comprobantes se traen. Es una pantalla, no un
# reporte: más filas que esto no se leen, sólo tapan lo importante.
TOPE_PASADAS = 15
TOPE_COMPROBANTES = 20

# Si el último comprobante del producto habilitante es más viejo que esto, se
# avisa: dejó de facturarse, que es distinto de "no pagó".
MESES_SIN_FACTURAR = 2


def _norm_doc(valor: str) -> str:
    """Documento comparable: sin puntos, espacios ni guiones."""
    return re.sub(r"[^0-9A-Za-z]", "", str(valor or ""))


def _d(v) -> str | None:
    """Fecha a ISO, tolerando None y datetime/date."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _txt(v) -> str:
    return (str(v).strip() if v is not None else "")


# --------------------------------------------------------------------- candidatos
_SQL_CANDIDATOS_DOC = """
SELECT C.Id_Cliente,
       LTRIM(RTRIM(ISNULL(C.Apellido,'') + ', ' + ISNULL(C.Nombre,''))) AS nom,
       ISNULL(C.Razon_Social,'') AS razon,
       C.Doc_Nro, C.Id_Tipo_Cli, ISNULL(T.Descripcion,'') AS categoria,
       ISNULL(C.Activo,0) AS activo, C.Fecha_Baja, C.Ult_Cuota_Paga,
       ISNULL(C.Id_Cliente_Ref,0) AS id_ref, ISNULL(C.Credencial_Nro,'') AS credencial
FROM Clientes C
LEFT JOIN Clientes_Tipos T ON T.Id_Tipo_Cli = C.Id_Tipo_Cli
WHERE REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(C.Doc_Nro)),'.',''),' ',''),'-','') = ?
ORDER BY ISNULL(C.Activo,0) DESC, C.Id_Cliente
"""

_SQL_CANDIDATO_ID = _SQL_CANDIDATOS_DOC.replace(
    "WHERE REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(C.Doc_Nro)),'.',''),' ',''),'-','') = ?",
    "WHERE C.Id_Cliente = ?",
)


def _fila_candidato(row) -> dict[str, Any]:
    (cid, nom, razon, doc, tipo, categoria, activo, baja, ucp, id_ref, cred) = row
    nombre = _txt(nom).strip(",").strip() or _txt(razon)
    return {
        "id_cliente": int(cid),
        "nombre": nombre or f"Cliente {cid}",
        "doc_nro": _txt(doc),
        "id_tipo_cli": int(tipo) if tipo is not None else None,
        "categoria": _txt(categoria),
        "activo": bool(activo),
        "fecha_baja": _d(baja),
        "ult_cuota_paga": _d(ucp),
        "id_cliente_ref": int(id_ref or 0),
        "credencial": _txt(cred),
    }


def _buscar_candidatos(cur, *, doc: str | None, id_cliente: int | None) -> list[dict]:
    if id_cliente:
        cur.execute(_SQL_CANDIDATO_ID, (int(id_cliente),))
        return [_fila_candidato(r) for r in cur.fetchall()]
    cur.execute(_SQL_CANDIDATOS_DOC, (_norm_doc(doc),))
    return [_fila_candidato(r) for r in cur.fetchall()]


def _elegir(candidatos: list[dict]) -> dict | None:
    """El registro sobre el que se diagnostica.

    Se prefiere el activo. Entre varios activos (pasa: mismo DNI cargado dos
    veces), el de id más alto, que es el último creado — pero la pantalla muestra
    todos para que el operador pueda cambiar de registro.
    """
    if not candidatos:
        return None
    activos = [c for c in candidatos if c["activo"]]
    pool = activos or candidatos
    return max(pool, key=lambda c: c["id_cliente"])


# ------------------------------------------------------------------- la cascada
# Misma sub-consulta de producto que ``whitelist_bulk._PRODUCTO_SQL``, pero
# correlacionada al acceso de la fila (A.Id_Acceso) en vez de a un @acc fijo:
# así los 27 accesos se resuelven en UNA sola ida al servidor.
_PRODUCTO_POR_ACCESO = """
    SELECT TOP 1 ISNULL(PR.Descripcion_Resumida, CI.Id_Producto) AS Descr
    FROM Cbtes_Items CI
    JOIN Cbtes CB ON CI.Id_Trans = CB.Id_Trans
    JOIN Cbtes_Tipos CT ON CT.Id_Tipo_Cbte = CB.Id_Tipo_Cbte
    JOIN CD_Accesos_Prod CA ON CA.Id_Producto = CI.Id_Producto AND CA.Id_Acceso = A.Id_Acceso
    JOIN Productos PR ON PR.Id_Producto = CA.Id_Producto
    WHERE CI.Id_Cliente = {cliente}
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


def _sql_cascada() -> str:
    prod = _PRODUCTO_POR_ACCESO.format(cliente="@cid", titular="IN (0,1)")
    prod_tit = _PRODUCTO_POR_ACCESO.format(cliente="@ref", titular="= 1")
    return f"""
DECLARE @cid INT = ?;
DECLARE @f DATETIME = ?;
DECLARE @ref INT = ISNULL((SELECT Id_Cliente_Ref FROM Clientes WHERE Id_Cliente = @cid), 0);

SELECT A.Id_Acceso,
       LTRIM(RTRIM(A.Descripcion))                                      AS descripcion,
       ISNULL(A.Flag_Ult_Cuota_Paga, 0)                                 AS flag_ucp,
       dbo.CF_SCA_ValidarVencimientosPersona(@cid, A.Id_Acceso, @f)     AS venc,
       dbo.CF_SCA_ValidarMaster(@cid)                                   AS master,
       dbo.CF_SCA_ValidarUltCuotaPaga(@cid, A.Id_Acceso, @f)            AS ucp,
       dbo.CF_SCA_ValidarContratosTipos(@cid, A.Id_Acceso, @f)          AS contrato,
       dbo.CF_SCA_ValidarTipo(@cid, A.Id_Acceso, @f)                    AS tipo,
       P.Descr                                                          AS prod,
       PT.Descr                                                         AS prod_tit
FROM CD_Accesos A
OUTER APPLY ({prod}) P
OUTER APPLY ({prod_tit}) PT
WHERE ISNULL(A.Activo, 0) = 1
ORDER BY A.Id_Acceso;
"""


def _evaluar_accesos(cur, cid: int, fecha, activo: bool) -> list[dict]:
    """La cascada de xSys, acceso por acceso, con el motivo en castellano.

    El orden es el de ``CP_SCA_RegistrarAcceso`` y el mismo que usa
    ``whitelist_bulk``: vencimientos → UCP obligatoria → master → UCP → contrato
    → categoría → producto → producto del titular.
    """
    cur.execute(_sql_cascada(), (cid, fecha))
    filas = cur.fetchall()
    out: list[dict] = []
    for (acc, desc, flag_ucp, venc, master, ucp, contrato, tipo, prod, prod_tit) in filas:
        flag_ucp = int(flag_ucp or 0)
        if not activo:
            hab, motivo, detalle = False, "Persona inactiva en xSys", ""
        elif venc:
            hab, motivo, detalle = False, "Vencimiento de la persona", str(venc)
        elif flag_ucp == 2 and not ucp:
            hab, motivo, detalle = False, "Cuota social vencida (obligatoria en este acceso)", ""
        elif master:
            hab, motivo, detalle = True, "Acceso master", ""
        elif flag_ucp == 1 and ucp:
            hab, motivo, detalle = True, "Cuota social al día", ""
        elif contrato:
            hab, motivo, detalle = True, "Contrato habilitante", f"contrato {contrato}"
        elif tipo:
            hab, motivo, detalle = True, "Categoría habilitante", f"tipo {tipo}"
        elif prod:
            hab, motivo, detalle = True, "Producto comprado", _txt(prod)
        elif prod_tit:
            hab, motivo, detalle = True, "Producto comprado por el titular", _txt(prod_tit)
        else:
            hab, motivo, detalle = False, "No cumple ninguna condición habilitante", ""
        out.append({
            "id_acceso": int(acc),
            "descripcion": _txt(desc),
            "habilitado": hab,
            "motivo": motivo,
            "detalle": detalle,
            # Flag_Ult_Cuota_Paga: 0 = la cuota no se mira acá, 1 = habilita,
            # 2 = es obligatoria (rechaza si está vencida).
            "controla_cuota": flag_ucp,
            "es_barrera": flag_ucp != 0,
            "id_contrato": int(contrato) if contrato else None,
            "id_tipo_cli": int(tipo) if tipo else None,
        })
    return out


# ------------------------------------------------------- comprobantes del producto
_SQL_COMPROBANTES = """
SELECT TOP (?) CI.Id_Trans, CI.Id_Producto,
       LTRIM(RTRIM(ISNULL(PR.Descripcion_Resumida, CI.Id_Producto))) AS producto,
       LTRIM(RTRIM(CT.Descripcion))                                  AS tipo_cbte,
       CB.Comprobante_Nro, CB.Fecha, CI.Fecha_QA, CI.Fecha_Venc,
       CI.Imp_Final, CB.Id_Estado_Cbte,
       LTRIM(RTRIM(ISNULL(ES.Descripcion,'')))                       AS estado,
       CT.Compromete_Factura,
       ISNULL(CA.Meses_Gracia,0)                                     AS meses_gracia,
       ISNULL(CA.Dias_Gracia,0)                                      AS dias_gracia,
       ISNULL(PR.Flag_Mes,0)                                         AS flag_mes,
       ISNULL(PR.Flag_Periodo,0)                                     AS flag_periodo,
       DATEADD(DAY, ISNULL(CA.Dias_Gracia,0),
               DATEADD(DAY, 1, EOMONTH(DATEADD(MONTH, ISNULL(CA.Meses_Gracia,0),
                                               CONVERT(date, CI.Fecha_QA)), -1)))  AS limite_mes,
       DATEADD(DAY, ISNULL(CA.Dias_Gracia,0), CI.Fecha_Venc)                       AS limite_periodo,
       dbo.CF_NC_A_FC(CB.Id_Trans)                                   AS anulado_por_nc
FROM Cbtes_Items CI
JOIN Cbtes CB ON CB.Id_Trans = CI.Id_Trans
JOIN Cbtes_Tipos CT ON CT.Id_Tipo_Cbte = CB.Id_Tipo_Cbte
JOIN CD_Accesos_Prod CA ON CA.Id_Producto = CI.Id_Producto AND CA.Id_Acceso = ?
LEFT JOIN Productos PR ON PR.Id_Producto = CI.Id_Producto
LEFT JOIN Cbtes_Estados ES ON ES.Id_Estado_Cbte = CB.Id_Estado_Cbte
WHERE CI.Id_Cliente = ?
ORDER BY CI.Fecha_QA DESC, CI.Id_Trans DESC
"""


def _comprobantes_producto(cur, cid: int, id_acceso: int, hoy: date) -> list[dict]:
    """Los comprobantes del producto que habilita ese acceso, con el veredicto.

    Es el detalle que ninguna pantalla mostraba: no alcanza con "tiene el
    producto", hay que ver **en qué estado** está el comprobante y **hasta
    cuándo** vale. Por cada fila se dice si sirve y, si no, por qué no.
    """
    cur.execute(_SQL_COMPROBANTES, (TOPE_COMPROBANTES, id_acceso, cid))
    out: list[dict] = []
    for row in cur.fetchall():
        (trans, id_prod, producto, tipo_cbte, nro, fecha, fqa, fvenc, imp,
         estado_id, estado, compromete, meses_g, dias_g, flag_mes, flag_per,
         lim_mes, lim_per, anulado) = row
        estado_id = int(estado_id or 0)
        imp = float(imp or 0)
        # Las mismas dos condiciones del SP, evaluadas por separado para poder
        # decir CUÁL falla. ``Compromete_Factura`` vale 1 en lo que factura,
        # -1 en las notas de crédito y 0 en remitos y similares: sólo el 1 puede
        # habilitar (o, por la segunda rama, un pendiente de importe cero).
        es_factura = int(compromete or 0) == 1
        estado_ok = (es_factura and estado_id in ESTADOS_PAGO) or \
                    (estado_id == ESTADO_PENDIENTE and imp == 0)
        if int(flag_mes or 0) == 1:
            limite = lim_mes
        elif int(flag_per or 0) == 1:
            limite = lim_per
        else:
            limite = None  # el producto no vence
        limite_d = limite.date() if isinstance(limite, datetime) else limite
        vigente = True if limite_d is None else (hoy <= limite_d)
        anulado = bool(anulado)

        if anulado:
            veredicto, porque = False, "anulado por una nota de crédito posterior"
        elif not es_factura and not (estado_id == ESTADO_PENDIENTE and imp == 0):
            # Una NC o un remito aparecen en el listado porque explican el saldo,
            # pero por definición no habilitan: no son el comprobante que se paga.
            veredicto, porque = False, f"{_txt(tipo_cbte).lower() or 'este comprobante'} no habilita accesos"
        elif not estado_ok:
            veredicto, porque = False, (
                f"comprobante {estado or estado_id}: la puerta sólo toma PARCIAL o COMPLETO"
            )
        elif not vigente:
            veredicto, porque = False, f"venció el {_d(limite_d)}"
        else:
            veredicto, porque = True, ""

        out.append({
            "id_trans": int(trans),
            "id_producto": _txt(id_prod),
            "producto": _txt(producto),
            "tipo": _txt(tipo_cbte),
            "comprobante_nro": int(nro) if nro is not None else None,
            "fecha": _d(fecha),
            "periodo": _d(fqa),
            "importe": imp,
            "estado": _txt(estado) or str(estado_id),
            "estado_id": estado_id,
            "vale_hasta": _d(limite_d),
            "habilita": veredicto,
            "porque_no": porque,
            "es_factura": es_factura,
            "gracia": f"{meses_g} mes(es) + {dias_g} día(s)",
        })
    return out


# ------------------------------------------------------------------- contratos
_SQL_CONTRATOS = """
SELECT CO.Id_Contrato, CO.Id_Tipo_Con, LTRIM(RTRIM(ISNULL(CT.Descripcion,''))) AS tipo,
       CO.Fecha_Desde, CO.Fecha_Hasta, CO.Fecha_Baja, ISNULL(CO.Activo,0) AS activo
FROM Contratos CO
LEFT JOIN Contratos_Tipos CT ON CT.Id_Tipo_Con = CO.Id_Tipo_Con
WHERE CO.Id_Cliente = ?
ORDER BY CO.Id_Contrato DESC
"""


def _contratos(cur, cid: int, hoy: date) -> list[dict]:
    cur.execute(_SQL_CONTRATOS, (cid,))
    out = []
    for (idc, tipo_id, tipo, desde, hasta, baja, activo) in cur.fetchall():
        d_desde = desde.date() if isinstance(desde, datetime) else desde
        d_hasta = hasta.date() if isinstance(hasta, datetime) else hasta
        # Ojo: ésta es la regla REAL de xSys. ``CF_SCA_ValidarContratosTipos``
        # ignora Fecha_Baja y Activo — hay un comentario de 2019 en la función
        # que dice "se controla solo por las fechas desde y hasta". Por eso un
        # contrato dado de baja puede seguir habilitando, y acá se marca.
        en_fecha = (d_desde is None or d_desde <= hoy) and (d_hasta is None or d_hasta >= hoy)
        out.append({
            "id_contrato": int(idc),
            "id_tipo_con": int(tipo_id) if tipo_id is not None else None,
            "tipo": _txt(tipo),
            "desde": _d(desde),
            "hasta": _d(hasta),
            "baja": _d(baja),
            "activo": bool(activo),
            "habilita_por_fecha": en_fecha,
            "dado_de_baja_pero_habilita": bool(en_fecha and (baja is not None or not activo)),
        })
    return out


# -------------------------------------------------------------------- pasadas
_SQL_PASADAS = """
SELECT TOP (?) E.Fecha, E.Id_Acceso, LTRIM(RTRIM(ISNULL(A.Descripcion,''))) AS acceso,
       E.Id_Controlador, LTRIM(RTRIM(ISNULL(CC.Descripcion,''))) AS controlador,
       E.Resultado, E.Id_CD_Motivo, CAST(E.Observacion AS VARCHAR(200)) AS obs
FROM CD_ES E
LEFT JOIN CD_Accesos A ON A.Id_Acceso = E.Id_Acceso
LEFT JOIN CD_Controladores CC ON CC.Id_Controlador = E.Id_Controlador
WHERE E.Id_Cliente = ?
ORDER BY E.Fecha DESC
"""


def _pasadas(cur, cid: int) -> list[dict]:
    cur.execute(_SQL_PASADAS, (TOPE_PASADAS, cid))
    return [{
        "fecha": f.isoformat() if isinstance(f, datetime) else _d(f),
        "id_acceso": int(acc) if acc is not None else None,
        "acceso": _txt(accd),
        "id_controlador": int(ctrl) if ctrl is not None else None,
        "controlador": _txt(ctrld),
        "permitido": _txt(res) == "S",
        "motivo": _txt(obs),
    } for (f, acc, accd, ctrl, ctrld, res, mot, obs) in cur.fetchall()]


# ------------------------------------------------------- estado local y BioStar
def _estado_local(cid: int) -> dict:
    """Lo que el propio geba_acs cree, para poder contrastarlo con xSys."""
    from access_control.models import BioStarUser, BiostarAccessEvent
    from access_control.services.biostar_access_state import biostar_permite
    from xsys.models import XsysSocio, XsysWhitelist

    wl = XsysWhitelist.objects.filter(id_cliente=cid).first()
    socio = XsysSocio.objects.filter(id_cliente=cid).first()
    user = BioStarUser.objects.filter(user_id=cid).first()
    faciales = list(
        BiostarAccessEvent.objects.filter(id_cliente=cid)
        .order_by("-fecha")
        .values("fecha", "device_name", "event_name", "permitido")[:TOPE_PASADAS]
    )
    return {
        "espejo": None if socio is None else {
            "categoria": socio.categoria,
            "ult_cuota_paga": _d(socio.ult_cuota_paga),
            "activo": bool(socio.activo),
            "synced_at": socio.synced_at.isoformat() if socio.synced_at else None,
        },
        "whitelist": None if wl is None else {
            "habilitado": bool(wl.habilitado),
            "motivo": wl.motivo,
            "detalle": wl.detalle,
            "id_acceso": wl.id_acceso,
            "fecha_calculo": wl.fecha_calculo.isoformat() if wl.fecha_calculo else None,
        },
        "biostar": None if user is None else {
            "enrolado": True,
            "nombre": user.name,
            "permite_paso": biostar_permite(user.raw_payload),
        },
        "eventos_faciales": [{
            "fecha": e["fecha"].isoformat() if e["fecha"] else None,
            "equipo": e["device_name"],
            "evento": e["event_name"],
            "permitido": bool(e["permitido"]),
        } for e in faciales],
    }


# --------------------------------------------------------------------- alertas
def _alertas(*, candidatos, socio, accesos, comprobantes, contratos, local,
             id_acceso_wl: int, hoy: date) -> list[dict]:
    """Las contradicciones que hacen perder tiempo, dichas explícitamente.

    Cada una de estas salió de un caso real en el que dos pantallas del club
    decían cosas distintas y nadie sabía cuál mandaba.
    """
    al: list[dict] = []

    def add(nivel: str, titulo: str, detalle: str) -> None:
        al.append({"nivel": nivel, "titulo": titulo, "detalle": detalle})

    # 1. Mismo documento en varios clientes.
    if len(candidatos) > 1:
        otros = [c for c in candidatos if c["id_cliente"] != socio["id_cliente"]]
        activos = [c for c in otros if c["activo"]]
        add(
            "warning" if activos else "info",
            f"El documento está en {len(candidatos)} registros de xSys",
            f"Se diagnostica el #{socio['id_cliente']} ({socio['categoria'] or 'sin categoría'})."
            + (f" Hay {len(activos)} más activo(s): "
               + ", ".join(f"#{c['id_cliente']} {c['categoria']}" for c in activos[:5]) + "."
               if activos else " Los demás están dados de baja."),
        )

    # 2. El campo manual contra los pagos.
    ucp = socio.get("ult_cuota_paga")
    sirve_alguno = any(c["habilita"] for c in comprobantes)
    if ucp and ucp >= hoy.isoformat() and not sirve_alguno and comprobantes:
        add("danger",
            "«Última cuota paga» dice al día, pero ningún comprobante lo habilita",
            f"El campo marca {ucp}. Se carga a mano en xSys y la puerta no lo mira "
            f"en este acceso: mira los comprobantes, y ninguno sirve hoy.")

    # 3. Emitido pero impago.
    pendientes = [c for c in comprobantes
                  if c["es_factura"] and c["estado_id"] == ESTADO_PENDIENTE and c["importe"]]
    if pendientes and not sirve_alguno:
        p = pendientes[0]
        add("danger",
            "Tiene el producto, pero el comprobante está PENDIENTE",
            f"{p['producto']} del período {p['periodo']} por ${p['importe']:,.2f} sigue impago "
            f"(comprobante {p['comprobante_nro']}). La puerta sólo acepta PARCIAL o COMPLETO.")

    # 4. Pagó, pero se le venció la gracia del producto.
    vencidos = [c for c in comprobantes
                if c["es_factura"] and not c["habilita"] and c["vale_hasta"]
                and c["estado_id"] in ESTADOS_PAGO]
    if vencidos and not sirve_alguno:
        v = max(vencidos, key=lambda c: c["vale_hasta"])
        add("warning",
            "El último comprobante pago ya venció",
            f"{v['producto']} del período {v['periodo']} valía hasta el {v['vale_hasta']} "
            f"(gracia de {v['gracia']}).")

    # 5. Dejó de facturarse. Se mira el último comprobante que FACTURA: las
    # notas de crédito son posteriores y taparían el hueco.
    facturas = [c for c in comprobantes if c["es_factura"]]
    if facturas:
        ultimo = facturas[0]["periodo"]
        if ultimo:
            meses = (hoy.year - int(ultimo[:4])) * 12 + (hoy.month - int(ultimo[5:7]))
            if meses >= MESES_SIN_FACTURAR:
                add("warning",
                    f"No se le emite comprobante desde hace {meses} meses",
                    f"El último es del período {ultimo}. Eso es de administración, "
                    f"no de accesos: sin cupón nuevo no hay forma de que se ponga al día.")

    # 6. Contrato dado de baja que igual habilita.
    fantasmas = [c for c in contratos if c["dado_de_baja_pero_habilita"]]
    for c in fantasmas[:3]:
        add("info",
            f"Entra por un contrato dado de baja: {c['tipo']}",
            f"El contrato {c['id_contrato']} tiene baja {c['baja'] or 'sin fecha'} pero sigue "
            f"habilitando, porque xSys valida sólo por las fechas desde/hasta.")

    # 7. El espejo local discrepa de xSys.
    wl = (local or {}).get("whitelist")
    acc_wl = next((a for a in accesos if a["id_acceso"] == id_acceso_wl), None)
    if wl is not None and acc_wl is not None and bool(wl["habilitado"]) != acc_wl["habilitado"]:
        add("warning",
            "La lista blanca local no coincide con xSys",
            f"Guardada: {'habilitado' if wl['habilitado'] else 'no habilitado'} "
            f"(calculada {wl['fecha_calculo']}). En xSys ahora: "
            f"{'habilitado' if acc_wl['habilitado'] else 'no habilitado'}.")

    # 8. Enrolado en el facial pero con el paso cerrado.
    bs = (local or {}).get("biostar")
    if bs and not bs["permite_paso"]:
        add("info",
            "En el equipo facial figura con el acceso cerrado",
            "El lector le va a reconocer la cara y le va a negar el paso. "
            "Se destraba solo cuando la lista blanca vuelva a habilitarlo.")

    return al


def _conclusion(socio: dict, accesos: list[dict], alertas: list[dict], id_acceso_wl: int) -> str:
    """Una frase para el operador, que es lo único que va a leer con la fila esperando."""
    abiertos = [a for a in accesos if a["habilitado"]]
    if not socio["activo"]:
        return "La persona está inactiva en xSys: no entra por ninguna puerta."
    if not abiertos:
        graves = [a for a in alertas if a["nivel"] == "danger"]
        if graves:
            return f"No entra por ninguna puerta. {graves[0]['titulo']}."
        return "No entra por ninguna puerta: no cumple ninguna condición habilitante."
    acc_wl = next((a for a in accesos if a["id_acceso"] == id_acceso_wl), None)
    if acc_wl is not None and not acc_wl["habilitado"]:
        nombres = ", ".join(a["descripcion"] for a in abiertos[:4])
        return (f"No entra por los faciales ({acc_wl['motivo'].lower()}), "
                f"pero sí por: {nombres}.")
    return f"Entra por {len(abiertos)} acceso(s). {abiertos[0]['motivo']}."


# ------------------------------------------------------------------- entrypoint
def diagnosticar(*, doc: str | None = None, id_cliente: int | None = None) -> dict[str, Any]:
    """Diagnóstico completo de por qué una persona entra o no.

    Se busca por documento o por número de socio. Devuelve un dict listo para
    serializar; ``encontrado=False`` si el documento no existe en xSys.
    """
    if not doc and not id_cliente:
        raise ValueError("Hay que indicar un documento o un número de socio.")

    conn = xsys_connect(settings.MSSQL_XSYS)
    try:
        cur = conn.cursor()
        ahora = server_now(cur)
        hoy = ahora.date()

        candidatos = _buscar_candidatos(cur, doc=doc, id_cliente=id_cliente)
        socio = _elegir(candidatos)
        if socio is None:
            return {
                "encontrado": False,
                "consulta": {"doc": doc, "id_cliente": id_cliente},
                "fecha": ahora.isoformat(),
                "candidatos": [],
                "conclusion": "No hay ninguna persona con ese documento o número de socio en xSys.",
            }

        cid = socio["id_cliente"]
        id_acceso_wl, _ = whitelist_params()

        accesos = _evaluar_accesos(cur, cid, ahora, socio["activo"])
        comprobantes = _comprobantes_producto(cur, cid, id_acceso_wl, hoy)
        contratos = _contratos(cur, cid, hoy)
        pasadas = _pasadas(cur, cid)
        local = _estado_local(cid)
        alertas = _alertas(candidatos=candidatos, socio=socio, accesos=accesos,
                           comprobantes=comprobantes, contratos=contratos, local=local,
                           id_acceso_wl=id_acceso_wl, hoy=hoy)

        return {
            "encontrado": True,
            "consulta": {"doc": doc, "id_cliente": id_cliente},
            "fecha": ahora.isoformat(),
            "candidatos": candidatos,
            "socio": socio,
            "conclusion": _conclusion(socio, accesos, alertas, id_acceso_wl),
            "alertas": alertas,
            "accesos": accesos,
            "acceso_faciales": id_acceso_wl,
            "comprobantes": comprobantes,
            "contratos": contratos,
            "pasadas": pasadas,
            "local": local,
        }
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - cierre best-effort
            logger.debug("diagnostico: no se pudo cerrar la conexión", exc_info=True)

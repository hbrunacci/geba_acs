"""Cuenta corriente del socio: cargos, pagos y saldo, leídos en vivo de xSys.

No se espeja. La cuenta corriente cambia con cada cobro de caja y con cada lote de
facturación, y el operador que la mira está atendiendo al socio en el mostrador:
un espejo con minutos de atraso sería peor que no tenerla. Se consulta por socio y
por ventana de fechas, que es un acceso puntual y barato.

Cómo leer los signos, que es lo que confunde a todo el mundo la primera vez:

    Importe > 0   cargo   (cupón de cuota, remito de comodidades, nota de débito)
    Importe < 0   pago    (recibo, débito automático acreditado)
    Saldo         lo que queda impago de ESE cargo; en los pagos es 0

La deuda del socio es la suma de ``Saldo`` de los cargos, no la suma de importes.
Y ojo con el mes que viene: el club emite por adelantado (la cuota de octubre
existe desde agosto), así que un cargo con período futuro figura impago sin que el
socio deba nada todavía. Por eso ``resumen()`` separa vencido de por vencer.
"""

from __future__ import annotations

import datetime as _dt

from django.conf import settings

from xsys.services.mssql import connect

PAGINA = 50
PAGINA_MAX = 500
MESES_DEFECTO = 6

# El "CUPON OPCIONAL PROFORMA" es el ofrecimiento de cuota social voluntaria: se
# emite todos los meses y el socio paga sólo si quiere. Queda impago para siempre
# y en la cuenta corriente se ve idéntico a un cargo real. Medido el 07/09/2026:
# 395.933 cupones de 8.026 socios por $4.379 millones, contra $4.718 millones de
# cupones de cuota de verdad. Sumarlo a la deuda haría figurar en mora a socios
# que están al día, así que se muestra aparte y con su nombre.
TIPOS_OPCIONALES = ("CPRO",)

# Paginado con ROW_NUMBER y no con OFFSET/FETCH: la base corre en nivel de
# compatibilidad 100 (SQL 2008), donde OFFSET/FETCH no existe todavía.
_SELECT = """
    SELECT Id_Trans, Fecha, Fecha_Vence, Importe, Saldo, Id_Tipo_Cbte,
           Comprobante_Nro, tipo_desc, detalle, Periodo
    FROM (
        SELECT CC.Id_Trans,
               CC.Fecha,
               CC.Fecha_Vence,
               CC.Importe,
               CC.Saldo,
               B.Id_Tipo_Cbte,
               B.Comprobante_Nro,
               T.Descripcion            AS tipo_desc,
               CC.Descripcion           AS detalle,
               B.Periodo,
               fila = ROW_NUMBER() OVER (ORDER BY CC.Fecha DESC, CC.Id_Trans DESC)
        FROM Clientes_CtaCte CC
             LEFT JOIN Cbtes B ON B.Id_Trans = CC.Id_Trans
             LEFT JOIN Cbtes_Tipos T ON T.Id_Tipo_Cbte = B.Id_Tipo_Cbte
        WHERE CC.Id_Cliente = ?
"""


def _rango(desde, hasta, meses: int = MESES_DEFECTO) -> tuple[_dt.date, _dt.date | None]:
    if desde is None:
        hoy = _dt.date.today()
        mes = hoy.month - meses
        anio = hoy.year + (mes - 1) // 12
        mes = (mes - 1) % 12 + 1
        desde = _dt.date(anio, mes, 1)
    return desde, hasta


def _fila(r) -> dict:
    importe = float(r[3] or 0)
    saldo = float(r[4] or 0)
    tipo = (r[5] or "").strip()
    concepto = (r[7] or "").strip() or (r[8] or "").strip() or tipo
    return {
        "id_trans": r[0],
        "fecha": r[1].date().isoformat() if r[1] else None,
        "vence": r[2].date().isoformat() if r[2] else None,
        "importe": importe,
        "saldo": saldo,
        "es_pago": importe < 0,
        "opcional": tipo in TIPOS_OPCIONALES,
        "tipo": tipo,
        "comprobante": f"{tipo} {r[6]}".strip() if r[6] else tipo,
        "concepto": concepto,
        "periodo": r[9].strftime("%Y-%m") if r[9] else None,
    }


def movimientos(id_cliente: int, *, desde=None, hasta=None, limit: int = PAGINA,
                offset: int = 0, solo_impagos: bool = False) -> dict:
    """Página de movimientos, del más reciente al más viejo.

    Los totales se calculan sobre TODO el filtro, no sobre la página: si el
    operador filtra los últimos seis meses, el total tiene que ser el de los seis
    meses aunque en pantalla se vean cincuenta líneas.
    """
    limit = max(1, min(int(limit or PAGINA), PAGINA_MAX))
    offset = max(0, int(offset or 0))
    desde, hasta = _rango(desde, hasta)

    where = ""
    params: list = [int(id_cliente)]
    if desde:
        where += " AND CC.Fecha >= ?"
        params.append(_dt.datetime(desde.year, desde.month, desde.day))
    if hasta:
        where += " AND CC.Fecha < ?"
        params.append(_dt.datetime(hasta.year, hasta.month, hasta.day) + _dt.timedelta(days=1))
    if solo_impagos:
        where += " AND CC.Importe > 0 AND CC.Saldo > 0"

    conn = connect(settings.MSSQL_XSYS)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*), ISNULL(SUM(CASE WHEN CC.Importe > 0 THEN CC.Importe ELSE 0 END),0), "
            f"       ISNULL(SUM(CASE WHEN CC.Importe < 0 THEN -CC.Importe ELSE 0 END),0) "
            f"FROM Clientes_CtaCte CC WHERE CC.Id_Cliente = ?{where}",
            params,
        )
        total, cargos, pagos = cur.fetchone()

        cur.execute(
            f"{_SELECT}{where}) P WHERE P.fila > ? AND P.fila <= ? ORDER BY P.fila",
            params + [offset, offset + limit],
        )
        filas = [_fila(r) for r in cur.fetchall()]
    finally:
        conn.close()

    return {
        "id_cliente": int(id_cliente),
        "desde": desde.isoformat() if desde else None,
        "hasta": hasta.isoformat() if hasta else None,
        "total": int(total or 0),
        "cargos": float(cargos or 0),
        "pagos": float(pagos or 0),
        "limit": limit,
        "offset": offset,
        "movimientos": filas,
    }


def resumen(id_cliente: int) -> dict:
    """Foto del estado de cuenta: deuda vencida, por vencer y último pago."""
    conn = connect(settings.MSSQL_XSYS)
    try:
        cur = conn.cursor()
        # El corte es el primer día del mes que viene: todo lo emitido para
        # períodos posteriores es adelanto de facturación, no mora.
        opcionales = ",".join("?" for _ in TIPOS_OPCIONALES)
        cur.execute(
            f"""
            SELECT vencido = ISNULL(SUM(CASE WHEN opcional = 0
                                              AND Fecha < DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()) + 1, 0)
                                             THEN Saldo ELSE 0 END), 0),
                   por_vencer = ISNULL(SUM(CASE WHEN opcional = 0
                                                 AND Fecha >= DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()) + 1, 0)
                                                THEN Saldo ELSE 0 END), 0),
                   opcional = ISNULL(SUM(CASE WHEN opcional = 1 THEN Saldo ELSE 0 END), 0),
                   comprobantes = SUM(CASE WHEN opcional = 0 THEN 1 ELSE 0 END)
            FROM (
                SELECT CC.Fecha, CC.Saldo,
                       opcional = CASE WHEN B.Id_Tipo_Cbte IN ({opcionales}) THEN 1 ELSE 0 END
                FROM Clientes_CtaCte CC
                     LEFT JOIN Cbtes B ON B.Id_Trans = CC.Id_Trans
                WHERE CC.Id_Cliente = ? AND CC.Importe > 0 AND CC.Saldo > 0
            ) X
            """,
            list(TIPOS_OPCIONALES) + [int(id_cliente)],
        )
        vencido, por_vencer, opcional, impagos = cur.fetchone()

        cur.execute(
            """
            SELECT TOP 1 CC.Fecha, -CC.Importe, B.Id_Tipo_Cbte, B.Comprobante_Nro
            FROM Clientes_CtaCte CC LEFT JOIN Cbtes B ON B.Id_Trans = CC.Id_Trans
            WHERE CC.Id_Cliente = ? AND CC.Importe < 0
            ORDER BY CC.Fecha DESC, CC.Id_Trans DESC
            """,
            (int(id_cliente),),
        )
        pago = cur.fetchone()
    finally:
        conn.close()

    return {
        "deuda_vencida": float(vencido or 0),
        "deuda_por_vencer": float(por_vencer or 0),
        # Cuota social voluntaria ofrecida y no abonada: no es deuda.
        "proforma_opcional": float(opcional or 0),
        "comprobantes_impagos": int(impagos or 0),
        "ultimo_pago": (
            {
                "fecha": pago[0].date().isoformat() if pago[0] else None,
                "importe": float(pago[1] or 0),
                "comprobante": f"{(pago[2] or '').strip()} {pago[3]}".strip() if pago[2] else "",
            }
            if pago
            else None
        ),
    }

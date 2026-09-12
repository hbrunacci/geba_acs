"""Datos de la pantalla de Tableros.

Cuatro tableros, tres fuentes de verdad y una decisión de fondo detrás de cada
uno. Lo que sigue explica las decisiones, porque los números de gestión que no
se pueden explicar no sirven para tomar ninguna decisión.


QUIÉN ES SOCIO Y QUIÉN NO
-------------------------
xSys no tiene un campo "es socio". Lo más cerca que hay es ``Flag_Tipo`` en
``Clientes_Tipos``, que clasifica las 58 categorías en cuatro grupos:

    P  las categorías societarias plenas: INFANTIL, CADETE, ACTIVO MAYOR,
       los VITALICIO, ACOMPAÑANTE, SOCIO HONORARIO, CONYUGUE EMPLEADO...
    S  RESIDENTE EXTERIOR y SOCIO OLIMPICO, que también son socios
    N  los que no lo son: INVITADOS, EMPLEADO, BICICLETA, ALUMNO IGSM,
       DOCENTE IGSM, NO SOCIO, VISITA, PROVEEDORES...
    -  vacío en 10 categorías, que entre todas suman 259 personas activas:
       CONCESIONARIO (194), COLEGIOS (25), SIN CATEGORIZAR (20),
       ACCESO MASTER (13), TESORERIA (3), USUARIO (2), TENIS (1), MASTER (1)

Se toma **socio = P o S**, y todo lo demás no socio. Las diez categorías sin
flag no son socios en ninguna lectura razonable —son concesionarios, colegios y
fichas de servicio del propio club—, así que caen del lado correcto igual.

Está en ``FLAGS_SOCIO`` y es lo único que hay que tocar si el club decide que,
por ejemplo, ACOMPAÑANTE no debe contar como socio.


EL PADRÓN EN EL TIEMPO
----------------------
``Clientes`` guarda el estado de HOY (``Activo``) y dos fechas
(``Fecha_Alta``, ``Fecha_Baja``); no hay historial de estados. Así que la serie
se reconstruye, y hay que elegir cómo tratar los casos sucios:

  · 752 fichas están activas y tienen ``Fecha_Baja`` cargada. Son reingresos:
    volvieron y nadie limpió la fecha de la baja anterior. Si se las contara con
    la fecha de baja a rajatabla, aparecerían como bajas que nunca volvieron y
    el último punto de la serie no coincidiría con el padrón real de hoy.
  · 3 fichas están inactivas y no tienen ``Fecha_Baja``. No se sabe cuándo se
    fueron, así que no se las puede ubicar en ninguna serie.

Regla adoptada: **una ficha activa cuenta desde su alta hasta hoy; una ficha de
baja cuenta desde su alta hasta su baja**. Con esto el último punto de la línea
es exactamente el padrón de hoy, que es contra lo que cualquiera va a verificar
el gráfico.

Además la serie se calcula hacia atrás desde el total real de hoy
(``stock(mes-1) = stock(mes) - altas(mes) + bajas(mes)``) en lugar de hacia
adelante desde cero. Da lo mismo matemáticamente, pero garantiza que el extremo
derecho del gráfico sea el número de hoy y no una suma acumulada que arrastre
cualquier suciedad de 1900.


LA DEUDA
--------
Sale de ``Clientes_CtaCte``: ``Importe > 0`` es un cargo y ``Saldo > 0`` es lo
que queda impago de ese cargo. Con dos exclusiones que no son opcionales:

  · **CPRO, "CUPON OPCIONAL PROFORMA"**: es el ofrecimiento de cuota social
    voluntaria. Se emite todos los meses, el socio paga sólo si quiere y queda
    impago para siempre. Son 395.933 cupones de 8.026 socios por $4.379
    millones. Contarlo pondría en mora a socios que están al día.
  · **el mes que viene**: el club factura por adelantado (el cupón de octubre
    existe desde agosto), así que un cupón de período futuro figura impago sin
    que nadie deba nada todavía. Se separa en ``por_vencer``.

El cálculo completo tarda entre 3 y 13 segundos, así que no se hace en vivo:
ver ``xsys.models.tablero`` para el porqué de la foto guardada.
"""

from __future__ import annotations

import datetime as _dt
import time
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from xsys.models import (
    XsysDeudaFoto,
    XsysDeudaMes,
    XsysDeudaSocio,
    XsysDeudaSocioMes,
)
from xsys.services.mssql import connect

# Categorías que cuentan como socio. Ver el encabezado del módulo.
FLAGS_SOCIO = ("P", "S")

# El cupón voluntario que no es deuda. Ver el encabezado del módulo.
TIPOS_OPCIONALES = ("CPRO",)

# --------------------------------------------------------------------------- #
# Cuota social contra actividades
# --------------------------------------------------------------------------- #
# Qué es cada deuda sale del CONTRATO que la generó, no del tipo de comprobante.
# El tipo de comprobante no alcanza: una nota de débito (ND) puede ser vóley, la
# colonia, el instituto o el recupero de un gasto, y todas se ven iguales. El
# contrato, en cambio, dice exactamente de qué es: `Cbtes.Id_Contrato` ->
# `Contratos.Id_Tipo_Con` -> `Contratos_Tipos`.
#
# Los ids se listan explícitos y no se adivinan por el nombre: hay 55 tipos de
# contrato y varios se llaman parecido sin serlo ("CUOTA AGUINALDO" es cuota
# social, "CUOTA MANUAL" también, pero "PLAN DE PAGOS CUOTAS DEPORTIVAS" es
# actividades). Un tipo nuevo que nadie mapee cae en "otros", que es el default
# seguro: aparece en el gráfico, no se pierde plata, y se nota para mapearlo.

RUBRO_CUOTA = "cuota_social"
RUBRO_ACTIVIDADES = "actividades"
RUBRO_OTROS = "otros"

RUBROS_ETIQUETA = {
    RUBRO_CUOTA: "Cuota social",
    RUBRO_ACTIVIDADES: "Actividades",
    RUBRO_OTROS: "Otros conceptos",
}

TIPOS_CON_CUOTA = (
    1,    # CUOTA SOCIAL
    2,    # CUOTA MANUAL
    18,   # CUOTA EXTRAORDINARIA
    23,   # CUOTA AGUINALDO (REMITOS)
    37,   # CUOTA SOCIAL VOLUNTARIA (PROFORMA), la parte que NO es cupón CPRO:
          # son 119 comprobantes reales, no el ofrecimiento voluntario.
)

TIPOS_CON_ACTIVIDADES = (
    3,    # ACTIVIDADES CONTRATADAS
    9,    # DEPORTES FEDERADOS
    12,   # FUTBOL INTERNO
    22,   # ACTIVIDADES ARANCELADAS
    24,   # ED GIMNASIA ARTISTICA
    25,   # ED HOCKEY S/CESPED
    26,   # ED NATACION
    27,   # ED PATIN ARTISTICO
    28,   # ED EFI
    29,   # COLONIA
    31,   # ESCUELAS DEPORTIVAS
    36,   # NATACION PANDEMIA
    38,   # HOCKEY S/RUEDAS - LED
    41,   # ACTIVIDADES BONIFICADAS
    46,   # CENTRO DE FORMACION FUTBOL
    56,   # PLAN DE PAGOS CUOTAS DEPORTIVAS
)

# Todo lo demás cae en "otros": COMODIDADES, REMITOS, CONCESIONARIOS, CARNET
# MAYOR REFERENTE, RECUPERO DE GASTOS, INSTITUTO... El INSTITUTO (tipo 55) va
# acá a propósito: es el colegio, no una actividad deportiva del club.

# Cuando el comprobante NO tiene contrato (25.953 impagos, casi todos facturas a
# concesionarios, colegios y publicidad), se cae al tipo de comprobante. Los
# cupones de cuota sin contrato son 32.033 y dicen "CUOTA SOCIAL" en el detalle,
# así que ésos sí se reconocen; el resto queda en "otros".
TIPOS_CBTE_CUOTA = ("CUP", "CUPA", "CUPZ", "CADE")


# Qué actividad es, dentro del rubro "actividades". El tipo de contrato no
# alcanza: DEPORTES FEDERADOS es el más grande de todos y mete vóley, hockey,
# rugby, básquet y waterpolo en la misma bolsa. El deporte está en el detalle del
# comprobante, con la forma "VOLEY - CADETE..CUOTA", así que se toma el texto
# ANTES del primer guion y se normaliza.
#
# El detalle se resuelve en una derivada de una fila por comprobante y recién
# ahí se une, en vez de unir Cbtes_Items directo. Son dos problemas distintos y
# los dos duplicarían plata:
#   · un comprobante puede tener varios renglones (3.662 de 51.654 los tienen)
#   · un comprobante puede tener varias filas en la cuenta corriente, una por
#     cuota, y ahí el SUM(Saldo) se multiplicaría por la cantidad de renglones
_SQL_ACTIVIDAD_ITEM = """
    SELECT I.Id_Trans, detalle = MIN(RTRIM(ISNULL(I.Descripcion_producto, '')))
    FROM Cbtes_Items I GROUP BY I.Id_Trans
"""

_ACTIVIDAD = """LTRIM(RTRIM(CASE
        WHEN CHARINDEX('-', IT.detalle) > 0
            THEN LEFT(IT.detalle, CHARINDEX('-', IT.detalle) - 1)
        ELSE ISNULL(IT.detalle, '')
    END))"""


def _sql_actividad(alias_contrato: str = "O", alias_cbte: str = "B") -> str:
    """El deporte, sólo para el rubro actividades; vacío para el resto.

    Se deja vacío fuera de actividades a propósito: si se calculara siempre,
    cada mes de cuota social se abriría en tantas filas como textos distintos
    tengan los cupones, y la tabla de la foto crecería sin que nadie mire eso.
    """
    return f"""CASE
        WHEN {_sql_rubro(alias_contrato, alias_cbte)} = '{RUBRO_ACTIVIDADES}'
            THEN {_ACTIVIDAD}
        ELSE ''
    END"""


def _sql_rubro(alias_contrato: str = "O", alias_cbte: str = "B") -> str:
    """Arma el CASE que clasifica cada comprobante, desde el mapeo de arriba.

    Se genera desde las tuplas de Python en vez de escribirlo a mano en el SQL
    para que haya un solo lugar donde mapear un tipo de contrato nuevo.
    """
    cuota = ",".join(str(i) for i in TIPOS_CON_CUOTA)
    activ = ",".join(str(i) for i in TIPOS_CON_ACTIVIDADES)
    cbte_cuota = ",".join("'%s'" % t for t in TIPOS_CBTE_CUOTA)
    return f"""CASE
        WHEN {alias_contrato}.Id_Tipo_Con IN ({cuota}) THEN '{RUBRO_CUOTA}'
        WHEN {alias_contrato}.Id_Tipo_Con IN ({activ}) THEN '{RUBRO_ACTIVIDADES}'
        WHEN {alias_contrato}.Id_Tipo_Con IS NULL
             AND {alias_cbte}.Id_Tipo_Cbte IN ({cbte_cuota}) THEN '{RUBRO_CUOTA}'
        ELSE '{RUBRO_OTROS}'
    END"""

# Fechas basura del padrón: hay altas cargadas con 1900-01-01 y cupones con
# períodos igual de viejos. Nada anterior a esto entra en ninguna serie.
PISO_HISTORICO = _dt.date(2000, 1, 1)

# Cuando el rango pedido es corto, agrupar por mes deja el gráfico con dos
# puntos. Por debajo de este umbral se agrupa por día.
DIAS_PARA_AGRUPAR_POR_DIA = 62

# Rangos rápidos del selector. El valor es (etiqueta, días hacia atrás); ``mes``
# y ``hoy`` se resuelven aparte porque no son una cantidad fija de días.
RANGOS = {
    "hoy": "Hoy",
    "semana": "Última semana",
    "mes_actual": "Este mes",
    "30d": "Últimos 30 días",
    "3m": "Últimos 3 meses",
    "6m": "Últimos 6 meses",
    "1a": "Último año",
}
RANGO_DEFECTO = "1a"


class TableroError(Exception):
    """Falló la consulta a xSys. El mensaje va tal cual a la pantalla."""


# --------------------------------------------------------------------------- #
# Rango de fechas
# --------------------------------------------------------------------------- #

def _hoy() -> _dt.date:
    return timezone.localdate()


def rango(clave: str | None = None, desde=None, hasta=None) -> dict:
    """Traduce el selector rápido a un par de fechas y una granularidad.

    ``desde``/``hasta`` explícitos ganan sobre la clave: la pantalla ofrece los
    atajos, pero el que quiere un rango puntual lo escribe.
    """
    hoy = _hoy()
    if desde or hasta:
        d = _fecha(desde) or PISO_HISTORICO
        h = _fecha(hasta) or hoy
        clave = "personalizado"
    else:
        clave = clave if clave in RANGOS else RANGO_DEFECTO
        h = hoy
        if clave == "hoy":
            d = hoy
        elif clave == "semana":
            d = hoy - _dt.timedelta(days=7)
        elif clave == "mes_actual":
            d = hoy.replace(day=1)
        elif clave == "30d":
            d = hoy - _dt.timedelta(days=30)
        elif clave == "3m":
            d = _meses_atras(hoy, 3)
        elif clave == "6m":
            d = _meses_atras(hoy, 6)
        else:
            d = _meses_atras(hoy, 12)

    if d > h:
        d, h = h, d
    if d < PISO_HISTORICO:
        d = PISO_HISTORICO
    dias = (h - d).days
    return {
        "clave": clave,
        "desde": d,
        "hasta": h,
        "granularidad": "dia" if dias <= DIAS_PARA_AGRUPAR_POR_DIA else "mes",
        "etiqueta": RANGOS.get(clave, "Personalizado"),
    }


def _fecha(v) -> _dt.date | None:
    if not v:
        return None
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _meses_atras(d: _dt.date, n: int) -> _dt.date:
    mes = d.month - n
    anio = d.year + (mes - 1) // 12
    mes = (mes - 1) % 12 + 1
    dia = min(d.day, _ultimo_dia(anio, mes))
    return _dt.date(anio, mes, dia)


def _ultimo_dia(anio: int, mes: int) -> int:
    if mes == 12:
        return 31
    return (_dt.date(anio, mes + 1, 1) - _dt.timedelta(days=1)).day


def _fin_de_mes(d: _dt.date) -> _dt.date:
    return _dt.date(d.year, d.month, _ultimo_dia(d.year, d.month))


def _periodos(desde: _dt.date, hasta: _dt.date, granularidad: str) -> list[_dt.date]:
    """Todos los períodos del rango, incluidos los que no tienen movimiento.

    Un mes sin altas tiene que aparecer con cero y no desaparecer del eje: si
    no, el gráfico miente por omisión.
    """
    if granularidad == "dia":
        n = (hasta - desde).days
        return [desde + _dt.timedelta(days=i) for i in range(n + 1)]
    out, cur = [], desde.replace(day=1)
    while cur <= hasta:
        out.append(cur)
        cur = (cur.replace(day=28) + _dt.timedelta(days=7)).replace(day=1)
    return out


def _clave(d: _dt.date, granularidad: str) -> str:
    return d.isoformat() if granularidad == "dia" else d.strftime("%Y-%m")


# --------------------------------------------------------------------------- #
# Conexión
# --------------------------------------------------------------------------- #

def _cursor():
    try:
        conn = connect(settings.MSSQL_XSYS)
    except Exception as exc:  # pragma: no cover - depende de la red
        raise TableroError(_mensaje_conexion(exc)) from exc
    conn.timeout = 0
    return conn


def _mensaje_consulta(que: str, exc) -> str:
    """Una consulta que falló ya conectada. Si el motivo es que se cortó la red
    en el medio, se dice lo mismo que cuando no se pudo ni conectar."""
    crudo = str(exc).lower()
    if "timeout" in crudo or "communication link" in crudo or "conectar" in crudo:
        return _mensaje_conexion(exc)
    return f"xSys rechazó la consulta de {que}: {str(exc)[:180]}"


def _mensaje_conexion(exc) -> str:
    """Traduce el error de ODBC a algo que sirva en pantalla.

    Lo que llegaba antes era «No se pudo conectar a xSys: No se pudo conectar a
    xSys: ('HYT00', '[HYT00] [Microsoft][ODBC Driver 18...] Login timeout
    expired')»: el prefijo repetido porque ``mssql.connect`` ya lo agrega, y
    después un código que no le dice nada a quien está mirando el tablero.

    El dato que sí sirve es que la deuda se sigue viendo: sale de la foto
    guardada en Postgres y no necesita a xSys.
    """
    crudo = str(exc)
    servidor = (settings.MSSQL_XSYS or {}).get("HOST") or "xSys"
    if "timeout" in crudo.lower():
        detalle = f"el servidor {servidor} no responde"
    elif "login failed" in crudo.lower():
        detalle = "el servidor rechazó el usuario"
    else:
        detalle = crudo.replace("No se pudo conectar a xSys: ", "")[:160]
    return (f"Sin conexión con xSys: {detalle}. El padrón, las altas y bajas y "
            f"las categorías se consultan en vivo y no se pueden mostrar. "
            f"La deuda sí, porque sale de la última foto guardada.")


# Expresión SQL reutilizada: 1 si la categoría es societaria.
_ES_SOCIO = (
    "CASE WHEN RTRIM(ISNULL(T.Flag_Tipo,'')) IN ('%s') THEN 1 ELSE 0 END"
    % "','".join(FLAGS_SOCIO)
)


# --------------------------------------------------------------------------- #
# 1. Padrón: cuántos hay y cómo se movió
# --------------------------------------------------------------------------- #

def padron(clave: str | None = None, desde=None, hasta=None) -> dict:
    """Stock de activos al cierre de cada período y variación neta del período.

    Devuelve, para socios y para no socios, la serie de cuántos había y cuántos
    entraron y salieron. La serie se arma hacia atrás desde el total de hoy; ver
    el encabezado del módulo.
    """
    r = rango(clave, desde, hasta)
    gran = r["granularidad"]
    conn = _cursor()
    try:
        cur = conn.cursor()
        # Total de hoy, que es el ancla de toda la serie.
        cur.execute(f"""
            SELECT es_socio = {_ES_SOCIO}, n = COUNT(*)
            FROM Clientes C LEFT JOIN Clientes_Tipos T ON T.Id_Tipo_Cli = C.Id_Tipo_Cli
            WHERE ISNULL(C.Activo, 0) = 1
            GROUP BY {_ES_SOCIO}
        """)
        hoy_por_clase = {bool(f[0]): int(f[1]) for f in cur.fetchall()}

        # Altas y bajas de todo el período, agrupadas. La baja sólo cuenta si la
        # ficha sigue de baja: las 752 reingresadas ya volvieron.
        corte = min(r["desde"], PISO_HISTORICO)
        cur.execute(f"""
            SELECT clase = X.clase, es_socio = X.es_socio,
                   f = CONVERT(CHAR(10), X.f, 120), n = COUNT(*)
            FROM (
                SELECT clase = 'alta', es_socio = {_ES_SOCIO},
                       f = CAST(C.Fecha_Alta AS DATE)
                FROM Clientes C LEFT JOIN Clientes_Tipos T ON T.Id_Tipo_Cli = C.Id_Tipo_Cli
                WHERE C.Fecha_Alta IS NOT NULL AND C.Fecha_Alta >= ?
                UNION ALL
                SELECT 'baja', {_ES_SOCIO}, CAST(C.Fecha_Baja AS DATE)
                FROM Clientes C LEFT JOIN Clientes_Tipos T ON T.Id_Tipo_Cli = C.Id_Tipo_Cli
                WHERE C.Fecha_Baja IS NOT NULL AND C.Fecha_Baja >= ?
                  AND ISNULL(C.Activo, 0) = 0
            ) X
            GROUP BY X.clase, X.es_socio, CONVERT(CHAR(10), X.f, 120)
        """, (corte, corte))
        crudo = cur.fetchall()
    except TableroError:
        raise
    except Exception as exc:
        raise TableroError(_mensaje_consulta("padrón", exc)) from exc
    finally:
        conn.close()

    periodos = _periodos(r["desde"], r["hasta"], gran)
    claves = [_clave(p, gran) for p in periodos]
    indice = {k: i for i, k in enumerate(claves)}

    series = {}
    for es_socio in (True, False):
        altas = [0] * len(claves)
        bajas = [0] * len(claves)
        # Movimientos POSTERIORES al rango: hacen falta para desandar desde hoy.
        post_altas = post_bajas = 0
        for clase, flag, fecha_txt, n in crudo:
            if bool(flag) != es_socio:
                continue
            f = _dt.date.fromisoformat(fecha_txt)
            n = int(n)
            if f > r["hasta"]:
                if clase.strip() == "alta":
                    post_altas += n
                else:
                    post_bajas += n
                continue
            k = _clave(f, gran)
            i = indice.get(k)
            if i is None:
                continue
            if clase.strip() == "alta":
                altas[i] += n
            else:
                bajas[i] += n

        # Stock al cierre del último período = padrón de hoy menos lo que pasó
        # después del rango.
        stock_final = hoy_por_clase.get(es_socio, 0) - post_altas + post_bajas
        stock = [0] * len(claves)
        acum = stock_final
        for i in range(len(claves) - 1, -1, -1):
            stock[i] = acum
            acum = acum - altas[i] + bajas[i]

        series["socios" if es_socio else "no_socios"] = {
            "stock": stock,
            "altas": altas,
            "bajas": bajas,
            "neto": [a - b for a, b in zip(altas, bajas)],
            "hoy": hoy_por_clase.get(es_socio, 0),
        }

    return {
        "rango": _rango_dict(r),
        "etiquetas": claves,
        "series": series,
        "totales": {
            "socios": hoy_por_clase.get(True, 0),
            "no_socios": hoy_por_clase.get(False, 0),
            "total": sum(hoy_por_clase.values()),
        },
    }


# --------------------------------------------------------------------------- #
# 2. Altas y bajas
# --------------------------------------------------------------------------- #

def altas_bajas(clave: str | None = None, desde=None, hasta=None,
                tipo: str = "todos") -> dict:
    """Altas y bajas del rango. ``tipo``: ``socios``, ``no_socios`` o ``todos``.

    Reusa el cálculo del padrón porque sale de la misma consulta: pedirle a
    xSys dos veces lo mismo para mostrarlo de dos formas sería tonto.
    """
    base = padron(clave, desde, hasta)
    if tipo not in ("socios", "no_socios", "todos"):
        tipo = "todos"

    if tipo == "todos":
        s, n = base["series"]["socios"], base["series"]["no_socios"]
        altas = [a + b for a, b in zip(s["altas"], n["altas"])]
        bajas = [a + b for a, b in zip(s["bajas"], n["bajas"])]
    else:
        altas = base["series"][tipo]["altas"]
        bajas = base["series"][tipo]["bajas"]

    return {
        "rango": base["rango"],
        "tipo": tipo,
        "etiquetas": base["etiquetas"],
        "altas": altas,
        "bajas": bajas,
        "neto": [a - b for a, b in zip(altas, bajas)],
        "totales": {
            "altas": sum(altas),
            "bajas": sum(bajas),
            "neto": sum(altas) - sum(bajas),
        },
    }


# --------------------------------------------------------------------------- #
# 3. Categorías activas
# --------------------------------------------------------------------------- #

def categorias(umbral: float = 1.0, ids: list[int] | None = None,
               solo: str = "todos") -> dict:
    """Reparto del padrón activo por categoría, con las chicas en una torta aparte.

    El problema que resuelve: hay 34 categorías con gente activa y 20 de ellas
    no llegan al 1%. En una sola torta quedan como rayas ilegibles pegadas al
    borde. Se devuelven dos tortas, la grande y la de las chicas, cada una con
    sus porcentajes sobre el total general para que los números sigan cerrando.

    ``umbral`` es el porcentaje por debajo del cual una categoría se manda a la
    torta chica; ``ids`` limita a un subconjunto (por defecto, todas).
    """
    conn = _cursor()
    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT C.Id_Tipo_Cli,
                   categoria = RTRIM(ISNULL(T.Descripcion, '')),
                   es_socio = {_ES_SOCIO},
                   n = COUNT(*)
            FROM Clientes C LEFT JOIN Clientes_Tipos T ON T.Id_Tipo_Cli = C.Id_Tipo_Cli
            WHERE ISNULL(C.Activo, 0) = 1
            GROUP BY C.Id_Tipo_Cli, T.Descripcion, {_ES_SOCIO}
            HAVING COUNT(*) > 0
            ORDER BY COUNT(*) DESC
        """)
        filas = cur.fetchall()
    except Exception as exc:
        raise TableroError(_mensaje_consulta("categorías", exc)) from exc
    finally:
        conn.close()

    catalogo = [
        {
            "id": int(f[0]) if f[0] is not None else None,
            "categoria": (f[1] or "").strip() or "(sin categoría)",
            "es_socio": bool(f[2]),
            "n": int(f[3]),
        }
        for f in filas
    ]

    datos = catalogo
    if solo == "socios":
        datos = [c for c in datos if c["es_socio"]]
    elif solo == "no_socios":
        datos = [c for c in datos if not c["es_socio"]]
    if ids:
        pedidos = {int(i) for i in ids}
        datos = [c for c in datos if c["id"] in pedidos]

    total = sum(c["n"] for c in datos) or 1
    for c in datos:
        c["porcentaje"] = round(c["n"] * 100.0 / total, 2)

    grandes = [c for c in datos if c["porcentaje"] >= umbral]
    chicas = [c for c in datos if c["porcentaje"] < umbral]

    # La torta grande lleva una porción "Otras" que representa a las chicas, para
    # que las dos tortas sumen el 100% y se vea de dónde sale la segunda.
    otras = sum(c["n"] for c in chicas)
    if otras:
        grandes = grandes + [{
            "id": None,
            "categoria": f"Otras ({len(chicas)})",
            "es_socio": None,
            "n": otras,
            "porcentaje": round(otras * 100.0 / total, 2),
            "agrupada": True,
        }]

    return {
        "umbral": umbral,
        "solo": solo,
        "total": sum(c["n"] for c in datos),
        "principal": grandes,
        "chicas": chicas,
        "catalogo": [
            {"id": c["id"], "categoria": c["categoria"],
             "n": c["n"], "es_socio": c["es_socio"]}
            for c in catalogo
        ],
    }


# --------------------------------------------------------------------------- #
# 4. Deuda
# --------------------------------------------------------------------------- #

_OPCIONALES_SQL = ",".join("'%s'" % t for t in TIPOS_OPCIONALES)

# El JOIN con Contratos es LEFT y con la condición adentro del ON: hay
# comprobantes con Id_Contrato = 0, y si la condición fuera al WHERE se caerían
# los 25.953 impagos sin contrato en vez de clasificarse como "otros".
_DESDE_DEUDA = f"""
FROM    Clientes_CtaCte CC
        JOIN Cbtes B     ON B.Id_Trans = CC.Id_Trans
        JOIN Clientes C  ON C.Id_Cliente = CC.Id_Cliente
        LEFT JOIN Clientes_Tipos T ON T.Id_Tipo_Cli = C.Id_Tipo_Cli
        LEFT JOIN Contratos O ON O.Id_Contrato = B.Id_Contrato AND B.Id_Contrato > 0
        LEFT JOIN ({_SQL_ACTIVIDAD_ITEM}) IT ON IT.Id_Trans = B.Id_Trans
WHERE   CC.Importe > 0 AND CC.Saldo > 0
  AND   B.Id_Tipo_Cbte NOT IN ({_OPCIONALES_SQL})
  AND   CC.Fecha >= ?
"""

# Una fila por socio Y rubro; después se pivotea en Python a una fila por socio
# con la plata abierta en columnas. Se hace así y no con tres subconsultas para
# recorrer la cuenta corriente una sola vez.
_SQL_DEUDA_POR_SOCIO = f"""
SELECT  CC.Id_Cliente,
        nro_socio  = RTRIM(ISNULL(C.Id_Cliente_Externo, '')),
        doc_nro    = C.Doc_Nro,
        apellido   = RTRIM(ISNULL(C.Apellido, '')),
        nombre     = RTRIM(ISNULL(C.Nombre, '')),
        id_tipo_cli = C.Id_Tipo_Cli,
        categoria  = RTRIM(ISNULL(T.Descripcion, '')),
        es_socio   = {_ES_SOCIO},
        activo     = ISNULL(C.Activo, 0),
        rubro      = {_sql_rubro()},
        actividad  = {_sql_actividad()},
        cuotas     = COUNT(*),
        importe    = SUM(CC.Saldo),
        mes_viejo  = MIN(CC.Fecha),
        mes_nuevo  = MAX(CC.Fecha)
{_DESDE_DEUDA}
GROUP BY CC.Id_Cliente, C.Id_Cliente_Externo, C.Doc_Nro, C.Apellido, C.Nombre,
         C.Id_Tipo_Cli, T.Descripcion, {_ES_SOCIO}, ISNULL(C.Activo, 0),
         {_sql_rubro()}, {_sql_actividad()}
"""

_SQL_DEUDA_SOCIO_MES = f"""
SELECT  CC.Id_Cliente,
        mes       = DATEADD(MONTH, DATEDIFF(MONTH, 0, CC.Fecha), 0),
        rubro     = {_sql_rubro()},
        actividad = {_sql_actividad()},
        cuotas    = COUNT(*),
        importe   = SUM(CC.Saldo)
{_DESDE_DEUDA}
GROUP BY CC.Id_Cliente, DATEADD(MONTH, DATEDIFF(MONTH, 0, CC.Fecha), 0),
         {_sql_rubro()}, {_sql_actividad()}
"""

_SQL_DEUDA_POR_MES = f"""
SELECT  mes = DATEADD(MONTH, DATEDIFF(MONTH, 0, CC.Fecha), 0),
        id_tipo_cli = C.Id_Tipo_Cli,
        categoria = RTRIM(ISNULL(T.Descripcion, '')),
        es_socio  = {_ES_SOCIO},
        activo    = ISNULL(C.Activo, 0),
        rubro     = {_sql_rubro()},
        actividad = {_sql_actividad()},
        socios    = COUNT(DISTINCT CC.Id_Cliente),
        cupones   = COUNT(*),
        importe   = SUM(CC.Saldo)
{_DESDE_DEUDA}
GROUP BY DATEADD(MONTH, DATEDIFF(MONTH, 0, CC.Fecha), 0), C.Id_Tipo_Cli,
         T.Descripcion, {_ES_SOCIO}, ISNULL(C.Activo, 0), {_sql_rubro()},
         {_sql_actividad()}
"""


def recalcular_deuda(usuario: str = "") -> XsysDeudaFoto:
    """Recorre la cuenta corriente entera y guarda una foto nueva.

    Es la única operación cara del tablero (3 a 13 segundos). Si falla, guarda
    igual la cabecera con el error y deja intacta la última foto buena: la
    pantalla sigue mostrando el número viejo con su fecha, que es más útil que
    una pantalla en blanco.
    """
    t0 = time.time()
    foto = XsysDeudaFoto.objects.create(usuario=usuario or "", ok=False)
    try:
        conn = _cursor()
        try:
            cur = conn.cursor()
            cur.execute(_SQL_DEUDA_POR_SOCIO, (PISO_HISTORICO,))
            por_socio = cur.fetchall()
            cur.execute(_SQL_DEUDA_POR_MES, (PISO_HISTORICO,))
            por_mes = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:
        foto.error = str(exc)[:300]
        foto.duracion_ms = int((time.time() - t0) * 1000)
        foto.save(update_fields=["error", "duracion_ms"])
        raise TableroError(f"No se pudo calcular la deuda: {exc}") from exc

    filas_socio = _pivotar_por_socio(foto, por_socio)

    with transaction.atomic():
        XsysDeudaSocio.objects.bulk_create(filas_socio, batch_size=2000)
        _guardar_detalle_por_mes(foto, filas_socio)

        XsysDeudaMes.objects.bulk_create([
            XsysDeudaMes(
                foto=foto,
                mes=f[0].date() if hasattr(f[0], "date") else f[0],
                id_tipo_cli=int(f[1]) if f[1] is not None else None,
                categoria=(f[2] or "").strip()[:100] or "(sin categoría)",
                es_socio=bool(f[3]),
                activo=bool(f[4]),
                rubro=(f[5] or RUBRO_OTROS).strip(),
                actividad=(f[6] or "").strip()[:60],
                socios=int(f[7]),
                cupones=int(f[8]),
                importe=Decimal(f[9] or 0),
            )
            for f in por_mes
        ], batch_size=2000)

        foto.ok = True
        foto.duracion_ms = int((time.time() - t0) * 1000)
        foto.socios = len(filas_socio)
        foto.cupones = sum(int(f[8]) for f in por_mes)
        foto.importe = sum((x.importe for x in filas_socio), Decimal(0))
        foto.save()

    _limpiar_fotos_viejas()
    return foto


# Cuánto puede durar un cálculo antes de darlo por muerto. El real ronda los 4
# minutos; pasado este plazo se asume que el proceso que lo lanzó se cayó —el
# worker de gunicorn se recicló, el contenedor se reinició— y se permite arrancar
# otro. Sin esto, una foto que quedó a medias bloquearía el botón para siempre.
MINUTOS_CALCULO_MUERTO = 20

# Lo que tarda de verdad, para poder avisarlo en pantalla sin que el número salga
# de la nada. Se mide contra la última foto buena y se cae a esto si no hay.
SEGUNDOS_ESTIMADOS = 300


def foto_en_curso() -> XsysDeudaFoto | None:
    """La foto que se está calculando ahora mismo, si hay alguna.

    Una foto arranca con ``ok=False`` y sin error, y termina con ``ok=True`` o
    con el error cargado. O sea que "en curso" no necesita un campo nuevo: es una
    foto joven que todavía no es ninguna de las dos cosas.
    """
    corte = timezone.now() - _dt.timedelta(minutes=MINUTOS_CALCULO_MUERTO)
    return (XsysDeudaFoto.objects
            .filter(ok=False, error="", calculado_at__gte=corte)
            .order_by("-calculado_at").first())


def estado_calculo() -> dict:
    """Para la pantalla: si hay un cálculo corriendo y cuánto suele tardar."""
    en_curso = foto_en_curso()
    ultima = XsysDeudaFoto.ultima_buena()

    # El estimado sale de corridas reales y no de una constante: la base crece y
    # el número de la pantalla tiene que seguir siendo cierto. Se toma la MÁS
    # LENTA de las guardadas, no la última: entre dos corridas del mismo día hubo
    # 73 s y 229 s según cómo estuviera xSys, y un aviso que promete dos minutos
    # y tarda cuatro es peor que no avisar nada.
    duraciones = [f.duracion_ms for f in
                  XsysDeudaFoto.objects.filter(ok=True, duracion_ms__gt=0)
                  .order_by("-calculado_at")[:5]]
    estimado = max(int(max(duraciones) / 1000), 60) if duraciones else SEGUNDOS_ESTIMADOS

    fallida = None
    if not en_curso:
        candidata = XsysDeudaFoto.objects.order_by("-calculado_at").first()
        if candidata is not None and not candidata.ok and candidata.error:
            fallida = candidata

    return {
        "en_curso": en_curso is not None,
        "arrancado_at": en_curso.calculado_at.isoformat() if en_curso else None,
        "corriendo_hace_s": int((timezone.now() - en_curso.calculado_at).total_seconds())
                            if en_curso else 0,
        "estimado_s": estimado,
        "error": fallida.error if fallida else "",
        "ultima": {
            "calculado_at": ultima.calculado_at.isoformat(),
            "duracion_ms": ultima.duracion_ms,
            "socios": ultima.socios,
            "importe": float(ultima.importe),
        } if ultima else None,
    }


def lanzar_recalculo(usuario: str = "") -> dict:
    """Arranca el recálculo en segundo plano y vuelve enseguida.

    Se hace con un hilo y no con una cola de tareas porque el proyecto no tiene
    ninguna —no hay Celery ni broker— y montar una para un trabajo que corre
    cuatro veces por día sería más pieza que se puede romper que problema
    resuelto. El caso normal igual no pasa por acá: lo dispara el contenedor
    ``deuda-foto`` a las horas fijas. Este camino es el botón "Actualizar", que
    se usa cuando alguien necesita el número fresco antes de la próxima corrida.

    El hilo cierra su conexión a Postgres al terminar: Django abre una por hilo y
    si no se cierra queda colgada hasta que el worker se recicle.
    """
    import threading

    from django.db import connection

    ya = foto_en_curso()
    if ya is not None:
        return {"arrancado": False, "motivo": "ya_en_curso", **estado_calculo()}

    def trabajar():
        try:
            recalcular_deuda(usuario=usuario)
        except Exception:  # pragma: no cover - queda asentado en la foto
            pass
        finally:
            connection.close()

    hilo = threading.Thread(target=trabajar, name="recalculo-deuda", daemon=True)
    hilo.start()
    # Un respiro para que el hilo alcance a crear la fila antes de contestar: si
    # no, la pantalla pregunta el estado y todavía no hay nada en curso.
    time.sleep(0.4)
    return {"arrancado": True, **estado_calculo()}


def _guardar_detalle_por_mes(foto, filas_socio) -> int:
    """Trae el detalle fino de xSys y lo guarda de a tandas.

    Son ~313.000 filas. Se leen con ``fetchmany`` y se van insertando por lotes
    en vez de traerlas todas a memoria: el servidor tiene 3,7 GB para todo —nueve
    contenedores más Postgres— y ya se cayó una vez por presión de memoria.
    Sostener un cuarto de millón de tuplas mientras se arman otros tantos objetos
    de Django es exactamente cómo se vuelve a caer.
    """
    pk_por_cliente = {f.id_cliente: f.pk for f in filas_socio}
    conn = _cursor()
    guardadas = 0
    try:
        cur = conn.cursor()
        cur.execute(_SQL_DEUDA_SOCIO_MES, (PISO_HISTORICO,))
        while True:
            tanda = cur.fetchmany(5000)
            if not tanda:
                break
            objetos = []
            for f in tanda:
                pk = pk_por_cliente.get(int(f[0]))
                if pk is None:
                    # No debería pasar: las dos consultas salen del mismo filtro.
                    # Si pasa, se saltea en vez de romper la foto entera.
                    continue
                objetos.append(XsysDeudaSocioMes(
                    socio_id=pk,
                    mes=f[1].date() if hasattr(f[1], "date") else f[1],
                    rubro=(f[2] or RUBRO_OTROS).strip(),
                    actividad=(f[3] or "").strip()[:60],
                    cuotas=int(f[4]),
                    importe=Decimal(f[5] or 0),
                ))
            XsysDeudaSocioMes.objects.bulk_create(objetos, batch_size=2000)
            guardadas += len(objetos)
    finally:
        conn.close()
    return guardadas


def _pivotar_por_socio(foto, filas) -> list[XsysDeudaSocio]:
    """De una fila por (socio, rubro, actividad) a una fila por socio.

    El Excel es un listado de personas: una fila por persona y las columnas
    diciendo cuánto de esa deuda es cuota social y cuánto actividades. Si se
    dejara una fila por rubro, la misma persona aparecería hasta tres veces y
    cualquiera que filtrara la planilla contaría morosos de más.

    El detalle fino que alimenta el modal NO sale de acá: son 313.000 filas y se
    guardan aparte, por tandas, en ``_guardar_detalle_por_mes``.
    """
    acc: dict[int, XsysDeudaSocio] = {}
    for f in filas:
        cid = int(f[0])
        rubro = (f[9] or RUBRO_OTROS).strip()
        actividad = (f[10] or "").strip()
        cuotas = int(f[11])
        importe = Decimal(f[12] or 0)
        viejo = f[13].date().replace(day=1) if f[13] else None
        nuevo = f[14].date().replace(day=1) if f[14] else None

        fila = acc.get(cid)
        if fila is None:
            fila = XsysDeudaSocio(
                foto=foto,
                id_cliente=cid,
                nro_socio=(f[1] or "").strip()[:14],
                doc_nro=int(f[2]) if f[2] is not None else None,
                apellido=(f[3] or "").strip()[:100],
                nombre=(f[4] or "").strip()[:100],
                id_tipo_cli=int(f[5]) if f[5] is not None else None,
                categoria=(f[6] or "").strip()[:100] or "(sin categoría)",
                es_socio=bool(f[7]),
                activo=bool(f[8]),
                cuotas=0,
                importe=Decimal(0),
                importe_cuota_social=Decimal(0),
                importe_actividades=Decimal(0),
                importe_otros=Decimal(0),
                cuotas_cuota_social=0,
                cuotas_actividades=0,
                mes_mas_viejo=viejo,
                mes_mas_nuevo=nuevo,
            )
            acc[cid] = fila

        fila.cuotas += cuotas
        fila.importe += importe
        if rubro == RUBRO_CUOTA:
            fila.importe_cuota_social += importe
            fila.cuotas_cuota_social += cuotas
        elif rubro == RUBRO_ACTIVIDADES:
            fila.importe_actividades += importe
            fila.cuotas_actividades += cuotas
        else:
            fila.importe_otros += importe

        if viejo and (fila.mes_mas_viejo is None or viejo < fila.mes_mas_viejo):
            fila.mes_mas_viejo = viejo
        if nuevo and (fila.mes_mas_nuevo is None or nuevo > fila.mes_mas_nuevo):
            fila.mes_mas_nuevo = nuevo

    return list(acc.values())


def _limpiar_fotos_viejas(conservar: int = 2, dias_fallidas: int = 7) -> int:
    """Deja las últimas fotos buenas y descarta el resto.

    Cada foto son ~360.000 filas entre las tres tablas de detalle, así que
    acumularlas sin control haría crecer la base para nada: sirven la última
    —que es la que se muestra— y la anterior, para poder comparar contra ayer. Las
    fallidas se guardan unos días porque su único contenido útil es el mensaje
    de error, y sirve para entender por qué la foto quedó vieja.
    """
    conservadas = list(
        XsysDeudaFoto.objects.filter(ok=True).order_by("-calculado_at")
        .values_list("id", flat=True)[:conservar]
    )
    corte = timezone.now() - _dt.timedelta(days=dias_fallidas)
    borrables = XsysDeudaFoto.objects.exclude(id__in=conservadas).filter(
        calculado_at__lt=corte
    )
    n, _ = borrables.delete()
    return n


def deuda(clave: str | None = None, desde=None, hasta=None,
          categorias_ids: list[int] | None = None, cuotas_min: int = 0,
          solo: str = "socios_activos") -> dict:
    """Lee la última foto buena y la devuelve agrupada por mes.

    ``solo`` acota la población: ``socios_activos`` (por defecto, que es lo que
    el club entiende por "la deuda"), ``socios``, ``activos`` o ``todos``.

    LOS GRÁFICOS SON SIEMPRE DE SOCIOS ACTIVOS. Es una decisión del club, no una
    opción: "la deuda" del club es lo que le deben los socios que están adentro.
    La foto igual guarda a todo el mundo —hacen falta los no socios y los de baja
    para los reportes que los piden— pero eso sale por el listado, no por el
    tablero. Por eso ``solo`` acota SÓLO la población del Excel y no las barras:
    si moviera las barras, dos personas mirando la misma pantalla con distinto
    desplegable discutirían números distintos como si fueran "la deuda".

    Devuelve dos bloques que NO son el mismo recorte, y la pantalla los muestra
    separados a propósito:

    ``totales``    lo que suman las barras: importe y comprobantes impagos de los
                   meses del rango, de socios activos. Son sumas exactas.
    ``poblacion``  cuánta gente hay detrás, sobre la foto ENTERA y no sólo sobre
                   los meses del rango, con el recorte de ``solo`` y el mínimo de
                   cuotas. Es exactamente lo que va a salir en el Excel.

    Mezclarlos sería el error fácil: contar personas sumando el total de cada
    mes duplica a quien debe cuotas de varios meses, y la cuenta daría el doble
    de socios morosos que los que hay. Por eso las personas se cuentan siempre
    contra la tabla por socio, donde cada uno aparece una sola vez.

    ``cuotas_min`` tampoco afecta al gráfico —el desglose por mes no sabe cuántas
    cuotas debe cada persona— y sí a la población y al Excel. La pantalla rotula
    los dos controles como filtros del listado por eso mismo.
    """
    r = rango(clave, desde, hasta)
    foto = XsysDeudaFoto.ultima_buena()
    if foto is None:
        return {
            "sin_foto": True,
            "rango": _rango_dict(r),
            "etiquetas": [], "importe": [], "cupones": [],
            "rubros": [], "actividades": [],
            "totales": {"importe": 0.0, "cupones": 0},
            "poblacion": {"socios": 0, "cupones": 0, "importe": 0.0},
            "distribucion_cuotas": [],
            "foto": None,
        }

    # Las barras, siempre socios activos. Ver el docstring.
    qs = XsysDeudaMes.objects.filter(foto=foto, mes__gte=r["desde"], mes__lte=r["hasta"],
                                     es_socio=True, activo=True)
    if categorias_ids:
        qs = qs.filter(id_tipo_cli__in=[int(i) for i in categorias_ids])

    etiquetas = [_clave(p, "mes") for p in _periodos(r["desde"], r["hasta"], "mes")]
    indice = {k: i for i, k in enumerate(etiquetas)}

    importe = [0.0] * len(etiquetas)
    cupones = [0] * len(etiquetas)
    por_rubro: dict[str, list] = {}
    por_actividad: dict[str, dict] = {}
    for m in qs.values("mes", "importe", "cupones", "rubro", "actividad"):
        i = indice.get(m["mes"].strftime("%Y-%m"))
        if i is None:
            continue
        v = float(m["importe"] or 0)
        importe[i] += v
        cupones[i] += m["cupones"]
        rubro = m["rubro"] or RUBRO_OTROS
        serie = por_rubro.setdefault(rubro, [0.0] * len(etiquetas))
        serie[i] += v
        if rubro == RUBRO_ACTIVIDADES:
            nombre = (m["actividad"] or "").strip() or "(sin detalle)"
            acc = por_actividad.setdefault(
                nombre, {"actividad": nombre, "importe": 0.0, "cupones": 0,
                         "serie": [0.0] * len(etiquetas)})
            acc["importe"] += v
            acc["cupones"] += m["cupones"]
            acc["serie"][i] += v

    # Orden fijo, y siempre los tres: un rubro sin deuda tiene que verse en cero
    # y no desaparecer de la leyenda, si no el gráfico cambia de forma según el
    # mes y no se puede comparar.
    rubros = [
        {"clave": k, "etiqueta": RUBROS_ETIQUETA[k],
         "importe": por_rubro.get(k, [0.0] * len(etiquetas)),
         "total": sum(por_rubro.get(k, [0.0]))}
        for k in (RUBRO_CUOTA, RUBRO_ACTIVIDADES, RUBRO_OTROS)
    ]
    for r_ in rubros:
        r_["total"] = float(sum(r_["importe"]))

    return {
        "sin_foto": False,
        "rango": _rango_dict(r),
        "etiquetas": etiquetas,
        "importe": importe,
        "cupones": cupones,
        "rubros": rubros,
        "actividades": _ranking_actividades(por_actividad, len(etiquetas)),
        "totales": {
            "importe": float(sum(Decimal(str(i)) for i in importe)),
            "cupones": sum(cupones),
        },
        "poblacion": _totales_socios(foto, categorias_ids, cuotas_min, solo),
        # Sin ``cuotas_min``: la tabla de distribución es justamente la que deja
        # elegir el mínimo con criterio, así que tiene que mostrarlo todo.
        "distribucion_cuotas": _distribucion_cuotas(foto, categorias_ids, solo),
        "foto": {
            "calculado_at": foto.calculado_at.isoformat(),
            "duracion_ms": foto.duracion_ms,
            "usuario": foto.usuario,
            "socios": foto.socios,
            "importe": float(foto.importe),
        },
    }


# Tramos de antigüedad, en meses cumplidos desde el comprobante impago más viejo.
# El corte en 3 no es arbitrario: es donde el club empieza a aplicar la deuda de
# actividades como bloqueo de acceso (ver ``xsys.models.deuda_actividades``).
TRAMOS_ANTIGUEDAD = (
    (0, 1, "Hasta 1 mes"),
    (1, 3, "1 a 3 meses"),
    (3, 6, "3 a 6 meses"),
    (6, 12, "6 a 12 meses"),
    (12, None, "Más de 1 año"),
)


def _meses_desde(mes, hoy) -> int:
    if mes is None:
        return 0
    return max(0, (hoy.year - mes.year) * 12 + (hoy.month - mes.month))


def _tramo(meses: int) -> str:
    for desde, hasta, etiqueta in TRAMOS_ANTIGUEDAD:
        if meses >= desde and (hasta is None or meses < hasta):
            return etiqueta
    return TRAMOS_ANTIGUEDAD[-1][2]


# Por qué campos se puede ordenar el listado del modal. Se ordena en el servidor
# y no en el navegador a propósito: la pantalla muestra las primeras 200 filas de
# un conjunto que puede tener miles, y ordenar sólo esas 200 daría un "mayor
# deudor" que no es el mayor deudor. Se reordena el conjunto entero y se vuelve
# a recortar.
ORDENES_DETALLE = {
    "nombre": lambda r: r["nombre"].upper(),
    "categoria": lambda r: r["categoria"].upper(),
    "cuotas": lambda r: r["cuotas"],
    "importe": lambda r: r["importe"],
    "meses": lambda r: r["meses"],
    "nro_socio": lambda r: r["nro_socio"],
}
ORDEN_DETALLE_DEFECTO = "importe"


def detalle_actividad(actividad: str = "", rubro: str = "", *,
                      clave_rango: str | None = None, desde=None, hasta=None,
                      categorias_ids: list[int] | None = None,
                      orden: str = ORDEN_DETALLE_DEFECTO, desc: bool = True,
                      limit: int = 200, offset: int = 0) -> dict:
    """Quiénes deben una actividad (o un rubro entero) y desde cuándo.

    Es lo que abre el modal al tocar una barra del desglose. Sale de la MISMA
    foto que el gráfico, no de una consulta nueva a xSys: si se consultara en
    vivo, el detalle y el gráfico que se acaba de tocar podrían no coincidir y
    no habría forma de saber cuál de los dos mirar.

    Respeta el período que tenga puesto la pantalla, igual que las barras: si el
    filtro dice "últimos 3 meses", el modal lista lo que se debe de esos tres
    meses y no la mora histórica completa. Y siempre socios activos, igual que
    los gráficos.
    """
    foto = XsysDeudaFoto.ultima_buena()
    if foto is None:
        return {"sin_foto": True, "actividad": actividad, "rubro": rubro,
                "titulo": actividad or RUBROS_ETIQUETA.get(rubro, "Deuda"),
                "socios": [], "total": 0, "importe": 0.0, "cuotas": 0,
                "antiguedad": [], "orden": orden, "desc": bool(desc),
                "rango": _rango_dict(rango(clave_rango, desde, hasta)),
                "limit": limit, "offset": offset, "foto": None}

    from django.db.models import Max, Min, Sum

    hoy = _hoy()
    # `rng` y no `r`: más abajo hay un `for r in registros` que pisaba la
    # variable y hacía estallar el armado de la respuesta.
    rng = rango(clave_rango, desde, hasta)
    base = XsysDeudaSocio.objects.filter(foto=foto, es_socio=True, activo=True)
    if categorias_ids:
        base = base.filter(id_tipo_cli__in=[int(i) for i in categorias_ids])

    # El mismo recorte de meses que las barras. Sin esto el modal contestaba con
    # la deuda histórica completa y no coincidía con la barra que se acababa de
    # tocar: se tocaba un mes con $8 M y el detalle sumaba $93 M.
    filas = XsysDeudaSocioMes.objects.filter(
        socio__in=base, mes__gte=rng["desde"], mes__lte=rng["hasta"])
    if actividad:
        filas = filas.filter(actividad=actividad)
        titulo = actividad
    elif rubro in RUBROS_ETIQUETA:
        filas = filas.filter(rubro=rubro)
        titulo = RUBROS_ETIQUETA[rubro]
    else:
        titulo = "Deuda"

    # Una fila por persona: se suman sus meses dentro del rango. ``desde`` es su
    # comprobante impago más viejo DENTRO del rango, que es de lo que habla el
    # gráfico; su mora anterior al rango no entra, igual que no entra en la barra.
    agregados = (filas.values("socio_id")
                 .annotate(cuotas=Sum("cuotas"), importe=Sum("importe"),
                           viejo=Min("mes"), nuevo=Max("mes")))

    datos_socio = {
        s.pk: s for s in XsysDeudaSocio.objects.filter(
            pk__in=[a["socio_id"] for a in agregados])
    }
    registros = []
    for a in agregados:
        s = datos_socio.get(a["socio_id"])
        if s is None:
            continue
        registros.append({
            "id_cliente": s.id_cliente,
            "nro_socio": s.nro_socio,
            "doc_nro": s.doc_nro,
            "nombre": f"{s.apellido}, {s.nombre}".strip(", "),
            "categoria": s.categoria,
            "cuotas": a["cuotas"] or 0,
            "importe": float(a["importe"] or 0),
            "desde": a["viejo"].isoformat() if a["viejo"] else None,
            "meses": _meses_desde(a["viejo"], hoy),
        })

    for r in registros:
        r["antiguedad"] = _tramo(r["meses"])

    clave = orden if orden in ORDENES_DETALLE else ORDEN_DETALLE_DEFECTO
    registros.sort(key=ORDENES_DETALLE[clave], reverse=bool(desc))

    # El resumen de antigüedad se arma sobre TODOS los registros, no sobre la
    # página: el modal muestra 200 filas pero el resumen tiene que hablar del
    # total, si no engaña.
    resumen = []
    for _d, _h, etiqueta in TRAMOS_ANTIGUEDAD:
        dentro = [r for r in registros if r["antiguedad"] == etiqueta]
        if dentro:
            resumen.append({
                "tramo": etiqueta,
                "socios": len(dentro),
                "importe": sum(r["importe"] for r in dentro),
            })

    return {
        "sin_foto": False,
        "actividad": actividad,
        "rubro": rubro,
        "titulo": titulo,
        "rango": _rango_dict(rng),
        "total": len(registros),
        "importe": sum(r["importe"] for r in registros),
        "cuotas": sum(r["cuotas"] for r in registros),
        "antiguedad": resumen,
        "socios": registros[offset:offset + limit],
        "orden": clave,
        "desc": bool(desc),
        "limit": limit,
        "offset": offset,
        "foto": {"calculado_at": foto.calculado_at.isoformat()},
    }


def _ranking_actividades(por_actividad: dict, largo: int, tope: int = 12) -> list[dict]:
    """Las actividades de mayor a menor, con la cola agrupada en "Otras".

    Hay 62 textos distintos y la mitad no llega a los cien mil pesos: en un
    gráfico son rayas ilegibles y en una tabla, ruido. Se muestran las primeras y
    el resto se junta, igual que en la torta de categorías. La cola agrupada
    conserva su serie mensual sumada, para que el gráfico apilado siga cerrando
    con el total de actividades.
    """
    orden = sorted(por_actividad.values(), key=lambda a: -a["importe"])
    principales = orden[:tope]
    cola = orden[tope:]
    if cola:
        serie = [0.0] * largo
        for a in cola:
            for i, v in enumerate(a["serie"]):
                serie[i] += v
        principales.append({
            "actividad": f"Otras ({len(cola)})",
            "importe": sum(a["importe"] for a in cola),
            "cupones": sum(a["cupones"] for a in cola),
            "serie": serie,
            "agrupada": True,
        })
    return principales


def _acotar(qs, solo: str):
    if solo == "socios_activos":
        return qs.filter(es_socio=True, activo=True)
    if solo == "socios":
        return qs.filter(es_socio=True)
    if solo == "activos":
        return qs.filter(activo=True)
    return qs


def socios_con_deuda(foto: XsysDeudaFoto, categorias_ids=None, cuotas_min: int = 0,
                     solo: str = "socios_activos", orden: str = "-importe"):
    """Queryset de la tabla por socio con los filtros de la pantalla aplicados."""
    qs = XsysDeudaSocio.objects.filter(foto=foto)
    qs = _acotar(qs, solo)
    if categorias_ids:
        qs = qs.filter(id_tipo_cli__in=[int(i) for i in categorias_ids])
    if cuotas_min:
        qs = qs.filter(cuotas__gte=int(cuotas_min))
    permitidos = {"-importe", "importe", "-cuotas", "cuotas", "apellido", "categoria"}
    return qs.order_by(orden if orden in permitidos else "-importe")


def _totales_socios(foto, categorias_ids, cuotas_min, solo) -> dict:
    from django.db.models import Count, Sum

    agg = socios_con_deuda(foto, categorias_ids, cuotas_min, solo).aggregate(
        socios=Count("id"), cupones=Sum("cuotas"), importe=Sum("importe"))
    return {
        "socios": agg["socios"] or 0,
        "cupones": agg["cupones"] or 0,
        "importe": float(agg["importe"] or 0),
    }


def _distribucion_cuotas(foto, categorias_ids, solo, tope: int = 12) -> list[dict]:
    """Cuántos socios deben 1 cuota, 2, 3... El club mide la mora así."""
    from django.db.models import Count, Sum

    filas = (socios_con_deuda(foto, categorias_ids, 0, solo)
             .values("cuotas").annotate(n=Count("id"), importe=Sum("importe"))
             .order_by("cuotas"))
    out: dict[int, dict] = {}
    for f in filas:
        k = min(int(f["cuotas"]), tope)
        acc = out.setdefault(k, {"cuotas": k, "socios": 0, "importe": 0.0,
                                 "tope": k == tope})
        acc["socios"] += f["n"]
        acc["importe"] += float(f["importe"] or 0)
    return [out[k] for k in sorted(out)]


def _rango_dict(r: dict) -> dict:
    return {
        "clave": r["clave"],
        "etiqueta": r["etiqueta"],
        "desde": r["desde"].isoformat(),
        "hasta": r["hasta"].isoformat(),
        "granularidad": r["granularidad"],
    }

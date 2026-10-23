"""Datos de la pantalla de Elecciones.

DÓNDE ESTÁ EL VOTO
------------------
xSys no tiene una tabla de elecciones: reutiliza las listas de precios. Cada
elección (y cada asamblea) es un registro de ``Prod_Listas_Precios`` y su padrón
son las filas de ``Clientes_Listas_Precios`` con ese ``Id_Lista_Precio``, que
arma ``SP_Generar_Padron``. Cuando el socio vota, la mesa completa su fila:

    Nro_Mesa       la mesa asignada (viene cargada desde el padrón)
    Fecha_Proceso  cuándo votó; vacía = todavía no votó
    Nro_Orden      su número de orden en el padrón de la mesa

Así que "votaron" es ``Fecha_Proceso IS NOT NULL`` y la hora del voto es esa
fecha. Las listas que se llaman "Elecciones ..." son las elecciones; se dejan
afuera los "(backup)" y el "Borrador" que xSys guarda al lado.

LA COMPARACIÓN
--------------
Se compara contra la elección anterior que tuvo votos, alineando por hora del
día: "a las 15:20 de 2024 habían votado tantos". Se compara en porcentaje del
padrón además de en votos, porque los padrones no miden lo mismo (10.695 en
2024 contra 10.154 en 2026).

LAS ACTIVIDADES
---------------
Son los contratos vigentes HOY de quienes votaron, agrupados por familia de
producto. Se descartan las familias que no son una actividad elegida por el
socio: la cuota social (la tiene todo el padrón), la facturación de remitos y
la deuda. Las familias deportivas son las mismas del apto físico
(``XSYS_APTO_FAMILIAS``); el resto va como servicio (cocheras, comodidades...).

Todo es de solo lectura y se guarda unos segundos en caché: la pantalla se
refresca sola y puede estar abierta en varias computadoras a la vez.
"""

from __future__ import annotations

import datetime as _dt

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from xsys.services.mssql import connect


class EleccionesError(RuntimeError):
    """No se pudieron leer los datos de xSys; el mensaje va tal cual a pantalla."""


# La jornada electoral, de 10 a 21. Es el eje de los gráficos: lo acreditado
# antes de abrir (en 2024 el primero fue 09:44) cuenta en la primera franja.
APERTURA = _dt.time(10, 0)
CIERRE = _dt.time(21, 0)
TRAMO_MIN = 10

# Familias que no son una actividad: la cuota social, los remitos y la deuda.
FAMILIAS_EXCLUIDAS = (0, 1, 65)

TTL_VOTOS = 20
TTL_ACTIVIDADES = 120


# --------------------------------------------------------------------------- #
# Conexión
# --------------------------------------------------------------------------- #

def _conectar():
    try:
        conn = connect(settings.MSSQL_XSYS)
    except Exception as exc:  # pragma: no cover - depende de la red
        raise EleccionesError(
            "Sin conexión con Clever: los datos se leen en vivo y no se pueden "
            "mostrar hasta que vuelva.") from exc
    return conn


def _consultar(sql: str, params: tuple = ()) -> list[tuple]:
    conn = _conectar()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [tuple(r) for r in cur.fetchall()]
    except EleccionesError:
        raise
    except Exception as exc:
        raise EleccionesError(f"xSys rechazó la consulta: {str(exc)[:180]}") from exc
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover
            pass


def _ahora() -> _dt.datetime:
    return timezone.localtime().replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Las elecciones
# --------------------------------------------------------------------------- #

def listar() -> list[dict]:
    """Las elecciones con padrón, de la más nueva a la más vieja."""
    clave = "elecciones:listar"
    datos = cache.get(clave)
    if datos is not None:
        return datos
    filas = _consultar("""
        SELECT  P.Id_Lista_Precio, RTRIM(P.Descripcion), P.Fecha_Desde,
                COUNT(*), SUM(CASE WHEN L.Fecha_Proceso IS NULL THEN 0 ELSE 1 END)
        FROM    Prod_Listas_Precios P
                JOIN Clientes_Listas_Precios L ON L.Id_Lista_Precio = P.Id_Lista_Precio
        WHERE   P.Descripcion LIKE 'Elecciones%'
          AND   P.Descripcion NOT LIKE '%backup%'
        GROUP BY P.Id_Lista_Precio, P.Descripcion, P.Fecha_Desde
        HAVING  COUNT(*) > 100
        ORDER BY P.Fecha_Desde DESC, P.Id_Lista_Precio DESC
    """)
    datos = [{
        "id": int(i),
        "nombre": nombre,
        "fecha": (f.date().isoformat() if f else None),
        "padron": int(padron),
        "votaron": int(votaron or 0),
    } for i, nombre, f, padron, votaron in filas]
    cache.set(clave, datos, 300)
    return datos


def _buscar(id_lista: int | None) -> tuple[dict, dict | None]:
    """La elección pedida (o la más nueva) y la anterior que tuvo votos."""
    todas = listar()
    if not todas:
        raise EleccionesError("xSys no tiene ninguna elección cargada.")
    actual = next((e for e in todas if e["id"] == id_lista), None) if id_lista else todas[0]
    if actual is None:
        raise EleccionesError(f"No existe la elección {id_lista}.")
    anteriores = [e for e in todas
                  if e["fecha"] and actual["fecha"] and e["fecha"] < actual["fecha"]
                  and e["votaron"] > 0]
    return actual, (anteriores[0] if anteriores else None)


def _votos(id_lista: int) -> list[tuple[int | None, _dt.datetime | None]]:
    """(mesa, fecha del voto) de cada empadronado. Es lo único que se lee de
    xSys para los tres primeros sectores: el padrón entero son unas 10.000 filas
    de dos columnas y todo lo demás se cuenta acá."""
    clave = f"elecciones:votos:{id_lista}"
    datos = cache.get(clave)
    if datos is None:
        datos = _consultar(
            "SELECT Nro_Mesa, Fecha_Proceso FROM Clientes_Listas_Precios "
            "WHERE Id_Lista_Precio = ?", (id_lista,))
        cache.set(clave, datos, TTL_VOTOS)
    return datos


# --------------------------------------------------------------------------- #
# El reloj de la jornada
# --------------------------------------------------------------------------- #

def _minuto(h: _dt.datetime | _dt.time) -> int:
    """Minutos desde la apertura (puede ser negativo o pasar el cierre)."""
    return (h.hour * 60 + h.minute) - (APERTURA.hour * 60 + APERTURA.minute)


def _duracion() -> int:
    return _minuto(CIERRE)


def _momento(eleccion: dict) -> dict:
    """En qué punto de la jornada estamos, para esa elección.

    Una elección pasada se mira entera; una futura, vacía. El día de la
    elección, el corte es la hora actual.
    """
    ahora = _ahora()
    fecha = _dt.date.fromisoformat(eleccion["fecha"]) if eleccion["fecha"] else ahora.date()
    if ahora.date() < fecha:
        estado, minuto = "pendiente", 0
    elif ahora.date() > fecha:
        estado, minuto = "cerrada", _duracion()
    else:
        minuto = max(0, min(_duracion(), _minuto(ahora)))
        estado = ("pendiente" if _minuto(ahora) < 0
                  else "cerrada" if _minuto(ahora) >= _duracion() else "en_curso")
    return {"estado": estado, "minuto": minuto, "ahora": ahora.isoformat(timespec="seconds")}


def _etiqueta(minuto: int) -> str:
    total = APERTURA.hour * 60 + APERTURA.minute + minuto
    return f"{total // 60:02d}:{total % 60:02d}"


def _tramos(votos, ancho: int) -> list[int]:
    """Votos por tramo de ``ancho`` minutos, de la apertura al cierre. Lo que
    llegue antes de abrir o después de cerrar va al primer o último tramo, para
    que la suma dé siempre el total."""
    n = -(-_duracion() // ancho)
    cuenta = [0] * n
    for _mesa, fecha in votos:
        if fecha is None:
            continue
        i = min(n - 1, max(0, _minuto(fecha) // ancho))
        cuenta[i] += 1
    return cuenta


def _acumulado(cuenta: list[int]) -> list[int]:
    salida, suma = [], 0
    for c in cuenta:
        suma += c
        salida.append(suma)
    return salida


def _base(id_lista: int | None) -> tuple[dict, dict | None, dict]:
    actual, anterior = _buscar(id_lista)
    return actual, anterior, _momento(actual)


def _cabecera(actual: dict, anterior: dict | None, momento: dict) -> dict:
    return {
        "eleccion": actual,
        "comparada": anterior,
        "momento": momento,
        "jornada": {"apertura": APERTURA.strftime("%H:%M"), "cierre": CIERRE.strftime("%H:%M")},
    }


# --------------------------------------------------------------------------- #
# 1. Participación
# --------------------------------------------------------------------------- #

def participacion(id_lista: int | None = None) -> dict:
    """El número grande, los indicadores y la velocidad cada 10 minutos."""
    actual, anterior, momento = _base(id_lista)
    votos = _votos(actual["id"])
    padron = len(votos)
    votaron = sum(1 for _m, f in votos if f is not None)
    tramos = _tramos(votos, TRAMO_MIN)
    corte = momento["minuto"]
    tramo_actual = min(len(tramos) - 1, corte // TRAMO_MIN) if momento["estado"] == "en_curso" else None

    # Ritmo: votos de los últimos 60 minutos cerrados, expresado cada 10.
    ult_hora = 0
    if momento["estado"] == "en_curso":
        desde = max(0, corte - 60)
        ult_hora = sum(1 for _m, f in votos if f is not None and desde <= _minuto(f) < corte)
    ritmo = round(ult_hora / 6, 1)
    restan_min = max(0, _duracion() - corte)
    proyeccion = (votaron + round(ult_hora * restan_min / 60)
                  if momento["estado"] == "en_curso" else votaron)

    # Acreditados de la última hora de reloj ya terminada (a las 15:40, de 14 a
    # 15). En la primera hora todavía no hay una cerrada: va la que corre.
    por_hora = _tramos(votos, 60)
    if momento["estado"] == "pendiente":
        h, completa = 0, False
    elif momento["estado"] == "cerrada":
        h, completa = len(por_hora) - 1, True
    else:
        h, completa = (corte // 60 - 1, True) if corte >= 60 else (0, False)
    hora_corte = {
        "desde": _etiqueta(h * 60),
        "hasta": _etiqueta(min(_duracion(), (h + 1) * 60)),
        "votos": por_hora[h] if momento["estado"] != "pendiente" else 0,
        "completa": completa,
    }

    comp = None
    if anterior:
        votos_ant = _votos(anterior["id"])
        padron_ant = len(votos_ant)
        total_ant = sum(1 for _m, f in votos_ant if f is not None)
        a_esta_hora = (total_ant if momento["estado"] == "cerrada" else
                       sum(1 for _m, f in votos_ant if f is not None and _minuto(f) < corte))
        comp = {
            "padron": padron_ant,
            "a_esta_hora": a_esta_hora,
            "pct_a_esta_hora": _pct(a_esta_hora, padron_ant),
            "total": total_ant,
            "pct_total": _pct(total_ant, padron_ant),
            "tramos": _tramos(votos_ant, TRAMO_MIN),
        }

    return {
        **_cabecera(actual, anterior, momento),
        "padron": padron,
        "votaron": votaron,
        "restan": padron - votaron,
        "pct": _pct(votaron, padron),
        "ritmo_10min": ritmo,
        "ultima_hora": ult_hora,
        "hora_corte": hora_corte,
        "proyeccion": proyeccion,
        "pct_proyeccion": _pct(proyeccion, padron),
        "mesas": len({m for m, _f in votos if m is not None}),
        "tramos": {
            "ancho": TRAMO_MIN,
            "etiquetas": [_etiqueta(i * TRAMO_MIN) for i in range(len(tramos))],
            "votos": tramos,
            "en_curso": tramo_actual,
        },
        "comparacion": comp,
    }


def _pct(n: int, total: int) -> float:
    return round(100.0 * n / total, 1) if total else 0.0


# --------------------------------------------------------------------------- #
# 2. Mesas
# --------------------------------------------------------------------------- #

def mesas(id_lista: int | None = None) -> dict:
    """Votos y padrón de cada mesa, con el último voto para ver si alguna se
    quedó quieta."""
    actual, anterior, momento = _base(id_lista)
    por_mesa: dict[int, dict] = {}
    for mesa, fecha in _votos(actual["id"]):
        m = por_mesa.setdefault(int(mesa or 0), {"padron": 0, "votaron": 0, "ultimo": None})
        m["padron"] += 1
        if fecha is not None:
            m["votaron"] += 1
            if m["ultimo"] is None or fecha > m["ultimo"]:
                m["ultimo"] = fecha
    filas = [{
        "mesa": n,
        "padron": m["padron"],
        "votaron": m["votaron"],
        "pct": _pct(m["votaron"], m["padron"]),
        "ultimo": m["ultimo"].strftime("%H:%M") if m["ultimo"] else None,
    } for n, m in sorted(por_mesa.items())]
    total_p = sum(f["padron"] for f in filas)
    total_v = sum(f["votaron"] for f in filas)
    return {
        **_cabecera(actual, anterior, momento),
        "mesas": filas,
        "promedio_pct": _pct(total_v, total_p),
    }


# --------------------------------------------------------------------------- #
# 3. Horarios
# --------------------------------------------------------------------------- #

def horarios(id_lista: int | None = None) -> dict:
    """Votos por hora y curva acumulada en % del padrón, contra la anterior."""
    actual, anterior, momento = _base(id_lista)
    votos = _votos(actual["id"])
    por_hora = _tramos(votos, 60)
    serie_10 = _tramos(votos, TRAMO_MIN)
    corte = momento["minuto"]
    visibles = (len(serie_10) if momento["estado"] == "cerrada"
                else 0 if momento["estado"] == "pendiente"
                else min(len(serie_10), corte // TRAMO_MIN + 1))
    acum = [_pct(v, len(votos)) for v in _acumulado(serie_10)][:visibles]

    comp = None
    if anterior:
        votos_ant = _votos(anterior["id"])
        comp = {
            "por_hora": _tramos(votos_ant, 60),
            "acumulado_pct": [_pct(v, len(votos_ant))
                              for v in _acumulado(_tramos(votos_ant, TRAMO_MIN))],
        }
    pico = max(range(len(por_hora)), key=lambda i: por_hora[i]) if any(por_hora) else None
    return {
        **_cabecera(actual, anterior, momento),
        "horas": [_etiqueta(i * 60) for i in range(len(por_hora))],
        "por_hora": por_hora,
        "hora_en_curso": (min(len(por_hora) - 1, corte // 60)
                          if momento["estado"] == "en_curso" else None),
        "hora_pico": (_etiqueta(pico * 60) if pico is not None else None),
        "acumulado": {
            "etiquetas": [_etiqueta(i * TRAMO_MIN) for i in range(len(serie_10))],
            "pct": acum,
        },
        "comparacion": comp,
    }


def pantalla(id_lista: int | None = None) -> dict:
    """Lo del televisor en una sola respuesta: resumen, horarios y mesas.

    Los votos se leen una vez (``_votos`` está cacheado), así que armar las
    tres partes no triplica las consultas a xSys.
    """
    d = participacion(id_lista)
    d["horarios"] = horarios(id_lista)
    d["mesas"] = mesas(id_lista)["mesas"]
    return d


# --------------------------------------------------------------------------- #
# 4. Actividades de los que votaron
# --------------------------------------------------------------------------- #

def actividades(id_lista: int | None = None) -> dict:
    """Qué actividades tienen contratadas los que ya votaron.

    Para cada actividad: cuántos votaron, cuántos del padrón la tienen y qué
    porcentaje de ellos ya votó. Ese último número es el que dice algo: que
    entre los de fútbol vote el 40 % y en el padrón general el 20 % es un dato;
    que voten 300 de fútbol, sin el padrón al lado, no.
    """
    actual, anterior, momento = _base(id_lista)
    clave = f"elecciones:actividades:{actual['id']}"
    filas = cache.get(clave)
    if filas is None:
        excl = ",".join("?" * len(FAMILIAS_EXCLUIDAS))
        filas = _consultar(f"""
            SELECT  PR.Id_Familia, RTRIM(ISNULL(F.Descripcion, '')),
                    COUNT(DISTINCT L.Id_Cliente),
                    COUNT(DISTINCT CASE WHEN L.Fecha_Proceso IS NOT NULL THEN L.Id_Cliente END)
            FROM    Clientes_Listas_Precios L
                    JOIN Contratos K       ON K.Id_Cliente = L.Id_Cliente
                    JOIN Contratos_Prod CP ON CP.Id_Contrato = K.Id_Contrato
                    JOIN Productos PR      ON PR.Id_Producto = CP.Id_Producto
                    JOIN Prod_Familias F   ON F.Id_Familia = PR.Id_Familia
            WHERE   L.Id_Lista_Precio = ?
              AND   K.Activo = 1 AND K.Fecha_Baja IS NULL
              AND   (K.Fecha_Hasta IS NULL OR K.Fecha_Hasta >= CAST(GETDATE() AS date))
              AND   (K.Fecha_Desde IS NULL OR K.Fecha_Desde <= GETDATE())
              AND   PR.Id_Familia NOT IN ({excl})
            GROUP BY PR.Id_Familia, F.Descripcion
            UNION ALL
            SELECT  -1, '', COUNT(*), SUM(CASE WHEN L.Fecha_Proceso IS NULL THEN 0 ELSE 1 END)
            FROM    Clientes_Listas_Precios L
            WHERE   L.Id_Lista_Precio = ?
              AND   NOT EXISTS (
                        SELECT 1 FROM Contratos K
                               JOIN Contratos_Prod CP ON CP.Id_Contrato = K.Id_Contrato
                               JOIN Productos PR      ON PR.Id_Producto = CP.Id_Producto
                        WHERE  K.Id_Cliente = L.Id_Cliente
                          AND  K.Activo = 1 AND K.Fecha_Baja IS NULL
                          AND  (K.Fecha_Hasta IS NULL OR K.Fecha_Hasta >= CAST(GETDATE() AS date))
                          AND  (K.Fecha_Desde IS NULL OR K.Fecha_Desde <= GETDATE())
                          AND  PR.Id_Familia NOT IN ({excl}))
        """, (actual["id"], *FAMILIAS_EXCLUIDAS, actual["id"], *FAMILIAS_EXCLUIDAS))
        cache.set(clave, filas, TTL_ACTIVIDADES)

    deportes = set(getattr(settings, "XSYS_APTO_FAMILIAS", ()))
    votos = _votos(actual["id"])
    votaron_total = sum(1 for _m, f in votos if f is not None)
    salida, sin_actividad = [], None
    for id_familia, nombre, padron, votaron in filas:
        fila = {
            "familia": int(id_familia),
            "actividad": _nombre_actividad(nombre),
            "tipo": "deporte" if int(id_familia) in deportes else "servicio",
            "padron": int(padron or 0),
            "votaron": int(votaron or 0),
            "pct": _pct(int(votaron or 0), int(padron or 0)),
            "pct_de_votantes": _pct(int(votaron or 0), votaron_total),
        }
        if int(id_familia) == -1:
            sin_actividad = {**fila, "actividad": "Sin actividad contratada", "tipo": "ninguna"}
        else:
            salida.append(fila)
    salida.sort(key=lambda f: (-f["votaron"], -f["padron"], f["actividad"]))
    con_actividad = votaron_total - (sin_actividad["votaron"] if sin_actividad else 0)
    return {
        **_cabecera(actual, anterior, momento),
        "votaron": votaron_total,
        "padron": len(votos),
        "pct_general": _pct(votaron_total, len(votos)),
        "con_actividad": con_actividad,
        "actividades": salida,
        "sin_actividad": sin_actividad,
    }


def _nombre_actividad(nombre: str) -> str:
    """'Hockey s/ Cesped' -> 'Hockey s/ Césped'; 'INSTITUTO (SOLO SOCIOS)' ->
    'Instituto (solo socios)'. Las familias vienen cargadas a mano en xSys,
    algunas en mayúsculas y otras no."""
    limpio = " ".join((nombre or "").split())
    if limpio.isupper():
        limpio = limpio.capitalize()
    palabras = [_TILDES.get(p, p) for p in limpio.split(" ")]
    return " ".join(palabras) or "Sin nombre"


_TILDES = {"Cesped": "Césped", "Natacion": "Natación", "Futbol": "Fútbol",
           "Patin": "Patín", "Martin": "Martín", "Voley": "Vóley",
           "Gimnasia": "Gimnasia", "Artistica": "Artística"}

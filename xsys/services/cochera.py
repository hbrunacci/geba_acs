"""Cuotas de cochera impagas, para explicar el rechazo de la barrera.

Cuando la barrera no deja pasar a alguien que tiene cochera, xSys dice "No
cumple ninguna condición habilitante" (motivo 112), y el visor lo traducía a
"Chequear Oficina de Socios". El motivo real casi siempre es otro: la cochera
se valida por cupón pago (``CP_SCA_ValidarProducVta``: cada cuota habilita del
1° del mes hasta 41 días después) y el socio todavía no pagó la del mes. Así lo
vivió una socia de la Noble 109 entre junio y octubre de 2026: la rechazaban
del 12 en adelante cada mes que pagaba tarde por la web.

Se lee en vivo, no se espeja: el socio suele pagar desde el celular parado
frente a la barrera, y el operador tiene que ver el cambio enseguida. La
consulta es sólo para los rechazos 112 de barrera, que son pocos por día, y se
cachea unos minutos por (socio, acceso).
"""

from __future__ import annotations

import datetime as _dt
import logging

from django.conf import settings
from django.core.cache import cache

from xsys.services.mssql import connect

logger = logging.getLogger(__name__)

MOTIVO_NINGUNA_CONDICION = 112
CACHE_SEGUNDOS = 180

# Cupones (Compromete_Factura = 1) en estado PENDIENTE (1) y con importe: los
# que CP_SCA_ValidarProducVta no toma como pagos. Sólo productos que habilitan
# ESE acceso (CD_Accesos_Prod), que en una barrera son las cocheras.
_SQL = """
    SELECT CI.Id_Cliente, CA.Id_Acceso, CI.Fecha_QA,
           ISNULL(PR.Descripcion_Resumida, CI.Id_Producto)
    FROM Cbtes_Items CI
         JOIN Cbtes CB ON CB.Id_Trans = CI.Id_Trans
         JOIN Cbtes_Tipos CT ON CT.Id_Tipo_Cbte = CB.Id_Tipo_Cbte
         JOIN CD_Accesos_Prod CA ON CA.Id_Producto = CI.Id_Producto
         JOIN Productos PR ON PR.Id_Producto = CI.Id_Producto
    WHERE CI.Id_Cliente IN ({clientes})
      AND CA.Id_Acceso IN ({accesos})
      AND CT.Compromete_Factura = 1
      AND CB.Id_Estado_Cbte = 1
      AND CI.Imp_Final > 0
"""


def _clave(id_cliente: int, id_acceso: int) -> str:
    return f"xsys:cochera_impaga:{id_cliente}:{id_acceso}"


def _como_fecha(valor) -> _dt.date | None:
    if isinstance(valor, _dt.datetime):
        return valor.date()
    return valor


def _consultar(pares: set[tuple[int, int]]) -> dict[tuple[int, int], list]:
    clientes = sorted({c for c, _ in pares})
    accesos = sorted({a for _, a in pares})
    sql = _SQL.format(clientes=",".join("?" * len(clientes)),
                      accesos=",".join("?" * len(accesos)))
    conn = connect(settings.MSSQL_XSYS)
    try:
        cur = conn.cursor()
        cur.execute(sql, clientes + accesos)
        filas = cur.fetchall()
    finally:
        conn.close()
    por_par: dict[tuple[int, int], list] = {p: [] for p in pares}
    for cid, acceso, fecha_qa, descripcion in filas:
        par = (int(cid), int(acceso))
        if par in por_par:
            por_par[par].append((_como_fecha(fecha_qa), (descripcion or "").strip()))
    return por_par


def impagas(pares) -> dict[tuple[int, int], list]:
    """{(id_cliente, id_acceso): [(período, cochera), ...]} con los cupones impagos.

    Un par sin cupones impagos vuelve con lista vacía (y se cachea igual, para
    no volver a preguntar en cada refresco del visor). Si Clever no responde,
    devuelve lo que haya en caché: el visor no se cae por un accesorio.
    """
    pares = {(int(c), int(a)) for c, a in pares if c and a}
    if not pares:
        return {}
    claves = {p: _clave(*p) for p in pares}
    en_cache = cache.get_many(claves.values())
    resultado = {p: en_cache[k] for p, k in claves.items() if k in en_cache}
    faltan = pares - set(resultado)
    if faltan:
        try:
            nuevos = _consultar(faltan)
        except Exception:
            logger.exception("No se pudieron leer las cuotas de cochera de xSys")
            return resultado
        cache.set_many({claves[p]: v for p, v in nuevos.items()}, CACHE_SEGUNDOS)
        resultado.update(nuevos)
    return resultado


def adeudada_al(cupones: list, fecha: _dt.date) -> tuple[str, list[_dt.date]] | None:
    """(cochera, períodos) de lo impago a esa fecha, o None si no debía nada.

    Un cupón de un mes que todavía no empezó no cuenta: el club emite por
    adelantado y el de noviembre existe en octubre sin que nadie lo deba.
    """
    vencidos = sorted((qa, desc) for qa, desc in cupones if qa and qa <= fecha)
    if not vencidos:
        return None
    return vencidos[-1][1], [qa for qa, _ in vencidos]

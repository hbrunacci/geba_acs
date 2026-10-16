"""Copia del apto físico de los deportistas, para el monitor de la puerta.

QUÉ SE CONTROLA
---------------
El sector de Socios carga el vencimiento del apto en xSys: ``Clientes_Venc``,
tipo ``AF`` (APTO FISICO), una fila por persona con la fecha de vencimiento en
``Fecha``. La web del socio ya avisa con un pop-up; el molinete no traba.

En la puerta se avisa sólo a quien hace un deporte: tiene un contrato vigente
con algún producto de las familias de ``XSYS_APTO_FAMILIAS``. Al resto no se le
exige todavía. Medido el 15/10/2026, de las 9.549 personas que entraron en la
semana sólo 531 tenían el apto vigente: avisarle a todos habría puesto en
amarillo el 94 % de los ingresos, y un aviso que sale siempre deja de leerse.

POR QUÉ ES UNA COPIA Y NO UNA CONSULTA EN VIVO
----------------------------------------------
El monitor se refresca cada pocos segundos en cada garita. Salir a xSys en cada
refresco es justo lo que saturó el servidor en julio. La consulta entera —unas
6.700 personas— vuelve en 0,13 s, así que alcanza con copiarla cada pocos
minutos: el que presentó el apto a la mañana deja de figurar enseguida.

LAS FECHAS DE RELLENO
---------------------
``Clientes_Venc`` tiene fechas de 1950 (nunca presentó) y de 2200 (exento).
La de 1950 se guarda como "sin fecha"; la de 2200 queda tal cual y cuenta como
vigente, que es lo que quiso decir quien la cargó.
"""

from __future__ import annotations

import datetime as _dt

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from xsys.models import XsysAptoFisico
from xsys.services.mssql import connect

TIPO_VENC = "AF"

# Antes de esto la fecha es relleno ("nunca presentó"), no un vencimiento real.
RELLENO_HASTA = _dt.date(1960, 1, 1)


class AptoError(Exception):
    """No se pudo copiar. El mensaje va tal cual al log del proceso."""


def familias() -> tuple[int, ...]:
    return tuple(int(f) for f in getattr(settings, "XSYS_APTO_FAMILIAS", ()))


def _sql(cantidad_familias: int) -> str:
    """Una fila por persona y familia deportiva vigente, con su apto.

    Contrato vigente con el mismo criterio que el resto del sistema: activo, sin
    baja y dentro de su período. El apto va por LEFT JOIN porque justamente
    interesan los que no lo tienen.
    """
    marcadores = ",".join("?" for _ in range(cantidad_familias))
    return f"""
        SELECT  K.Id_Cliente, PR.Id_Familia, RTRIM(ISNULL(F.Descripcion, '')),
                MAX(V.Fecha)
        FROM    Contratos K
                JOIN Contratos_Prod CP ON CP.Id_Contrato = K.Id_Contrato
                JOIN Productos PR      ON PR.Id_Producto = CP.Id_Producto
                JOIN Prod_Familias F   ON F.Id_Familia = PR.Id_Familia
                LEFT JOIN Clientes_Venc V
                       ON V.Id_Cliente = K.Id_Cliente AND V.Id_Tipo_Venc = ?
        WHERE   K.Activo = 1 AND K.Fecha_Baja IS NULL
          AND   (K.Fecha_Hasta IS NULL OR K.Fecha_Hasta >= CAST(GETDATE() AS date))
          AND   (K.Fecha_Desde IS NULL OR K.Fecha_Desde <= GETDATE())
          AND   PR.Id_Familia IN ({marcadores})
        GROUP BY K.Id_Cliente, PR.Id_Familia, F.Descripcion
    """


def _fecha(valor) -> _dt.date | None:
    if valor is None:
        return None
    fecha = valor.date() if isinstance(valor, _dt.datetime) else valor
    return None if fecha < RELLENO_HASTA else fecha


def _agrupar(filas) -> dict[int, dict]:
    """Junta las familias de cada persona en un solo registro."""
    por_cliente: dict[int, dict] = {}
    for id_cliente, _id_familia, descripcion, fecha in filas:
        p = por_cliente.setdefault(int(id_cliente), {"vence": _fecha(fecha), "deportes": []})
        nombre = (descripcion or "").strip()
        if nombre and nombre not in p["deportes"]:
            p["deportes"].append(nombre)
    return por_cliente


def leer(cursor) -> dict[int, dict]:
    fams = familias()
    if not fams:
        return {}
    cursor.execute(_sql(len(fams)), [TIPO_VENC, *fams])
    return _agrupar(cursor.fetchall())


def guardar(por_cliente: dict[int, dict]) -> dict:
    """Reemplaza la copia por lo leído. Todo o nada."""
    ahora = timezone.now()
    objs = [
        XsysAptoFisico(
            id_cliente=cid,
            vence=p["vence"],
            deportes=", ".join(sorted(p["deportes"]))[:200],
            synced_at=ahora,
        )
        for cid, p in por_cliente.items()
    ]
    with transaction.atomic():
        borrados, _ = XsysAptoFisico.objects.exclude(id_cliente__in=por_cliente.keys()).delete()
        if objs:
            XsysAptoFisico.objects.bulk_create(
                objs,
                update_conflicts=True,
                unique_fields=["id_cliente"],
                update_fields=["vence", "deportes", "synced_at"],
                batch_size=2000,
            )
    hoy = timezone.localdate()
    sin = sum(1 for o in objs if o.vence is None)
    vencidos = sum(1 for o in objs if o.vence is not None and o.vence < hoy)
    return {
        "deportistas": len(objs),
        "sin_apto": sin,
        "vencidos": vencidos,
        "vigentes": len(objs) - sin - vencidos,
        "salieron": borrados,
    }


def sincronizar() -> dict:
    try:
        conn = connect(settings.MSSQL_XSYS)
    except Exception as exc:  # pragma: no cover - depende de la red
        raise AptoError(f"No se pudo conectar a xSys: {exc}") from exc
    try:
        por_cliente = leer(conn.cursor())
    except Exception as exc:
        raise AptoError(f"Falló la consulta del apto físico: {exc}") from exc
    finally:
        conn.close()
    return guardar(por_cliente)

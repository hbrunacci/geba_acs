"""Grupo familiar del socio, armado desde el espejo local.

En xSys el grupo es un solo campo: ``Clientes.Id_Cliente_Ref`` apunta al titular, y
los integrantes son las fichas que lo apuntan. No hay tabla de grupo ni jerarquía
de más de un nivel: el titular tiene ``Id_Cliente_Ref = 0``.

Se resuelve contra ``XsysSocio`` y no contra xSys porque el espejo ya tiene el
campo y la pantalla lo pide en cada socio que se abre. Los inactivos también se
muestran —un integrante dado de baja sigue siendo parte de la historia del
grupo— pero se marcan, porque son los que explican un cupón que bajó de monto.
"""

from __future__ import annotations

from xsys.models import XsysSocio


def _persona(s: XsysSocio, *, rol: str) -> dict:
    nombre = f"{s.apellido}, {s.nombre}".strip(", ") or s.razon_social
    return {
        "id_cliente": s.id_cliente,
        "nro_socio": s.id_cliente_externo or "",
        "nombre": nombre,
        "doc_nro": s.doc_nro,
        "categoria": s.categoria,
        "id_tipo_cli": s.id_tipo_cli,
        "activo": bool(s.activo),
        "fecha_nac": s.fecha_nac.date().isoformat() if s.fecha_nac else None,
        "ult_cuota_paga": s.ult_cuota_paga.date().isoformat() if s.ult_cuota_paga else None,
        "rol": rol,
    }


def de(id_cliente: int) -> dict:
    """Devuelve el grupo familiar visto desde ``id_cliente``.

    ``rol`` vale ``titular``, ``integrante`` o ``consultado`` para el propio socio
    (que además lleva ``es_titular``), así la pantalla puede resaltarlo sin
    recalcular nada.
    """
    yo = XsysSocio.objects.filter(pk=id_cliente).first()
    if yo is None:
        # Misma forma que el caso normal: la pantalla lee siempre ``miembros`` y
        # no tiene por qué distinguir "no existe" de "no tiene grupo".
        return {
            "id_cliente": id_cliente,
            "tiene_grupo": False,
            "es_titular": False,
            "id_titular": None,
            "titular_desconocido": True,
            "cantidad": 0,
            "activos": 0,
            "miembros": [],
        }

    ref = yo.id_cliente_ref or 0
    id_titular = ref or yo.id_cliente
    es_titular = ref == 0

    titular = yo if es_titular else XsysSocio.objects.filter(pk=id_titular).first()
    integrantes = list(
        XsysSocio.objects.filter(id_cliente_ref=id_titular)
        .exclude(pk=id_titular)
        .order_by("-activo", "fecha_nac", "apellido", "nombre")
    )

    miembros = []
    if titular is not None:
        miembros.append(_persona(titular, rol="titular"))
    for s in integrantes:
        miembros.append(_persona(s, rol="integrante"))
    for m in miembros:
        m["es_consultado"] = m["id_cliente"] == yo.id_cliente

    activos = sum(1 for m in miembros if m["activo"])
    return {
        "id_cliente": yo.id_cliente,
        # Una ficha sola, sin titular ni integrantes, no es un "grupo familiar".
        "tiene_grupo": len(miembros) > 1,
        "es_titular": es_titular,
        "id_titular": id_titular if titular is not None else None,
        "titular_desconocido": titular is None,
        "cantidad": len(miembros),
        "activos": activos,
        "miembros": miembros,
    }

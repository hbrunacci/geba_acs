"""Contratos del socio tal como se muestran en el visor de molinetes.

El espejo `XsysContrato` guarda todos los contratos con `Activo=1` de xSys, pero
esa marca no alcanza como criterio de "lo que el socio tiene hoy": arrastra
contratos muertos hace años (DESCUENTO POR PANDEMIA 2020, RECUPERO DE GASTOS
2021, ACTIVIDADES BONIFICADAS 2021...). Acá se aplica el criterio de vigencia
real y se arma el resumen que ve el operador en la pantalla.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from xsys.models import XsysContrato, XsysSocio

# Un contrato se considera vivo si se le emitió algún comprobante en este lapso.
# Los residuos históricos no facturan hace años y quedan afuera solos.
MESES_FACTURACION_RECIENTE = 12


def _nombre_visible(c: XsysContrato) -> str:
    """Nombre corto para la pantalla.

    El tipo de contrato dice "DEPORTES FEDERADOS" para todas las disciplinas, así
    que cuando hay producto se usa ese, recortado antes del sufijo administrativo:
    "ESGRIMA - ACT MAYOR..CUOTA" -> "ESGRIMA".
    """
    prod = (c.producto_desc or "").strip()
    if prod:
        return (prod.split(" - ")[0].strip() or prod)[:40]
    return (c.descripcion or "").strip()[:40]


def _fila(c: XsysContrato, titular: str = "") -> dict:
    deuda = c.deuda or 0
    if deuda > 0:
        estado = "deuda"
    elif c.ultimo_pago_fecha is None:
        estado = "sin_pagos"
    else:
        estado = "ok"
    return {
        "nombre": _nombre_visible(c),
        "tipo": (c.descripcion or "").strip(),
        "ultimo_pago": c.ultimo_pago_fecha.isoformat() if c.ultimo_pago_fecha else None,
        "ultimo_pago_importe": float(c.ultimo_pago_importe) if c.ultimo_pago_importe is not None else None,
        "deuda": float(deuda) if deuda else 0.0,
        "estado": estado,
        # No vacío cuando el contrato es del titular del grupo familiar: el
        # operador tiene que saber que ese pago no es del socio que pasó.
        "via_titular": titular,
    }


# Orden de urgencia: lo accionable primero, porque la tarjeta muestra 3 líneas.
_PRIORIDAD = {"deuda": 0, "sin_pagos": 1, "ok": 2}


def _ordenar_y_deduplicar(filas: list[dict]) -> list[dict]:
    """Une contratos que muestran el mismo nombre y deja arriba lo urgente.

    Un socio puede tener dos contratos distintos del mismo producto (p.ej. dos
    altas sucesivas de RUGBY); en pantalla se verían como una línea repetida.
    Se conserva el peor estado y se suman las deudas.
    """
    por_nombre: dict[str, dict] = {}
    for f in filas:
        prev = por_nombre.get(f["nombre"])
        if prev is None:
            por_nombre[f["nombre"]] = dict(f)
            continue
        prev["deuda"] = (prev["deuda"] or 0) + (f["deuda"] or 0)
        if _PRIORIDAD[f["estado"]] < _PRIORIDAD[prev["estado"]]:
            prev["estado"] = f["estado"]
        # De los pagos se queda el más reciente.
        if (f["ultimo_pago"] or "") > (prev["ultimo_pago"] or ""):
            prev["ultimo_pago"] = f["ultimo_pago"]
            prev["ultimo_pago_importe"] = f["ultimo_pago_importe"]
    out = list(por_nombre.values())
    for f in out:
        if f["deuda"] > 0:
            f["estado"] = "deuda"
    out.sort(key=lambda f: (_PRIORIDAD[f["estado"]], -(f["deuda"] or 0), f["nombre"]))
    return out


def _vigentes(ids: set[int]):
    """Contratos vigentes (activos, no vencidos, con facturación reciente)."""
    if not ids:
        return []
    corte = timezone.localdate() - timedelta(days=30 * MESES_FACTURACION_RECIENTE)
    return list(
        XsysContrato.objects
        .filter(id_cliente__in=ids, activo=1, ultimo_cbte_fecha__gte=corte)
        .exclude(fecha_hasta__lt=timezone.now())
        .order_by("id_cliente", "descripcion")
    )


def resumen_por_socio(ids_cliente) -> dict[int, list[dict]]:
    """{id_cliente: [contrato, ...]} con los contratos vigentes de cada socio.

    A los adherentes de un grupo familiar la cuota social se les factura contra
    el contrato del TITULAR, así que su propio contrato no tiene comprobantes y
    quedarían sin nada que mostrar (~22% de los socios). Para esos se agregan
    los contratos del titular, marcados con ``via_titular``.

    Lee solo del espejo local (el visor nunca consulta xSys en vivo).
    """
    ids = {int(i) for i in ids_cliente if i}
    if not ids:
        return {}

    out: dict[int, list[dict]] = {}
    for c in _vigentes(ids):
        out.setdefault(c.id_cliente, []).append(_fila(c))

    # Socios sin contrato propio facturado -> mirar al titular del grupo.
    sin_propios = ids - set(out)
    if sin_propios:
        titulares = dict(
            XsysSocio.objects
            .filter(id_cliente__in=sin_propios)
            .exclude(id_cliente_ref__in=[None, 0])
            .values_list("id_cliente", "id_cliente_ref")
        )
        if titulares:
            nombres = {
                s.id_cliente: (f"{s.apellido}, {s.nombre}".strip(", ") or s.razon_social)
                for s in XsysSocio.objects.filter(id_cliente__in=set(titulares.values()))
            }
            por_titular: dict[int, list[XsysContrato]] = {}
            for c in _vigentes(set(titulares.values())):
                por_titular.setdefault(c.id_cliente, []).append(c)
            for cli, ref in titulares.items():
                filas = [_fila(c, titular=nombres.get(ref, f"Socio {ref}")) for c in por_titular.get(ref, [])]
                if filas:
                    out[cli] = filas
    return {cli: _ordenar_y_deduplicar(filas) for cli, filas in out.items()}

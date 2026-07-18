"""Regla de cuota del estatuto (bloqueo por 2 cuotas impagas).

El estatuto no fija días de vencimiento; solo restringe a quien acumule DOS
cuotas impagas. Criterio implementado (acordado con el club):

    fecha_limite_ingreso = 1° del mes pagado
                           + días del 1er mes impago
                           + días del 2do mes impago
                           + días de vencimiento

El socio mantiene acceso mientras adeude hasta UNA cuota y no se venza la
segunda. ``ult_cuota_paga`` es el mes pagado hasta (1° de mes); el 1er mes impago
es el mes siguiente.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from django.conf import settings
from django.utils import timezone


def dias_vencimiento_default() -> int:
    return int(getattr(settings, "XSYS_CUOTA_DIAS_VENCIMIENTO", 10))


def _as_date(v):
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v


def _add_month(y: int, m: int, n: int) -> tuple[int, int]:
    m0 = (m - 1) + n
    return y + m0 // 12, (m0 % 12) + 1


def fecha_limite_ingreso(ult_cuota_paga, dias_vencimiento: int | None = None) -> date | None:
    """Fecha (date) hasta la cual el socio puede ingresar. None si no hay cuota."""
    ucp = _as_date(ult_cuota_paga)
    if ucp is None:
        return None
    if dias_vencimiento is None:
        dias_vencimiento = dias_vencimiento_default()
    base = date(ucp.year, ucp.month, 1)          # 1° del mes pagado
    y1, m1 = _add_month(ucp.year, ucp.month, 1)  # 1er mes impago
    y2, m2 = _add_month(ucp.year, ucp.month, 2)  # 2do mes impago
    dias1 = calendar.monthrange(y1, m1)[1]
    dias2 = calendar.monthrange(y2, m2)[1]
    return base + timedelta(days=dias1 + dias2 + dias_vencimiento)


def cuota_al_dia(ult_cuota_paga, hoy=None, dias_vencimiento: int | None = None) -> bool:
    """True si, a ``hoy``, el socio no acumula 2 cuotas impagas vencidas."""
    limite = fecha_limite_ingreso(ult_cuota_paga, dias_vencimiento)
    if limite is None:
        return False
    hoy = _as_date(hoy) if hoy is not None else timezone.localdate()
    return hoy <= limite

"""Regla de cuota del visor. Es un ESPEJO de la de xSys, a propósito.

    fecha_limite_ingreso = fin del mes pagado (+ Meses_Gracia meses)
                           + Dias_Gracia días

El socio tiene derecho a todo el mes que abonó, y recién al terminarlo empiezan
a correr los días de gracia. Con la config del club (0 meses, 40 días): cuota de
agosto → entra todo agosto y hasta el 10/10.

Por qué es un espejo
--------------------
La decisión de dejar pasar la toma xSys en ``CF_SCA_ValidarUltCuotaPaga``; acá
sólo se pinta la etiqueta "Cuota Vencida" del visor. Cuando las dos reglas no
coinciden, el operador ve "al día" a alguien que el molinete acaba de rechazar
—o al revés— y no hay forma de saber cuál miente. Antes acá vivía la regla del
estatuto ("dos cuotas impagas"), que daba un día más que xSys en casi todos los
meses.

REGLA: si cambia ``CF_SCA_ValidarUltCuotaPaga`` en xSys, hay que cambiar esto.
La fórmula de allá, textual::

    DATEADD(DAY, @Dias_Gracia, EOMONTH(CONVERT(date, @Ult_Cuota_Paga), @Meses_Gracia))

``EOMONTH`` normaliza a los socios cuyo ``Ult_Cuota_Paga`` no cae día 1 (hay
miles): se toma el fin del mes al que corresponde el pago.

Los valores por defecto salen de ``settings`` y tienen que coincidir con los de
``CD_Accesos`` en los accesos que exigen cuota (hoy los cuatro de auto/Noble
están en 0 meses y 40 días).
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from django.conf import settings
from django.utils import timezone


def dias_gracia_default() -> int:
    return int(getattr(settings, "XSYS_CUOTA_DIAS_GRACIA", 40))


def meses_gracia_default() -> int:
    return int(getattr(settings, "XSYS_CUOTA_MESES_GRACIA", 0))


def _as_date(v):
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v


def _fin_de_mes(d: date, meses: int = 0) -> date:
    """Último día del mes de ``d`` corrido ``meses`` meses. Igual que EOMONTH."""
    m0 = (d.month - 1) + meses
    y = d.year + m0 // 12
    m = (m0 % 12) + 1
    return date(y, m, calendar.monthrange(y, m)[1])


def fecha_limite_ingreso(
    ult_cuota_paga,
    dias_gracia: int | None = None,
    meses_gracia: int | None = None,
) -> date | None:
    """Fecha (date) hasta la cual —inclusive— el socio puede ingresar.

    None si no tiene cuota registrada.
    """
    ucp = _as_date(ult_cuota_paga)
    if ucp is None:
        return None
    if dias_gracia is None:
        dias_gracia = dias_gracia_default()
    if meses_gracia is None:
        meses_gracia = meses_gracia_default()
    return _fin_de_mes(ucp, meses_gracia) + timedelta(days=dias_gracia)


def cuota_al_dia(
    ult_cuota_paga,
    hoy=None,
    dias_gracia: int | None = None,
    meses_gracia: int | None = None,
) -> bool:
    """True si, a ``hoy``, la cuota todavía habilita el ingreso."""
    limite = fecha_limite_ingreso(ult_cuota_paga, dias_gracia, meses_gracia)
    if limite is None:
        return False
    hoy = _as_date(hoy) if hoy is not None else timezone.localdate()
    return hoy <= limite

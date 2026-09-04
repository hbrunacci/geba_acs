"""Rellena el historial de accesos por socio con lo que ya está en los espejos.

El historial se escribe solo, en la ingesta. Este comando existe para el hueco:
si el registro estuvo roto un rato, o si un contenedor de larga vida quedó con
código viejo, acá se recupera lo que los espejos todavía tengan
(``ExternalAccessLogEntry`` + ``BiostarAccessEvent``). Es idempotente: la
``referencia`` es única, así que se puede correr las veces que haga falta.

Alcance: la ventana de retención de esos espejos, hoy 7 días de xSys y lo que
guarde el de faciales. Más atrás no llega, y es a propósito.

    NO importa la historia de ``CD_ES``. Se probó y se descartó: el club decidió
    que el historial arranque con lo que registra este sistema, no con lo que
    xSys venía guardando desde 2016. Si alguna vez se cambia de opinión, el
    camino es leer CD_ES paginando por ``Id_ES`` y armar las filas con
    ``historial_socio.fila_de_movimiento``; el conflicto de paso pendiente no se
    puede reconstruir para el pasado, porque depende del reloj del momento.

Uso:
    manage.py historial_socio_backfill
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from access_control.services import historial_socio

_LOTE = 5000


class Command(BaseCommand):
    help = "Rellena el historial de accesos por socio desde los espejos locales."

    def handle(self, *args, **opts):
        from access_control.models import BiostarAccessEvent
        from access_control.models.models import ExternalAccessLogEntry

        historial_socio.invalidar_cache()
        ctx = historial_socio.contexto()

        n = 0
        qs = ExternalAccessLogEntry.objects.filter(id_cliente__gt=0).order_by("external_id")
        for lote in self._por_lotes(qs.iterator(chunk_size=_LOTE)):
            n += historial_socio.registrar_movimientos(lote, ctx)
            self.stdout.write(f"  movimientos: {n}")

        f = 0
        fqs = BiostarAccessEvent.objects.filter(id_cliente__gt=0).order_by("id")
        for lote in self._por_lotes(fqs.iterator(chunk_size=_LOTE)):
            f += historial_socio.registrar_faciales(lote, ctx)
            self.stdout.write(f"  faciales: {f}")

        self.stdout.write(self.style.SUCCESS(
            f"Procesados {n} movimientos y {f} faciales. Los ya registrados no se duplican."))

    @staticmethod
    def _por_lotes(it):
        lote = []
        for x in it:
            lote.append(x)
            if len(lote) >= _LOTE:
                yield lote
                lote = []
        if lote:
            yield lote

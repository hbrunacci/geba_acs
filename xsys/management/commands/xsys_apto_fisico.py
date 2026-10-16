"""Copia el apto físico de los deportistas desde xSys.

    python manage.py xsys_apto_fisico
        Copia una vez y sale.

    python manage.py xsys_apto_fisico --interval 600
        Se queda corriendo y copia cada 10 minutos. Es lo que hace el contenedor
        `apto-fisico`.

Sólo lee de xSys. Ver ``xsys.services.apto_fisico`` para el criterio.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError

from xsys.services.apto_fisico import AptoError, sincronizar


class Command(BaseCommand):
    help = "Copia de xSys el apto físico de quienes tienen un deporte vigente."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval", type=float, default=0.0,
            help="Segundos entre corridas. 0 (default) = una sola vez y sale.")
        parser.add_argument(
            "--silencioso", action="store_true",
            help="En modo continuo, no imprime nada cuando no cambió la cantidad.")

    def handle(self, *args, **opciones):
        intervalo = max(0.0, opciones["interval"])
        if not intervalo:
            self._una_vez(silencioso=False, anterior=None, fatal=True)
            return

        self.stdout.write("Copia del apto físico cada %.0f s." % intervalo)
        anterior = None
        while True:
            anterior = self._una_vez(opciones["silencioso"], anterior, fatal=False)
            time.sleep(intervalo)

    def _una_vez(self, silencioso: bool, anterior, *, fatal: bool):
        try:
            inf = sincronizar()
        except AptoError as exc:
            if fatal:
                raise CommandError(str(exc)) from exc
            # En modo continuo no se muere: si xSys se cae, la copia anterior
            # sigue sirviendo y la próxima vuelta lo reintenta.
            self.stderr.write(self.style.ERROR("falló la copia: %s" % exc))
            return anterior

        resumen = (inf["deportistas"], inf["sin_apto"], inf["vencidos"])
        if not (silencioso and resumen == anterior):
            self.stdout.write(
                "Deportistas: %s | sin apto: %s | vencido: %s | vigente: %s | salieron: %s"
                % (inf["deportistas"], inf["sin_apto"], inf["vencidos"],
                   inf["vigentes"], inf["salieron"]))
        return resumen

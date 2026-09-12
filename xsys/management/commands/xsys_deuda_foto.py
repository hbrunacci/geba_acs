"""Recalcula la foto de deuda del tablero.

Dos modos:

    python manage.py xsys_deuda_foto
        Corre una vez y sale. Es lo que se usa a mano.

    python manage.py xsys_deuda_foto --horas 10,13,16,19
        Se queda corriendo y dispara a esas horas, todos los días. Es lo que hace
        el contenedor `deuda-foto`.

Por qué a horas fijas y no cada N segundos como los otros pollers: esto no es un
poller, es un cierre. El cálculo recorre la cuenta corriente entera de la base de
producción del club y tarda unos cuatro minutos, así que conviene que caiga en
momentos previsibles —empezando la mañana y después cada tres horas hasta el
cierre administrativo— y no a la deriva de cuándo se reinició el contenedor.

Si falla, sale con código 1 en el modo de una sola corrida y deja la foto
anterior intacta, así el tablero sigue mostrando el último número bueno con su
fecha a la vista. En el modo programado NO sale: anota el error y espera la
próxima hora, porque un contenedor que se muere por una VPN caída deja al club
sin actualización hasta que alguien lo note.
"""

from __future__ import annotations

import datetime as dt
import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from xsys.services.tableros import TableroError, recalcular_deuda

HORAS_DEFECTO = "10,13,16,19"


def _parsear_horas(crudo: str) -> list[int]:
    horas = []
    for parte in str(crudo).split(","):
        parte = parte.strip()
        if not parte:
            continue
        if not parte.isdigit() or not 0 <= int(parte) <= 23:
            raise CommandError(f"Hora inválida: {parte!r}. Se esperan horas 0-23.")
        horas.append(int(parte))
    if not horas:
        raise CommandError("No se indicó ninguna hora.")
    return sorted(set(horas))


def proxima_corrida(horas: list[int], ahora: dt.datetime) -> dt.datetime:
    """La próxima hora programada estrictamente posterior a ``ahora``.

    Se calcula sobre la hora LOCAL: "a las 10" quiere decir a las diez de la
    mañana en el club, no en UTC.
    """
    for h in horas:
        candidata = ahora.replace(hour=h, minute=0, second=0, microsecond=0)
        if candidata > ahora:
            return candidata
    manana = ahora + dt.timedelta(days=1)
    return manana.replace(hour=horas[0], minute=0, second=0, microsecond=0)


class Command(BaseCommand):
    help = "Recalcula la foto de deuda que muestra el tablero."

    def add_arguments(self, parser):
        parser.add_argument(
            "--usuario", default="",
            help="Queda asentado en la foto. Vacío = tarea programada.")
        parser.add_argument(
            "--horas", default="",
            help=f"Horas del día en que correr, separadas por coma "
                 f"(ej. {HORAS_DEFECTO}). Si se indica, el comando NO termina.")
        parser.add_argument(
            "--al-arrancar", action="store_true",
            help="En modo programado, calcula también al levantar el proceso.")

    # ------------------------------------------------------------------ #
    def handle(self, *args, **opciones):
        if not opciones.get("horas"):
            self._una_vez(opciones.get("usuario") or "", fatal=True)
            return

        horas = _parsear_horas(opciones["horas"])
        self.stdout.write("Recálculo de deuda programado para las %s de cada día." %
                          ", ".join("%02d:00" % h for h in horas))

        if opciones.get("al_arrancar"):
            self._una_vez("", fatal=False)

        while True:
            ahora = timezone.localtime()
            proxima = proxima_corrida(horas, ahora)
            espera = (proxima - ahora).total_seconds()
            self.stdout.write("Próxima corrida: %s (en %.1f h)" %
                              (proxima.strftime("%d/%m %H:%M"), espera / 3600.0))
            # Se duerme de a tramos y no de un saque: así un cambio de hora o un
            # reloj que se corrige no dejan al proceso durmiendo doce horas de más.
            while espera > 0:
                time.sleep(min(espera, 300))
                espera = (proxima - timezone.localtime()).total_seconds()
            self._una_vez("", fatal=False)

    # ------------------------------------------------------------------ #
    def _una_vez(self, usuario: str, *, fatal: bool):
        try:
            foto = recalcular_deuda(usuario=usuario)
        except TableroError as exc:
            if fatal:
                raise CommandError(str(exc)) from exc
            self.stderr.write(self.style.ERROR(
                "%s - falló el recálculo: %s" %
                (timezone.localtime().strftime("%d/%m %H:%M"), exc)))
            return None

        self.stdout.write(self.style.SUCCESS(
            "Foto de deuda al %s: %s personas, %s comprobantes impagos, $ %s "
            "(%.1f s)" % (
                timezone.localtime(foto.calculado_at).strftime("%d/%m/%Y %H:%M"),
                self._miles(foto.socios),
                self._miles(foto.cupones),
                self._pesos(foto.importe),
                foto.duracion_ms / 1000.0,
            )))
        return foto

    @staticmethod
    def _miles(n) -> str:
        return f"{n:,}".replace(",", ".")

    @staticmethod
    def _pesos(n) -> str:
        return f"{n:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")

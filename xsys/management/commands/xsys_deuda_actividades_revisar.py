"""Libera a los bloqueados por deuda de actividades que ya pagaron.

    python manage.py xsys_deuda_actividades_revisar
        Sólo informa. No toca nada. Es el modo por defecto a propósito: lo que
        está en juego es dejar entrar gente al club.

    python manage.py xsys_deuda_actividades_revisar --aplicar
        Aplica los cambios y sale.

    python manage.py xsys_deuda_actividades_revisar --aplicar --interval 600
        Se queda corriendo y revisa cada 10 minutos. Es lo que hace el
        contenedor `deuda-actividades`.

Corre seguido —y no cuatro veces por día como la foto de deuda— porque consulta
sólo a las 163 personas de la tabla de bloqueo y vuelve en menos de un segundo.
La diferencia práctica es "pagaste y en diez minutos entrás" contra "pagaste y
mañana vemos".

Ver ``xsys.services.deuda_actividades`` para el criterio con el que decide.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError

from xsys.services.deuda_actividades import RevisionError, revisar


class Command(BaseCommand):
    help = ("Revisa en vivo quiénes de los bloqueados por deuda de actividades "
            "ya pagaron, y los libera.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--aplicar", action="store_true",
            help="Escribe los cambios en xSys. Sin esto sólo informa.")
        parser.add_argument(
            "--interval", type=float, default=0.0,
            help="Segundos entre corridas. 0 (default) = una sola vez y sale.")
        parser.add_argument(
            "--silencioso", action="store_true",
            help="En modo continuo, no imprime nada cuando no hubo cambios.")

    def handle(self, *args, **opciones):
        intervalo = max(0.0, opciones["interval"])
        aplicar = opciones["aplicar"]

        if not intervalo:
            self._una_vez(aplicar, opciones["silencioso"], fatal=True)
            return

        self.stdout.write(
            "Revisión de bloqueados por deuda de actividades cada %.0f s (%s)."
            % (intervalo, "aplicando" if aplicar else "sólo informa"))
        while True:
            self._una_vez(aplicar, opciones["silencioso"], fatal=False)
            time.sleep(intervalo)

    # ------------------------------------------------------------------ #
    def _una_vez(self, aplicar: bool, silencioso: bool, *, fatal: bool):
        try:
            inf = revisar(aplicar=aplicar)
        except RevisionError as exc:
            if fatal:
                raise CommandError(str(exc)) from exc
            # En modo continuo no se muere: xSys se cae, vuelve, y el proceso
            # sigue. Un contenedor muerto por un corte de red dejaría a los que
            # pagaron sin poder entrar hasta que alguien lo note.
            self.stderr.write(self.style.ERROR("falló la revisión: %s" % exc))
            return

        hubo = inf["regularizados"] or inf["bajados_a_aviso"]
        if silencioso and not hubo:
            return

        self.stdout.write(
            "Revisados %s con bloqueo activo | siguen bloqueados: %s | "
            "sin cambio: %s" % (inf["revisados"], inf["siguen_bloqueados"],
                                inf["sin_cambio"]))

        for c in inf["regularizados"]:
            self.stdout.write(self.style.SUCCESS(
                "   LIBERA  %-8s %-34s debía %s cuota(s), hoy 0" % (
                    c["id_cliente"], (c["nombre"] or "?")[:34],
                    c["cuotas_planilla"])))
        for c in inf["bajados_a_aviso"]:
            self.stdout.write(
                "   AVISO   %-8s %-34s debía %s, hoy %s: pasa pero queda marcado"
                % (c["id_cliente"], (c["nombre"] or "?")[:34],
                   c["cuotas_planilla"], c["cuotas_hoy"]))

        if hubo and not aplicar:
            self.stdout.write(self.style.WARNING(
                "   (modo informe: no se escribió nada. Con --aplicar se hace.)"))
        elif aplicar:
            self.stdout.write("   filas actualizadas en xSys: %s" % inf["aplicados"])

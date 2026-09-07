"""Chequeo: quién puede entrar pero no tiene con qué identificarse.

Corre solo con cada sincronización (cada 6 h). Este comando es para mirarlo a
mano o para correrlo aparte.

    python manage.py xsys_chequeo_identificacion            # revisa y deja avisos
    python manage.py xsys_chequeo_identificacion --dry-run  # sólo lista, no escribe
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from xsys.services import chequeo_identificacion


class Command(BaseCommand):
    help = "Detecta socios habilitados a entrar que no tienen documento, credencial ni facial."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Lista los casos sin crear ni resolver avisos.")

    def handle(self, *args, **opts):
        filas = chequeo_identificacion.sin_identificacion()

        if not filas:
            self.stdout.write(self.style.SUCCESS(
                "Nadie con contrato vigente quedó sin forma de identificarse."))
        for f in filas:
            alta = f["fecha_alta"].strftime("%d/%m/%Y") if f["fecha_alta"] else "—"
            self.stdout.write(
                "  %-8s %-34s %-18s alta %s  %s" % (
                    f["id_cliente"], f["nombre"][:34], (f["categoria"] or "")[:18],
                    alta, ", ".join(f["contratos"])[:50]))
            if f["email"]:
                self.stdout.write("           %s" % f["email"])

        stats = chequeo_identificacion.revisar(dry_run=opts["dry_run"])
        self.stdout.write("")
        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("--dry-run: no se escribió nada."))
        for k, v in stats.items():
            self.stdout.write(f"  {k}: {v}")

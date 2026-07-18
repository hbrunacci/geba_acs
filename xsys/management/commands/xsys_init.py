"""Carga inicial del espejo local de xSys.

Ejemplos:
    python manage.py xsys_init
    python manage.py xsys_init --seed-whitelist --no-recompute-whitelist
    python manage.py xsys_init --with-movements
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from xsys.services import XsysConnectionError, XsysSyncService


class Command(BaseCommand):
    help = "Población inicial del espejo local de xSys (socios, fotos, lista blanca)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seed-whitelist",
            action="store_true",
            help="Siembra rápida de la lista blanca desde CD_Lista_Blanca_Suprema.",
        )
        parser.add_argument(
            "--no-recompute-whitelist",
            action="store_true",
            help="No recalcular la lista blanca socio por socio (parte pesada).",
        )
        parser.add_argument(
            "--with-movements",
            action="store_true",
            help="También sincroniza la ventana reciente de CD_ES.",
        )

    def handle(self, *args, **options):
        service = XsysSyncService()
        try:
            stats = service.initial_load(
                with_movements=options["with_movements"],
                seed_whitelist=options["seed_whitelist"],
                recompute_whitelist=not options["no_recompute_whitelist"],
            )
        except XsysConnectionError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS("Carga inicial completada:"))
        for key, value in stats.items():
            self.stdout.write(f"  {key}: {value}")

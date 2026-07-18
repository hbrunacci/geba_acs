"""Sincronización incremental del espejo local de xSys (por novedades).

Pensado para correr por cron. Lee CD_Clientes_Novedades por high-water-mark de
Id_Novedad (sin tocar Estado), refresca los socios/fotos afectados, recalcula su
lista blanca y avanza los movimientos (CD_ES).

Ejemplos:
    python manage.py xsys_sync
    python manage.py xsys_sync --limit 500
    python manage.py xsys_sync --full-whitelist
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from xsys.models import SyncState
from xsys.services import XsysConnectionError, XsysSyncService


class Command(BaseCommand):
    help = "Sincronización incremental del espejo local de xSys (por novedades)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Máximo de novedades a procesar.")
        parser.add_argument(
            "--full-whitelist",
            action="store_true",
            help="Recalcula la lista blanca de TODOS los socios activos (costoso).",
        )

    def handle(self, *args, **options):
        service = XsysSyncService()
        try:
            stats = service.incremental(
                limit=options["limit"],
                full_whitelist=options["full_whitelist"],
            )
        except XsysConnectionError as exc:
            # Degradación limpia: sin ruta al MSSQL no se committea nada parcial.
            state = SyncState.get("novedades")
            state.last_run_ok = False
            state.last_error = str(exc)[:2000]
            state.save(update_fields=["last_run_ok", "last_error"])
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS("Sync incremental completada:"))
        for key, value in stats.items():
            self.stdout.write(f"  {key}: {value}")

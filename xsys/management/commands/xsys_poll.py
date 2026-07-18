"""Poller en tiempo real de la tabla de accesos (CD_ES) de xSys.

Es el ÚNICO cliente que consulta MSSQL cada ~1s: lee los nuevos registros de
CD_ES por high-water Id_ES y los persiste localmente. Las pantallas de los
puestos consultan después el espejo local (no MSSQL). Reconecta ante caídas.

Ejemplo (contenedor `poller`):
    python manage.py xsys_poll --interval 1
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from xsys.services import XsysConnectionError
from xsys.services.mssql import connect
from xsys.services.sync import XsysSyncService


class Command(BaseCommand):
    help = "Poll en tiempo real de CD_ES hacia el espejo local (single-client)."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=1.0, help="Segundos entre consultas (default 1).")
        parser.add_argument("--reconnect-delay", type=float, default=5.0, help="Espera al reconectar tras un error.")

    def handle(self, *args, **options):
        interval = max(0.2, options["interval"])
        reconnect_delay = options["reconnect_delay"]
        service = XsysSyncService()
        self.stdout.write(self.style.SUCCESS(f"Iniciando poller CD_ES cada {interval}s. Ctrl-C para salir."))

        try:
            while True:
                try:
                    conn = connect(service.config)
                except XsysConnectionError as exc:
                    self.stderr.write(f"Sin conexión a xSys ({exc}); reintento en {reconnect_delay}s")
                    time.sleep(reconnect_delay)
                    continue

                cursor = conn.cursor()
                try:
                    while True:
                        n = service.sync_movements(cursor)
                        if n:
                            self.stdout.write(f"+{n} movimientos")
                        time.sleep(interval)
                except Exception as exc:  # pragma: no cover - caída de conexión
                    self.stderr.write(f"Error en el poll ({exc}); reconectando en {reconnect_delay}s")
                    time.sleep(reconnect_delay)
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
        except KeyboardInterrupt:
            self.stdout.write("\nPoller detenido.")

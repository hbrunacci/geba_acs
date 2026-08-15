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

from common.dbhealth import reset_db_connections
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

        # El poller mantiene UNA conexión viva en un loop largo. El pooling de ODBC
        # (default ON) devuelve conexiones MUERTAS del pool tras un corte de red:
        # connect() entrega el cadáver, el primer uso da "connection already closed"
        # y el poller queda en un loop infinito de reconexión que nunca se recupera
        # (visto en prod 2026-08-07, ~1h sin replicar). Sin pooling, cada connect()
        # abre un socket fresco y el reconnect SÍ se recupera solo.
        try:  # pragma: no cover - depende del entorno
            import pyodbc

            pyodbc.pooling = False
        except Exception:
            pass

        self.stdout.write(self.style.SUCCESS(f"Iniciando poller CD_ES cada {interval}s. Ctrl-C para salir."))

        try:
            while True:
                # (Re)conectar con VALIDACIÓN: un SELECT 1 descarta cualquier
                # conexión muerta antes de entrar al loop de poll.
                conn = None
                try:
                    conn = connect(service.config)
                    probe = conn.cursor()
                    probe.execute("SELECT 1")
                    probe.fetchall()
                    cursor = conn.cursor()
                except Exception as exc:
                    self.stderr.write(f"Sin conexión a xSys ({exc}); reintento en {reconnect_delay}s")
                    reset_db_connections()
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                    time.sleep(reconnect_delay)
                    continue

                last_purge = time.monotonic()
                try:
                    while True:
                        n = service.sync_movements(cursor)
                        if n:
                            self.stdout.write(f"+{n} movimientos")
                        # Purga horaria de la ventana de retención (CD_ES última
                        # semana), self-healing aunque el sync de 6h no corra.
                        if time.monotonic() - last_purge >= 3600:
                            purged = service.purge_old_movements()
                            if purged:
                                self.stdout.write(f"-{purged} movimientos fuera de ventana")
                            # Las reservas de paso duran segundos; sin purga la
                            # tabla sólo crece.
                            try:
                                from access_control.services import paso_pendiente as pp

                                pp.purgar()
                            except Exception:
                                pass
                            last_purge = time.monotonic()
                        time.sleep(interval)
                except Exception as exc:  # pragma: no cover - caída de conexión
                    self.stderr.write(f"Error en el poll ({exc}); reconectando en {reconnect_delay}s")
                    # El error puede venir de Postgres, no de MSSQL: acá abajo se
                    # reconecta xSys, pero si no se descarta también la conexión de
                    # Django el poller queda en un lazo infinito de "connection
                    # already closed" (12-08-2026: 12 h sin movimientos de CD_ES).
                    reset_db_connections()
                    time.sleep(reconnect_delay)
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
        except KeyboardInterrupt:
            self.stdout.write("\nPoller detenido.")

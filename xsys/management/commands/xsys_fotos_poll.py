"""Poll de refresco de fotos del espejo, INDEPENDIENTE de las novedades.

Cierra el hueco por el que las fotos quedaban desactualizadas: el poller de
CD_ES solo replica accesos y el sync de 6h solo baja fotos de socios afectados
por novedad. Este proceso trae, cada ~8 min, las fotos de socios activos con
``Fecha`` posterior al high-water y re-enrola el rostro en BioStar si cambió.

Ejemplo (contenedor `fotos-poller`):
    python manage.py xsys_fotos_poll --interval 480
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from xsys.services.mssql import connect
from xsys.services.sync import XsysSyncService


class Command(BaseCommand):
    help = "Refresca las fotos del espejo por high-water de Fecha, cada N segundos."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=480.0,
                            help="Segundos entre corridas (default 480 = 8 min).")
        parser.add_argument("--reconnect-delay", type=float, default=15.0,
                            help="Espera al reconectar tras un error.")

    def handle(self, *args, **options):
        interval = max(30.0, options["interval"])
        reconnect_delay = options["reconnect_delay"]
        service = XsysSyncService()

        # Mismo blindaje que xsys_poll: sin pooling de ODBC, cada connect() abre un
        # socket fresco y el reconnect se recupera solo tras un corte de red.
        try:  # pragma: no cover - depende del entorno
            import pyodbc

            pyodbc.pooling = False
        except Exception:
            pass

        self.stdout.write(self.style.SUCCESS(
            f"Iniciando refresco de fotos cada {interval}s. Ctrl-C para salir."))

        try:
            while True:
                conn = None
                try:
                    conn = connect(service.config)
                    probe = conn.cursor()
                    probe.execute("SELECT 1")
                    probe.fetchall()
                    cursor = conn.cursor()
                except Exception as exc:
                    self.stderr.write(f"Sin conexión a xSys ({exc}); reintento en {reconnect_delay}s")
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                    time.sleep(reconnect_delay)
                    continue

                try:
                    while True:
                        changed = service.sync_fotos_incremental(cursor)
                        if changed:
                            self.stdout.write(f"fotos actualizadas: {len(changed)}")
                            # Re-enrolar el rostro en BioStar (best-effort; respeta
                            # BIOSTAR_FACE_SYNC_MODE, no corta el refresco si falla).
                            try:
                                from access_control.services.biostar_face_sync import push_faces_affected

                                res = push_faces_affected(changed)
                                if res.get("enrolados") or res.get("creados") or res.get("errores"):
                                    self.stdout.write(f"  biostar_faces: {res}")
                            except Exception as exc:  # pragma: no cover - defensivo
                                self.stderr.write(f"  biostar_face_sync falló (no corta): {exc}")
                        time.sleep(interval)
                except Exception as exc:  # pragma: no cover - caída de conexión
                    self.stderr.write(f"Error en el refresco ({exc}); reconectando en {reconnect_delay}s")
                    time.sleep(reconnect_delay)
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
        except KeyboardInterrupt:
            self.stdout.write("\nRefresco de fotos detenido.")

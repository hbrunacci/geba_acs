"""Poller del log de eventos de BioStar hacia el espejo local.

BioStar es la ÚNICA fuente con identidad por-equipo de los accesos faciales
(en xSys/CD_ES todos colapsan en un controlador). Este poller consulta
``/api/events/search`` por cada facial y guarda solo los eventos de ACCESO
(identify/verify success/fail/denied) en ``BiostarAccessEvent``. El visor lee
después el espejo local. Reconecta ante caídas.

Ejemplo (contenedor `biostar-poller`):
    python manage.py biostar_poll --interval 3
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from access_control.services import biostar_events


class Command(BaseCommand):
    help = "Poll del log de eventos BioStar (accesos faciales) hacia el espejo local."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=3.0, help="Segundos entre ciclos (default 3).")
        parser.add_argument("--limit", type=int, default=200, help="Máx. eventos por device por ciclo.")
        parser.add_argument("--retention-days", type=int, default=7, help="Días de retención de eventos.")
        parser.add_argument("--reconnect-delay", type=float, default=10.0, help="Espera al reconectar tras un error.")
        parser.add_argument("--once", action="store_true", help="Un solo ciclo y termina (para pruebas).")

    def _client(self):
        from access_control.services.biostar2_client import BioStar2Client

        return BioStar2Client.from_db_and_env()

    def _devices(self, client) -> list[tuple[int, str]]:
        """Faciales a espejar: todos los devices de BioStar (id, nombre)."""
        d = client.list_devices()
        rows = (d.get("DeviceCollection") or d).get("rows") or []
        out = []
        for r in rows:
            try:
                out.append((int(r.get("id")), r.get("name") or ""))
            except (TypeError, ValueError):
                continue
        return out

    def handle(self, *args, **options):
        interval = max(0.5, options["interval"])
        limit = options["limit"]
        retention = options["retention_days"]
        reconnect_delay = options["reconnect_delay"]
        once = options["once"]

        self.stdout.write(self.style.SUCCESS(
            f"Iniciando poller BioStar cada {interval}s (limit {limit}/device). Ctrl-C para salir."
        ))

        event_types: dict = {}
        devices: list[tuple[int, str]] = []
        last_meta = 0.0
        last_purge = time.monotonic()

        try:
            while True:
                try:
                    client = self._client()
                    now = time.monotonic()
                    # Catálogo de tipos + lista de devices: refrescar cada ~10 min.
                    if not event_types or (now - last_meta) >= 600:
                        event_types = client.event_types()
                        devices = self._devices(client)
                        last_meta = now
                        self.stdout.write(f"meta: {len(event_types)} tipos, {len(devices)} devices")

                    total = 0
                    for dev_id, dev_name in devices:
                        total += biostar_events.ingest_device_events(
                            client, event_types, dev_id, dev_name, limit=limit
                        )
                    if total:
                        self.stdout.write(f"+{total} accesos faciales")

                    if time.monotonic() - last_purge >= 3600:
                        purged = biostar_events.purge_old(retention)
                        if purged:
                            self.stdout.write(f"-{purged} eventos fuera de ventana")
                        last_purge = time.monotonic()

                except Exception as exc:  # pragma: no cover - depende de red/BioStar
                    self.stderr.write(f"Error en el poll BioStar ({exc}); reintento en {reconnect_delay}s")
                    if once:
                        raise
                    time.sleep(reconnect_delay)
                    continue

                if once:
                    self.stdout.write("Ciclo único completado.")
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("\nPoller BioStar detenido.")

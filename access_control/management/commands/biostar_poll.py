"""Poller del log de eventos de BioStar hacia el espejo local.

BioStar es la ÚNICA fuente con identidad por-equipo de los accesos faciales
(en xSys/CD_ES todos colapsan en un controlador). Este poller consulta
``/api/events/search`` de forma INCREMENTAL (solo eventos con ``id`` mayor al
último procesado — high-water en ``BiostarPollState``) y guarda solo los
eventos de ACCESO (identify/verify success/fail/denied) en ``BiostarAccessEvent``.
El visor lee después el espejo local. Reconecta ante caídas.

Ejemplo (contenedor `biostar-poller`):
    python manage.py biostar_poll --interval 1
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from access_control.services import biostar_events


class Command(BaseCommand):
    help = "Poll incremental del log de eventos BioStar (accesos faciales) al espejo local."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=1.0, help="Segundos entre ciclos (default 1).")
        parser.add_argument("--limit", type=int, default=200, help="Máx. eventos por página por ciclo.")
        parser.add_argument("--backfill", type=int, default=1000, help="Eventos recientes a sembrar en el primer arranque.")
        parser.add_argument("--retention-days", type=int, default=7, help="Días de retención de eventos.")
        parser.add_argument("--reconnect-delay", type=float, default=10.0, help="Espera al reconectar tras un error.")
        parser.add_argument("--once", action="store_true", help="Un solo ciclo y termina (para pruebas).")

    def _client(self):
        from access_control.services.biostar2_client import BioStar2Client

        return BioStar2Client.from_db_and_env()

    def handle(self, *args, **options):
        from access_control.models import BiostarPollState

        interval = max(0.3, options["interval"])
        limit = options["limit"]
        backfill = options["backfill"]
        retention = options["retention_days"]
        reconnect_delay = options["reconnect_delay"]
        once = options["once"]

        self.stdout.write(self.style.SUCCESS(
            f"Iniciando poller BioStar incremental cada {interval}s. Ctrl-C para salir."
        ))

        event_types: dict = {}
        last_meta = 0.0
        last_purge = time.monotonic()

        try:
            while True:
                try:
                    client = self._client()
                    now = time.monotonic()
                    # Catálogo de tipos de evento: refrescar cada ~10 min.
                    if not event_types or (now - last_meta) >= 600:
                        event_types = client.event_types()
                        last_meta = now
                        self.stdout.write(f"meta: {len(event_types)} tipos de evento")

                    state = BiostarPollState.get_solo()

                    # Arranque en frío: sembrar el high-water con lo reciente.
                    if state.last_event_id == 0:
                        nuevos, mx = biostar_events.ingest_backfill(client, event_types, limit=backfill)
                        if mx:
                            state.last_event_id = mx
                            state.save(update_fields=["last_event_id", "updated_at"])
                        self.stdout.write(f"backfill: +{nuevos} accesos, high-water={state.last_event_id}")
                    else:
                        nuevos, new_last = biostar_events.ingest_new_events(
                            client, event_types, state.last_event_id, limit=limit
                        )
                        if new_last > state.last_event_id:
                            state.last_event_id = new_last
                            state.save(update_fields=["last_event_id", "updated_at"])
                        if nuevos:
                            self.stdout.write(f"+{nuevos} accesos faciales (high-water={state.last_event_id})")

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

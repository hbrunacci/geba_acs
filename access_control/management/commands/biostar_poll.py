"""Poller del log de eventos de BioStar hacia el espejo local.

BioStar es la ÚNICA fuente con identidad por-equipo de los accesos faciales
(en xSys/CD_ES todos colapsan en un controlador). Este poller consulta
``/api/events/search`` trayendo los eventos MÁS RECIENTES (por id descendente) y
deduplicando por ``biostar_id``, y guarda solo los de ACCESO (identify/verify
success/fail/denied) en ``BiostarAccessEvent``. El visor lee después el espejo.

Antes usaba high-water por ``after_id``, pero el ``id`` de eventos de BioStar NO
es monótono en el tiempo (hay bloques viejos con ids más altos, por reinicios de
su base): el high-water se estancaba y los pasos aparecían en ráfagas de minutos.
El tail-poll + dedup es inmune y da latencia de ~1 ciclo. Reconecta ante caídas.

Ejemplo (contenedor `biostar-poller`):
    python manage.py biostar_poll --interval 1
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from access_control.services import biostar_events


class Command(BaseCommand):
    help = "Poll de los eventos más recientes de BioStar (accesos faciales) al espejo local."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=1.0, help="Segundos entre ciclos (default 1).")
        parser.add_argument(
            "--limit",
            type=int,
            default=300,
            help="Cantidad de eventos más recientes a revisar por ciclo (dedup por id).",
        )
        parser.add_argument("--retention-days", type=int, default=7, help="Días de retención de eventos.")
        parser.add_argument("--reconnect-delay", type=float, default=10.0, help="Espera al reconectar tras un error.")
        parser.add_argument("--once", action="store_true", help="Un solo ciclo y termina (para pruebas).")

    def _client(self):
        from access_control.services.biostar2_client import BioStar2Client

        return BioStar2Client.from_db_and_env()

    def handle(self, *args, **options):
        interval = max(0.3, options["interval"])
        limit = options["limit"]
        retention = options["retention_days"]
        reconnect_delay = options["reconnect_delay"]
        once = options["once"]

        self.stdout.write(self.style.SUCCESS(
            f"Iniciando poller BioStar (más recientes) cada {interval}s. Ctrl-C para salir."
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

                    nuevos = biostar_events.ingest_recent(client, event_types, limit=limit)
                    if nuevos:
                        self.stdout.write(f"+{nuevos} accesos faciales")

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

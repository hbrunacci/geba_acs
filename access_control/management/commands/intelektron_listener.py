"""Listener por *polling* de marcas de un molinete Intelektron API-3000.

Modo "solo escuchar y loguear": abre el equipo cada ``--interval`` segundos,
lee ``list_marks`` y persiste en ``IntelektronEvent`` las marcas nuevas
(deduplicadas por contenido). No autoriza accesos.

Es la vía SEGURA (sin callbacks ctypes, que no tienen firma documentada). Para
push en tiempo real habría que implementar los callbacks de ``itk_open`` — ver
el README del wrapper.

Ejemplo:
    python manage.py intelektron_listener --ip 10.0.0.60 --dest-node 1 --interval 5
    python manage.py intelektron_listener --ip 10.0.0.60 --once   # una pasada, para probar
"""

from __future__ import annotations

import hashlib
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone


# Mapeos de códigos → nombre legible (constants.py del wrapper).
EVENT_NAMES = {
    0: "Desconocido", 1: "OK", 106: "Intruso", 146: "Huella inválida",
    108: "Deshabilitado", 150: "Password", 148: "Licencia", 107: "No autorizado",
}
DIRECTION_NAMES = {
    0: "Desconocido", 200: "Entrada", 201: "Salida",
    202: "Inter-entrada", 203: "Inter-salida", 204: "Software",
}


class Command(BaseCommand):
    help = "Escucha (polling) marcas de un molinete Intelektron y las guarda en IntelektronEvent."

    def add_arguments(self, parser):
        parser.add_argument("--ip", required=True, help="IP del molinete.")
        parser.add_argument("--port", type=int, default=3001, help="Puerto host TCP (default 3001).")
        parser.add_argument("--source-node", type=int, default=255, help="Nodo origen (default 255).")
        parser.add_argument("--dest-node", type=int, default=1, help="Nodo destino (default 1).")
        parser.add_argument("--interval", type=float, default=5.0, help="Segundos entre ciclos (default 5).")
        parser.add_argument("--batch", type=int, default=50, help="Marcas a leer por ciclo (default 50).")
        parser.add_argument("--start", type=int, default=0, help="start_position de list_marks (default 0).")
        parser.add_argument("--id-controlador", type=int, default=None, help="Id_Controlador xSys a asociar.")
        parser.add_argument("--retention-days", type=int, default=30, help="Días de retención de eventos.")
        parser.add_argument("--reconnect-delay", type=float, default=10.0, help="Espera tras un error.")
        parser.add_argument("--once", action="store_true", help="Un solo ciclo y termina (para pruebas).")

    def handle(self, *args, **options):
        ip = options["ip"]
        base = {
            "ip": ip,
            "port": options["port"],
            "source_node": options["source_node"],
            "dest_node": options["dest_node"],
        }
        params = {"start_position": options["start"], "records_to_list": options["batch"]}
        interval = options["interval"]
        once = options["once"]

        self.stdout.write(self.style.SUCCESS(f"Listener Intelektron iniciado contra {ip}:{options['port']} (nodo {options['dest_node']})."))

        cycles = 0
        while True:
            try:
                new_count = self._poll_once(base, params, options["id_controlador"])
                if new_count:
                    self.stdout.write(f"{timezone.now():%H:%M:%S} · {new_count} marca(s) nueva(s) de {ip}")
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING("Interrumpido por el usuario."))
                break
            except Exception as exc:  # conexión caída, equipo apagado, etc.
                self.stderr.write(self.style.ERROR(f"Error consultando {ip}: {exc}"))
                if once:
                    break
                time.sleep(options["reconnect_delay"])
                close_old_connections()
                continue

            cycles += 1
            if cycles % 20 == 0:
                self._purge_old(options["retention_days"])
            close_old_connections()

            if once:
                break
            time.sleep(interval)

    def _poll_once(self, base: dict, params: dict, id_controlador) -> int:
        from access_control.services.intelectron.api3000_console import execute_command
        from access_control.models import IntelektronEvent

        result = execute_command(command="list_marks", base=base, params=params)
        marks = result.get("marks", []) if isinstance(result, dict) else []
        new_count = 0
        for mark in marks:
            key = self._dedupe_key(base["ip"], mark)
            device_time = self._parse_time(mark.get("timestamp"))
            event_code = mark.get("event_code")
            direction = mark.get("direction")
            _, created = IntelektronEvent.objects.get_or_create(
                dedupe_key=key,
                defaults={
                    "device_ip": base["ip"],
                    "dest_node": base["dest_node"],
                    "id_controlador": id_controlador,
                    "access_id": mark.get("access_id"),
                    "event_code": event_code,
                    "event_name": EVENT_NAMES.get(event_code, ""),
                    "direction": direction,
                    "direction_name": DIRECTION_NAMES.get(direction, ""),
                    "source": mark.get("source"),
                    "device_time": device_time,
                    "raw": mark,
                },
            )
            if created:
                new_count += 1
        return new_count

    @staticmethod
    def _dedupe_key(ip: str, mark: dict) -> str:
        ts = mark.get("timestamp") or {}
        parts = [
            ip,
            str(ts.get("year")), str(ts.get("month")), str(ts.get("day")),
            str(ts.get("hour")), str(ts.get("minute")), str(ts.get("seconds")),
            str(mark.get("access_id")), str(mark.get("event_code")),
            str(mark.get("direction")), str(mark.get("source")),
        ]
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_time(ts: dict | None):
        if not ts:
            return None
        try:
            year = int(ts.get("year") or 0)
            if year < 100:
                year += 2000
            month = int(ts.get("month") or 0)
            day = int(ts.get("day") or 0)
            if not (year and month and day):
                return None
            naive = timezone.datetime(
                year, month, day,
                int(ts.get("hour") or 0), int(ts.get("minute") or 0), int(ts.get("seconds") or 0),
            )
            return timezone.make_aware(naive, timezone.get_current_timezone())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _purge_old(retention_days: int) -> None:
        from access_control.models import IntelektronEvent

        cutoff = timezone.now() - timezone.timedelta(days=retention_days)
        IntelektronEvent.objects.filter(created_at__lt=cutoff).delete()

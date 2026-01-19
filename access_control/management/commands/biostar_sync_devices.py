from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from access_control.models import BioStarDevice
from access_control.models import BioStarDeviceGroup
from access_control.services.biostar2_client import BioStar2Client


class Command(BaseCommand):
    help = "Sincroniza dispositivos (lectores) desde BioStar 2 a la base local."

    def handle(self, *args: Any, **options: Any) -> None:
        client = BioStar2Client.from_db_and_env()

        payload = client.list_devices()

        # BioStar suele devolver algo tipo {"DeviceCollection": {"rows": [...]} } o similar.
        # Lo dejamos robusto: intentamos extraer "rows" y si no, usamos lista directa.
        rows = None
        if isinstance(payload, dict):
            for key in ("DeviceCollection", "device_collection", "devices"):
                if key in payload and isinstance(payload[key], dict) and "rows" in payload[key]:
                    rows = payload[key]["rows"]
                    break
        if rows is None:
            rows = payload.get("rows") if isinstance(payload, dict) else payload

        if not isinstance(rows, list):
            raise RuntimeError(f"Formato inesperado de respuesta list_devices(): {payload}")

        created = 0
        updated = 0

        with transaction.atomic():
            for item in rows:
                if not isinstance(item, dict):
                    continue

                device_id = item.get("id") or item.get("device_id")
                if device_id is None:
                    continue

                raw_group = item.get("device_group_id") or item.get("device_group")

                group = None
                group_id_int = None
                group_name = None

                if isinstance(raw_group, dict):
                    group_id = raw_group.get("id")
                    group_name = raw_group.get("name")
                else:
                    group_id = raw_group

                # normalizar id a int
                try:
                    group_id_int = int(group_id) if group_id not in (None, "") else None
                except (TypeError, ValueError):
                    group_id_int = None

                if group_id_int is not None:
                    group = BioStarDeviceGroup.objects.filter(group_id=group_id_int).first()

                    # fallback: crear grupo si no existe todavía
                    if group is None:
                        group = BioStarDeviceGroup.objects.create(
                            group_id=group_id_int,
                            name=str(group_name or f"Grupo {group_id_int}"),
                            raw_payload=raw_group if isinstance(raw_group, dict) else {"id": group_id_int},
                        )

                defaults = {
                    "name": item.get("name", "") or "",
                    "device_type": (item.get("type") or item.get("device_type") or "")[:100],
                    "ip_addr": item.get("ip_addr") or item.get("ip") or None,
                    "status": item.get("status"),
                    "raw_payload": item,
                    "device_group": group,
                }

                obj, was_created = BioStarDevice.objects.update_or_create(
                    device_id=int(device_id),
                    defaults=defaults,
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS(f"Sync OK. created={created} updated={updated} total={created+updated}"))

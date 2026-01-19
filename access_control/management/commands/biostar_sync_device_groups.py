from django.core.management.base import BaseCommand
from django.db import transaction

from access_control.models import BioStarDeviceGroup
from access_control.services.biostar2_client import BioStar2Client


class Command(BaseCommand):
    help = "Sincroniza grupos de dispositivos desde BioStar 2"

    def handle(self, *args, **options):
        client = BioStar2Client.from_db_and_env()
        payload = client.list_device_groups()

        rows = payload.get("rows") or payload.get("DeviceGroupCollection", {}).get("rows")
        if not isinstance(rows, list):
            raise RuntimeError("Formato inesperado en device_groups")

        with transaction.atomic():
            for item in rows:
                BioStarDeviceGroup.objects.update_or_create(
                    group_id=item["id"],
                    defaults={
                        "name": item.get("name", ""),
                        "description": item.get("description", ""),
                        "raw_payload": item,
                    },
                )

        self.stdout.write(self.style.SUCCESS(f"Device groups sincronizados: {len(rows)}"))

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from access_control.models import BioStarUser
from access_control.services.biostar2_client import BioStar2Client


class Command(BaseCommand):
    help = "Sincroniza usuarios desde BioStar 2 a la base local."

    def handle(self, *args: Any, **options: Any) -> None:
        client = BioStar2Client.from_db_and_env()
        payload = client.list_users()

        rows = None
        if isinstance(payload, dict):
            for key in ("UserCollection", "user_collection", "users"):
                if key in payload and isinstance(payload[key], dict) and "rows" in payload[key]:
                    rows = payload[key]["rows"]
                    break
        if rows is None:
            rows = payload.get("rows") if isinstance(payload, dict) else payload

        if not isinstance(rows, list):
            raise RuntimeError(f"Formato inesperado de respuesta list_users(): {payload}")

        created = 0
        updated = 0

        with transaction.atomic():
            for item in rows:
                if not isinstance(item, dict):
                    continue

                user_id = item.get("id") or item.get("user_id")
                if user_id is None:
                    continue

                defaults = {
                    "user_unique_id": str(item.get("user_id") or item.get("user_unique_id") or ""),
                    "name": (item.get("name") or item.get("display_name") or "")[:255],
                    "email": (item.get("email") or "")[:254],
                    "phone": (item.get("phone") or item.get("mobile") or "")[:64],
                    "raw_payload": item,
                }

                _, was_created = BioStarUser.objects.update_or_create(
                    user_id=int(user_id),
                    defaults=defaults,
                )
                created += int(was_created)
                updated += int(not was_created)

        self.stdout.write(self.style.SUCCESS(f"Sync users OK. created={created} updated={updated} total={created+updated}"))

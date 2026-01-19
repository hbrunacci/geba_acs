from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from access_control.models import BioStarUser
from access_control.services.biostar2_client import BioStar2Client


class Command(BaseCommand):
    help = "Sincroniza usuarios desde BioStar 2 (paginado) sin duplicados."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument("--max-pages", type=int, default=0)  # 0 = sin limite (para debug)

    def handle(self, *args: Any, **options: Any) -> None:
        client = BioStar2Client.from_db_and_env()

        limit: int = options["limit"]
        max_pages: int = options["max_pages"]

        offset = 0
        page = 0
        seen_ids: set[int] = set()

        created = 0
        updated = 0
        skipped_duplicates = 0

        now = timezone.now()

        while True:
            page += 1
            if max_pages and page > max_pages:
                break

            payload = client.list_users(limit=limit, offset=offset)

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

            if not rows:
                break

            with transaction.atomic():
                for item in rows:
                    if not isinstance(item, dict):
                        continue

                    user_id = item.get("id") or item.get("user_id")
                    if user_id is None:
                        continue

                    # Normalizar a int
                    try:
                        user_id_int = int(user_id)
                    except (TypeError, ValueError):
                        continue

                    # Anti-repetidos dentro de la corrida (por si BioStar repite)
                    if user_id_int in seen_ids:
                        skipped_duplicates += 1
                        continue
                    seen_ids.add(user_id_int)

                    defaults = {
                        "user_unique_id": str(item.get("user_id") or item.get("user_unique_id") or ""),
                        "name": (item.get("name") or item.get("display_name") or "")[:255],
                        "email": (item.get("email") or "")[:254],
                        "phone": (item.get("phone") or item.get("mobile") or "")[:64],
                        "raw_payload": item,
                        "last_seen_at": now,
                        "is_active": True,
                    }

                    obj, was_created = BioStarUser.objects.update_or_create(
                        user_id=user_id_int,
                        defaults=defaults,
                    )
                    created += int(was_created)
                    updated += int(not was_created)

            self.stdout.write(
                f"page={page} offset={offset} fetched={len(rows)} created={created} updated={updated} dup_skipped={skipped_duplicates}"
            )

            # Si vino menos que el limite, era la ultima pagina
            if len(rows) < limit:
                break

            offset += limit

        # Marcar inactivos los que no se vieron en esta corrida (opcional, recomendado)
        BioStarUser.objects.filter(is_active=True).exclude(last_seen_at=now).update(is_active=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"Sync users OK. created={created} updated={updated} dup_skipped={skipped_duplicates} total_seen={len(seen_ids)}"
            )
        )

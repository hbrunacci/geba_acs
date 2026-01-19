from __future__ import annotations

from django.db import models


class BioStarUser(models.Model):
    """
    Cache local de usuarios/personas provenientes de BioStar 2.

    Nota:
    - user_id suele ser el identificador numérico interno de BioStar.
    - user_unique_id / user_id_str (según versión) puede venir como string.
    Guardamos raw_payload para no perder nada.
    """

    user_id = models.BigIntegerField(unique=True, db_index=True)

    user_unique_id = models.CharField(max_length=128, blank=True, default="")
    name = models.CharField(max_length=255, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=64, blank=True, default="")

    is_active = models.BooleanField(default=True, db_index=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    raw_payload = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "BioStar User"
        verbose_name_plural = "BioStar Users"

    def __str__(self) -> str:
        return f"{self.name or self.user_unique_id or 'User'} ({self.user_id})"

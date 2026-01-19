from __future__ import annotations
from django.db import models


class BioStarDeviceGroup(models.Model):
    """
    Grupo de dispositivos definido en BioStar 2
    (ej: Sede Central, Planta Baja, Depósito, etc.)
    """

    group_id = models.BigIntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")

    raw_payload = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "BioStar Device Group"
        verbose_name_plural = "BioStar Device Groups"

    def __str__(self) -> str:
        return self.name
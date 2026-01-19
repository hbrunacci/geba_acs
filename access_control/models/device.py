from __future__ import annotations

from django.db import models


class BioStarDevice(models.Model):
    """
    Cache local de dispositivos/lectores conocidos por BioStar 2.
    device_id es el ID interno de BioStar (no autoincremental nuestro).
    """

    device_id = models.BigIntegerField(unique=True, db_index=True)

    name = models.CharField(max_length=255, blank=True, default="")
    device_type = models.CharField(max_length=100, blank=True, default="")
    ip_addr = models.GenericIPAddressField(null=True, blank=True)
    status = models.IntegerField(null=True, blank=True)

    raw_payload = models.JSONField(default=dict, blank=True)

    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "BioStar Device"
        verbose_name_plural = "BioStar Devices"

    def __str__(self) -> str:
        return f"{self.name or 'Device'} ({self.device_id})"

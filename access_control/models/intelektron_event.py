from __future__ import annotations

from django.db import models
from django.utils import timezone


class IntelektronEvent(models.Model):
    """Evento (marca de acceso) leído de un molinete Intelektron API-3000.

    Lo puebla el comando ``intelektron_listener``, que hace *polling* de
    ``list_marks`` sobre un equipo y persiste las marcas nuevas (modo "solo
    escuchar y loguear", sin autorizar accesos). Las marcas no traen un id
    estable, así que la deduplicación es por contenido (``dedupe_key``).

    ``access_id`` es el identificador de acceso reportado por la placa; suele
    coincidir con la credencial/Id_Cliente de xSys.
    """

    # Hash de contenido (ip + fecha + access_id + evento + dirección + source).
    dedupe_key = models.CharField(max_length=80, unique=True)
    device_ip = models.CharField(max_length=40, db_index=True)
    dest_node = models.IntegerField(default=1)
    # Link opcional al controlador de xSys (XsysControlador.id_controlador).
    id_controlador = models.IntegerField(null=True, blank=True, db_index=True)

    access_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    event_code = models.IntegerField(null=True, blank=True)
    event_name = models.CharField(max_length=60, blank=True, default="")
    direction = models.IntegerField(null=True, blank=True)
    direction_name = models.CharField(max_length=60, blank=True, default="")
    source = models.IntegerField(null=True, blank=True)

    # Fecha/hora reportada por el equipo (puede venir desfasada por su zona).
    device_time = models.DateTimeField(null=True, blank=True, db_index=True)
    raw = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "intelektron_event"
        ordering = ("-device_time", "-id")
        indexes = [
            models.Index(fields=("device_ip", "-device_time")),
            models.Index(fields=("-created_at",)),
        ]
        verbose_name = "Evento Intelektron"
        verbose_name_plural = "Eventos Intelektron"

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"{self.device_ip} · acc {self.access_id} @ {self.device_time:%Y-%m-%d %H:%M:%S}"

from __future__ import annotations

from django.db import models
from django.utils import timezone


class BiostarAccessEvent(models.Model):
    """Espejo local de eventos de ACCESO de BioStar (identificación facial).

    Es la ÚNICA fuente con identidad por-equipo: ``CD_ES`` (xSys) colapsa todos
    los faciales en un único controlador, así que desde xSys no se puede saber
    por cuál facial pasó una persona. Este espejo se puebla con el comando
    ``biostar_poll`` consultando ``/api/events/search`` por device y guardando
    solo los eventos de acceso (identify/verify success/fail/denied), no el
    ruido de sincronización de lista blanca (UPDATE/DELETE/ENROLL).

    ``id_cliente`` == ``user_id`` de BioStar == ``Id_Cliente`` de xSys, así que
    el socio se resuelve contra el espejo local (``xsys.XsysSocio``).
    """

    # id del evento en BioStar (string). Clave de deduplicación / high-water.
    biostar_id = models.CharField(max_length=40, unique=True)
    device_id = models.BigIntegerField(db_index=True)
    device_name = models.CharField(max_length=255, blank=True, default="")
    id_cliente = models.BigIntegerField(null=True, blank=True, db_index=True)
    # Timestamp confiable: server_datetime (UTC real). El campo datetime del
    # evento viene desfasado por la zona horaria configurada en el equipo.
    fecha = models.DateTimeField(db_index=True)
    event_code = models.IntegerField(null=True, blank=True)
    event_name = models.CharField(max_length=120, blank=True, default="")
    permitido = models.BooleanField(default=True)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "biostar_access_event"
        ordering = ("-fecha", "-id")
        indexes = [
            models.Index(fields=("device_id", "-fecha")),
            models.Index(fields=("-fecha",)),
        ]
        verbose_name = "Evento de acceso BioStar"
        verbose_name_plural = "Eventos de acceso BioStar"

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"{self.device_name} · cli {self.id_cliente} @ {self.fecha:%Y-%m-%d %H:%M:%S}"

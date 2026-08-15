from __future__ import annotations

from django.db import models
from django.utils import timezone


class PasoPendiente(models.Model):
    """Reserva de paso: el socio validó en un molinete y todavía no lo cruzó.

    Una fila por socio (la última validación gana). Vive pocos segundos: sirve
    para que, si la misma credencial o el mismo rostro aparecen en OTRO molinete
    dentro de la ventana, ese segundo intento se marque como "Paso pendiente".

    Se guarda en base y no en memoria porque los eventos entran por dos procesos
    distintos —el poller de CD_ES y el de BioStar— y ambos tienen que ver el
    mismo estado.
    """

    id_cliente = models.BigIntegerField(primary_key=True)
    molinete_key = models.CharField(max_length=40)
    molinete_nombre = models.CharField(max_length=60, blank=True, default="")
    door_id = models.IntegerField(null=True, blank=True)
    origen = models.CharField(max_length=12, blank=True, default="")
    iniciado_en = models.DateTimeField(default=timezone.now)
    expira_en = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "acs_paso_pendiente"
        verbose_name = "Paso pendiente"
        verbose_name_plural = "Pasos pendientes"

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"{self.id_cliente} @ {self.molinete_nombre or self.molinete_key}"

    @property
    def vigente(self) -> bool:
        return self.expira_en > timezone.now()

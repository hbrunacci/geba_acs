from __future__ import annotations

from django.db import models
from django.utils import timezone


class SocioAviso(models.Model):
    """Aviso/registro que un operador deja sobre un socio desde el diagnóstico de
    facial: p.ej. "notificar que pase por Socios" (predefinido) o una nota libre.

    Queda asociado al ``id_cliente`` para poder revisarlo después (histórico de
    avisos del socio). No modifica xSys ni BioStar: es un registro local.
    """

    TIPO_PASE_POR_SOCIOS = "pase_por_socios"
    TIPO_LIBRE = "libre"

    TEXTO_PASE_POR_SOCIOS = "Notificar que pase por Socios"

    id_cliente = models.BigIntegerField(db_index=True)
    tipo = models.CharField(max_length=32, default=TIPO_LIBRE)
    texto = models.CharField(max_length=500)
    creado_por = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "socio_aviso"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("id_cliente", "-created_at"))]
        verbose_name = "Aviso de socio"
        verbose_name_plural = "Avisos de socios"

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"aviso {self.id_cliente}: {self.texto[:40]}"

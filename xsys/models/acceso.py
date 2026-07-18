from __future__ import annotations

from django.db import models
from django.utils import timezone


class XsysAcceso(models.Model):
    """Espejo local de `CD_Accesos` (las puertas / puntos de acceso)."""

    id_acceso = models.IntegerField(primary_key=True)
    descripcion = models.CharField(max_length=40, blank=True, default="")
    descripcion_corta = models.CharField(max_length=20, blank=True, default="")
    activo = models.SmallIntegerField(null=True, blank=True, db_index=True)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "xsys_acceso"
        verbose_name = "Acceso / puerta (xSys)"
        verbose_name_plural = "Accesos / puertas (xSys)"
        ordering = ("descripcion",)

    def __str__(self) -> str:  # pragma: no cover
        return self.descripcion or f"Acceso {self.id_acceso}"


class XsysMotivo(models.Model):
    """Espejo local de `CD_Motivos` (mensajes de validación para la pantalla)."""

    id_cd_motivo = models.IntegerField(primary_key=True)
    tipo = models.CharField(max_length=1, blank=True, default="")
    descripcion = models.CharField(max_length=200, blank=True, default="")
    descripcion_display = models.CharField(max_length=32, blank=True, default="")
    descripcion_pantalla = models.CharField(max_length=100, blank=True, default="")
    activo = models.SmallIntegerField(null=True, blank=True)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "xsys_motivo"
        verbose_name = "Motivo de acceso (xSys)"
        verbose_name_plural = "Motivos de acceso (xSys)"

    def __str__(self) -> str:  # pragma: no cover
        return self.descripcion_pantalla or self.descripcion or f"Motivo {self.id_cd_motivo}"

    @property
    def mensaje_pantalla(self) -> str:
        return self.descripcion_pantalla or self.descripcion_display or self.descripcion

from __future__ import annotations

from django.db import models
from django.utils import timezone


class XsysSocio(models.Model):
    """Espejo local curado de la tabla xSys `Clientes` (subset de accesos)."""

    id_cliente = models.IntegerField(primary_key=True)
    doc_nro = models.BigIntegerField(null=True, blank=True, db_index=True)
    apellido = models.CharField(max_length=100, blank=True, default="")
    nombre = models.CharField(max_length=100, blank=True, default="")
    razon_social = models.CharField(max_length=100, blank=True, default="")
    sexo = models.CharField(max_length=1, blank=True, default="")
    fecha_nac = models.DateTimeField(null=True, blank=True)
    email = models.CharField(max_length=600, blank=True, default="")
    activo = models.SmallIntegerField(null=True, blank=True, db_index=True)
    tipo_persona = models.CharField(max_length=1, blank=True, default="")
    credencial_nro = models.CharField(max_length=30, blank=True, default="", db_index=True)
    ult_cuota_paga = models.DateTimeField(null=True, blank=True)
    id_estado_cliente = models.SmallIntegerField(null=True, blank=True)
    id_cliente_externo = models.CharField(max_length=14, blank=True, default="")
    fecha_alta = models.DateTimeField(null=True, blank=True)
    fecha_baja = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "xsys_socio"
        verbose_name = "Socio (xSys)"
        verbose_name_plural = "Socios (xSys)"
        ordering = ("apellido", "nombre")

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        nombre = f"{self.apellido}, {self.nombre}".strip(", ")
        return nombre or self.razon_social or f"Cliente {self.id_cliente}"

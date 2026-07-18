from __future__ import annotations

from django.db import models
from django.utils import timezone


class XsysContrato(models.Model):
    """Espejo local de los contratos activos del socio (Contratos + Contratos_Tipos).

    Ej: CUOTA SOCIAL, DEPORTES FEDERADOS, COLONIA, ED NATACION, etc. Se usa en el
    modal del monitor para ver si el socio adquirió otros contratos.
    """

    id_contrato = models.IntegerField(primary_key=True)
    id_cliente = models.IntegerField(db_index=True)
    id_tipo_con = models.SmallIntegerField(null=True, blank=True)
    descripcion = models.CharField(max_length=50, blank=True, default="")
    fecha_alta = models.DateTimeField(null=True, blank=True)
    fecha_hasta = models.DateTimeField(null=True, blank=True)
    activo = models.SmallIntegerField(null=True, blank=True, db_index=True)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "xsys_contrato"
        verbose_name = "Contrato de socio (xSys)"
        verbose_name_plural = "Contratos de socios (xSys)"
        ordering = ("id_cliente", "descripcion")

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.descripcion} (cli {self.id_cliente})"

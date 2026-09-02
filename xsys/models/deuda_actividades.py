from __future__ import annotations

from django.db import models
from django.utils import timezone


class XsysDeudaActividades(models.Model):
    """Espejo de ``CD_Clientes_Deuda_Actividades``: deuda de cuotas de actividad.

    La deuda de actividades (atletismo, básquet, esgrima, hockey…) no la ve el
    control de acceso: sale de la facturación. El club la carga en xSys desde su
    planilla y ahí decide qué hacer con cada tanda:

    - ``bloquea = False``: la persona **pasa**, pero el visor la marca en
      amarillo con "Deuda de Actividades". Es la tanda de 2 y 3 cuotas.
    - ``bloquea = True``: la persona **no pasa**. ``CP_SCA_RegistrarAcceso`` la
      frena con el motivo 118 y ``MSSQLAccessCheckService`` hace lo mismo, para
      que la lista blanca del facial no la deje entrar por otro lado. Es la
      tanda de 4 cuotas.

    ``activo = False`` es el regularizado: deja de regir sin borrar el
    antecedente.
    """

    id_cliente = models.IntegerField(primary_key=True)
    cuotas = models.SmallIntegerField(null=True, blank=True)
    importe = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    actividad = models.CharField(max_length=60, blank=True, default="")
    bloquea = models.BooleanField(default=False, db_index=True)
    activo = models.BooleanField(default=True, db_index=True)
    origen = models.CharField(max_length=60, blank=True, default="")
    observacion = models.CharField(max_length=200, blank=True, default="")
    fecha_alta = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "xsys_deuda_actividades"
        verbose_name = "Deuda de actividades (xSys)"
        verbose_name_plural = "Deudas de actividades (xSys)"
        ordering = ("-bloquea", "id_cliente")

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        estado = "bloquea" if self.bloquea else "avisa"
        return f"{self.id_cliente}: {self.cuotas} cuota(s), {estado}"

    @property
    def mensaje(self) -> str:
        return "Deuda de Actividades"

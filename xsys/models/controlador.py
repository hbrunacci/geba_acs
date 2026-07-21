from __future__ import annotations

from django.db import models
from django.utils import timezone


class XsysControlador(models.Model):
    """Espejo local de `CD_Controladores` (molinetes / lectores / faciales).

    Cada controlador pertenece a un acceso (`id_acceso`). `Tipo_Cont`: K=molinete,
    F/W=facial (Suprema), L=lector libre.
    """

    id_controlador = models.IntegerField(primary_key=True)
    id_acceso = models.IntegerField(db_index=True)
    descripcion = models.CharField(max_length=100, blank=True, default="")
    tipo = models.CharField(max_length=1, blank=True, default="")
    tipo_cont = models.CharField(max_length=1, blank=True, default="")
    activo = models.SmallIntegerField(null=True, blank=True, db_index=True)
    # IP del controlador según xSys: Intelek_IP (configurada) o, si no, Ult_IP
    # (última IP vista). Vacío si xSys no tiene el dato.
    ip = models.CharField("IP", max_length=40, blank=True, default="")
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "xsys_controlador"
        verbose_name = "Controlador / molinete (xSys)"
        verbose_name_plural = "Controladores / molinetes (xSys)"
        ordering = ("id_acceso", "descripcion")

    def __str__(self) -> str:  # pragma: no cover
        return self.descripcion or f"Controlador {self.id_controlador}"

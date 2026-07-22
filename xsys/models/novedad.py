from __future__ import annotations

from django.db import models


class XsysNovedad(models.Model):
    """Auditoría local de la cola `CD_Clientes_Novedades` vista por el sync.

    Es solo lectura respecto de xSys: guardamos un snapshot de ``estado_origen``
    pero NUNCA se escribe de vuelta a la base (la app legacy consume la cola).
    """

    id_novedad = models.IntegerField(primary_key=True)
    id_cliente = models.IntegerField(db_index=True, null=True, blank=True)
    fecha = models.DateTimeField(null=True, blank=True)
    estado_origen = models.CharField(max_length=1, blank=True, default="")
    tipo = models.CharField(max_length=1, blank=True, default="")
    nota = models.CharField(max_length=100, blank=True, default="")
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "xsys_novedad"
        verbose_name = "Novedad de socio (xSys)"
        verbose_name_plural = "Novedades de socios (xSys)"
        ordering = ("-id_novedad",)

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"Novedad #{self.id_novedad} (cliente {self.id_cliente})"

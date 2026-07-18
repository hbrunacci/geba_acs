from __future__ import annotations

from django.db import models
from django.utils import timezone


class XsysWhitelist(models.Model):
    """Lista blanca general local, recalculada por socio (no espejada de xSys).

    La verdad de ``habilitado`` se computa con la lógica de acceso
    (``MSSQLAccessCheckService``) para un acceso representativo (Cuota Social).
    """

    id_cliente = models.IntegerField(unique=True, db_index=True)
    habilitado = models.BooleanField(default=False)
    motivo_code = models.IntegerField(null=True, blank=True)
    motivo = models.CharField(max_length=120, blank=True, default="")
    detalle = models.CharField(max_length=120, blank=True, default="")
    id_acceso = models.IntegerField(null=True, blank=True)
    fecha_calculo = models.DateTimeField(default=timezone.now)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "xsys_whitelist"
        verbose_name = "Lista blanca general (xSys)"
        verbose_name_plural = "Lista blanca general (xSys)"
        indexes = [
            models.Index(fields=("habilitado",)),
        ]

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        estado = "OK" if self.habilitado else "NO"
        return f"{self.id_cliente} [{estado}] {self.motivo}"

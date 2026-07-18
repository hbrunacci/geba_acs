from __future__ import annotations

from django.db import models
from django.utils import timezone


class PuertaMolinete(models.Model):
    """Grupo administrable = una COLUMNA del monitor (un 'molinete').

    Pertenece a una puerta (``id_acceso``) y agrupa uno o varios controladores
    (``id_controladores``). Así se puede juntar en una misma columna el molinete y
    su facial (dos controladores, mismo lugar físico), o mostrar cada uno aparte.
    """

    id_acceso = models.IntegerField(db_index=True)
    nombre = models.CharField(max_length=60)
    id_controladores = models.JSONField(default=list)
    orden = models.IntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "xsys_puerta_molinete"
        verbose_name = "Molinete (columna del monitor)"
        verbose_name_plural = "Molinetes (columnas del monitor)"
        ordering = ("id_acceso", "orden", "id")

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.nombre} (acceso {self.id_acceso})"

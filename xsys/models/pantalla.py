from __future__ import annotations

from django.db import models
from django.utils import timezone


class PantallaPuerta(models.Model):
    """Config de una pantalla de puesto de control, identificada por un TOKEN.

    Cada pantalla genera un token propio (persistido en su navegador) y lo manda
    en cada request (header ``X-Pantalla-Token``). La hamburguesa de la UI setea
    qué puerta muestra la pantalla (``door``). La ``ip`` se guarda solo como dato
    informativo (de qué lugar viene el request).
    """

    token = models.CharField(max_length=64, unique=True, db_index=True)
    # Puerta local (institutions.AccessDoor) que muestra la pantalla. Sus columnas
    # son los grupos de molinetes definidos para esa puerta.
    door = models.ForeignKey(
        "institutions.AccessDoor",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pantallas",
        db_index=True,
    )
    nombre = models.CharField(max_length=60, blank=True, default="")
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "xsys_pantalla_puerta"
        verbose_name = "Pantalla / puesto (por token)"
        verbose_name_plural = "Pantallas / puestos (por token)"
        ordering = ("nombre", "token")

    def __str__(self) -> str:  # pragma: no cover
        etiqueta = self.nombre or self.token[:8]
        return f"{etiqueta} → puerta {self.door_id}"

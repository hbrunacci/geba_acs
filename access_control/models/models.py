from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from people.models import PersonType


class WhitelistEntry(models.Model):
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="whitelist_entries",
    )
    access_point = models.ForeignKey(
        "institutions.AccessPoint",
        on_delete=models.CASCADE,
        related_name="whitelist_entries",
    )
    event = models.ForeignKey(
        "institutions.Event",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="whitelist_entries",
        help_text="Evento asociado si la autorización es específica para un evento.",
    )
    is_allowed = models.BooleanField(default=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Entrada en lista blanca"
        verbose_name_plural = "Lista blanca"
        unique_together = ("person", "access_point", "event")

    def __str__(self):
        event = f" - {self.event.name}" if self.event else ""
        return f"{self.person} @ {self.access_point}{event}"

    def clean(self):
        super().clean()
        if self.event:
            if self.event.site != self.access_point.site:
                raise ValidationError(
                    {"event": "El evento debe pertenecer a la misma sede del punto de acceso."}
                )
            person_type = self.person.person_type
            if person_type == PersonType.GUEST:
                if self.person.guest_type not in self.event.allowed_guest_types:
                    raise ValidationError(
                        {
                            "event": "El invitado no coincide con los tipos de invitados permitidos para el evento.",
                        }
                    )
            else:
                if person_type not in self.event.allowed_person_types:
                    raise ValidationError(
                        {
                            "event": "La persona no pertenece a una categoría permitida para el evento.",
                        }
                    )


class ExternalAccessLogEntry(models.Model):
    """Persistencia local de los movimientos provenientes del sistema externo."""

    external_id = models.BigIntegerField(unique=True)
    tipo = models.CharField(max_length=4, blank=True)
    origen = models.CharField(max_length=8, blank=True)
    id_tarjeta = models.CharField(max_length=64, blank=True)
    id_cliente = models.BigIntegerField(null=True, blank=True)
    fecha = models.DateTimeField()
    resultado = models.CharField(max_length=4, blank=True)
    id_controlador = models.BigIntegerField(null=True, blank=True)
    id_acceso = models.BigIntegerField(null=True, blank=True)
    observacion = models.CharField(max_length=255, blank=True)
    tipo_registro = models.CharField(max_length=32, blank=True)
    id_cd_motivo = models.BigIntegerField(null=True, blank=True)
    flag_permite_paso = models.CharField(max_length=4, blank=True)
    fecha_paso_permitido = models.DateTimeField(null=True, blank=True)
    id_controlador_paso_permitido = models.BigIntegerField(null=True, blank=True)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-fecha", "-external_id")
        indexes = [
            models.Index(fields=("-fecha",)),
            models.Index(fields=("external_id",)),
        ]
        verbose_name = "Movimiento externo sincronizado"
        verbose_name_plural = "Movimientos externos sincronizados"

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"#{self.external_id} @ {self.fecha:%Y-%m-%d %H:%M:%S}"



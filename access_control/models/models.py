from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from people.models import PersonType


class WhitelistEntry(models.Model):
    class Recurrence(models.TextChoices):
        NONE = "none", "Sin recurrencia"
        DAILY = "daily", "Diaria"
        WEEKLY = "weekly", "Semanal"

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
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    recurrence = models.CharField(
        max_length=16,
        choices=Recurrence.choices,
        default=Recurrence.NONE,
    )
    recurrence_days = models.JSONField(
        null=True,
        blank=True,
        help_text="Días de la semana (0=Lunes ... 6=Domingo) para recurrencia semanal.",
    )
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
        errors: dict[str, str] = {}
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            errors["valid_until"] = "La fecha de fin no puede ser anterior a la de inicio."
        if (self.start_time is None) ^ (self.end_time is None):
            errors["start_time"] = "Debe definir un rango horario completo (inicio y fin)."
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            errors["end_time"] = "La hora de fin debe ser posterior a la hora de inicio."
        if self.recurrence == self.Recurrence.WEEKLY:
            if not self.recurrence_days:
                errors["recurrence_days"] = "Debe indicar los días de la semana para la recurrencia semanal."
            else:
                invalid_days = [
                    day
                    for day in self.recurrence_days
                    if not isinstance(day, int) or day < 0 or day > 6
                ]
                if invalid_days:
                    errors["recurrence_days"] = "Los días de recurrencia deben ser enteros entre 0 y 6."
        elif self.recurrence_days:
            errors["recurrence_days"] = "Solo se permiten días de recurrencia cuando la recurrencia es semanal."
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
        if errors:
            raise ValidationError(errors)
        self._validate_overlaps()

    def _validate_overlaps(self) -> None:
        if not self.person_id or not self.access_point_id:
            return
        overlaps = WhitelistEntry.objects.filter(
            person=self.person,
            access_point=self.access_point,
        ).exclude(pk=self.pk)
        date_overlap = self._date_overlap_query()
        if date_overlap is not None:
            overlaps = overlaps.filter(date_overlap)
        time_overlap = self._time_overlap_query()
        if time_overlap is not None:
            overlaps = overlaps.filter(time_overlap)
        overlaps = overlaps.exclude(is_allowed=self.is_allowed)
        if self.recurrence == self.Recurrence.WEEKLY:
            weekdays = set(self.recurrence_days or [])
            weekly_entries = overlaps.filter(recurrence=self.Recurrence.WEEKLY)
            non_weekly_entries = overlaps.exclude(recurrence=self.Recurrence.WEEKLY)
            conflicts = list(non_weekly_entries)
            conflicts.extend(
                [
                    entry
                    for entry in weekly_entries
                    if weekdays.intersection(entry.recurrence_days or [])
                ]
            )
        else:
            conflicts = list(overlaps)
        if conflicts:
            raise ValidationError(
                {
                    "__all__": "Existe una autorización contradictoria con el mismo rango de fechas/horarios."
                }
            )

    def _date_overlap_query(self) -> models.Q | None:
        if self.valid_from and self.valid_until:
            return (
                (models.Q(valid_from__isnull=True) | models.Q(valid_from__lte=self.valid_until))
                & (models.Q(valid_until__isnull=True) | models.Q(valid_until__gte=self.valid_from))
            )
        if self.valid_from:
            return models.Q(valid_until__isnull=True) | models.Q(valid_until__gte=self.valid_from)
        if self.valid_until:
            return models.Q(valid_from__isnull=True) | models.Q(valid_from__lte=self.valid_until)
        return None

    def _time_overlap_query(self) -> models.Q | None:
        if self.start_time and self.end_time:
            return (
                models.Q(start_time__isnull=True, end_time__isnull=True)
                | models.Q(start_time__isnull=True, end_time__gte=self.start_time)
                | models.Q(end_time__isnull=True, start_time__lte=self.end_time)
                | models.Q(start_time__lte=self.end_time, end_time__gte=self.start_time)
            )
        return None


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

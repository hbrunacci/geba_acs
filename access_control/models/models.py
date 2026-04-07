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
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    days_of_week = models.JSONField(
        null=True,
        blank=True,
        help_text="Lista de días de la semana (0=Lunes, 6=Domingo) para recurrencia semanal.",
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
        if (self.start_time and not self.end_time) or (self.end_time and not self.start_time):
            errors["start_time"] = "Debe indicar hora de inicio y hora de fin juntas."
            errors["end_time"] = "Debe indicar hora de inicio y hora de fin juntas."
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            errors["start_time"] = "La hora de inicio debe ser anterior a la hora de fin."
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            errors["valid_until"] = "La fecha de inicio debe ser anterior o igual a la fecha de fin."
        if self.days_of_week is not None:
            if not isinstance(self.days_of_week, list) or not self.days_of_week:
                errors["days_of_week"] = "Debe ser una lista no vacía de días de la semana."
            else:
                invalid_days = [
                    day
                    for day in self.days_of_week
                    if not isinstance(day, int) or day not in range(7)
                ]
                if invalid_days:
                    errors["days_of_week"] = (
                        "Los días deben estar entre 0 (lunes) y 6 (domingo)."
                    )

        if self.event:
            if self.event.site != self.access_point.site:
                errors["event"] = "El evento debe pertenecer a la misma sede del punto de acceso."
            person_type = self.person.person_type
            if person_type == PersonType.GUEST:
                if self.person.guest_type not in self.event.allowed_guest_types:
                    errors["event"] = (
                        "El invitado no coincide con los tipos de invitados permitidos para el evento."
                    )
            else:
                if person_type not in self.event.allowed_person_types:
                    errors["event"] = (
                        "La persona no pertenece a una categoría permitida para el evento."
                    )

        if errors:
            raise ValidationError(errors)

        overlapping_entries = self._find_overlapping_entries()
        if overlapping_entries:
            raise ValidationError(
                {
                    "is_allowed": (
                        "Ya existe una autorización con horarios o fechas solapadas para la misma persona y acceso."
                    )
                }
            )

    def _find_overlapping_entries(self) -> list["WhitelistEntry"]:
        queryset = (
            WhitelistEntry.objects.filter(
                person=self.person,
                access_point=self.access_point,
            )
            .exclude(pk=self.pk)
            .exclude(is_allowed=self.is_allowed)
        )

        date_overlap_filters = []
        if self.valid_until is not None:
            date_overlap_filters.append(
                models.Q(valid_from__isnull=True) | models.Q(valid_from__lte=self.valid_until)
            )
        if self.valid_from is not None:
            date_overlap_filters.append(
                models.Q(valid_until__isnull=True) | models.Q(valid_until__gte=self.valid_from)
            )
        if date_overlap_filters:
            date_overlap = date_overlap_filters.pop()
            for condition in date_overlap_filters:
                date_overlap &= condition
            queryset = queryset.filter(date_overlap)

        if self.start_time and self.end_time:
            time_overlap = (
                models.Q(start_time__isnull=True)
                | models.Q(end_time__isnull=True)
                | (models.Q(start_time__lt=self.end_time) & models.Q(end_time__gt=self.start_time))
            )
            queryset = queryset.filter(time_overlap)

        candidates = list(queryset)
        if self.days_of_week is None:
            return candidates
        overlapping = []
        current_days = set(self.days_of_week or [])
        for entry in candidates:
            if entry.days_of_week is None:
                overlapping.append(entry)
                continue
            entry_days = set(entry.days_of_week or [])
            if current_days.intersection(entry_days):
                overlapping.append(entry)
        return overlapping


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


class AccessEvent(models.Model):
    person = models.ForeignKey("people.Person", on_delete=models.CASCADE, related_name="access_events")
    site = models.ForeignKey("institutions.Site", on_delete=models.CASCADE, related_name="access_events")
    category = models.ForeignKey(
        "people.PersonCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="access_events",
    )
    occurred_at = models.DateTimeField()
    source = models.CharField(max_length=32, default="manual")

    class Meta:
        ordering = ("-occurred_at",)
        indexes = [
            models.Index(fields=("site", "occurred_at")),
            models.Index(fields=("category", "occurred_at")),
        ]

    def __str__(self):
        return f"{self.person} @ {self.site} ({self.occurred_at:%Y-%m-%d %H:%M})"


class ParkingMovement(models.Model):
    class MovementType(models.TextChoices):
        ENTRY = "entry", "Ingreso"
        EXIT = "exit", "Salida"

    dni = models.BigIntegerField()
    patente = models.CharField(max_length=16)
    movement_type = models.CharField(max_length=10, choices=MovementType.choices)
    ult_cuota_paga = models.DateTimeField(null=True, blank=True)
    exit_at = models.DateTimeField(null=True, blank=True)
    stay_duration = models.DurationField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("dni", "-created_at")),
            models.Index(fields=("patente", "-created_at")),
        ]
        verbose_name = "Movimiento de estacionamiento"
        verbose_name_plural = "Movimientos de estacionamiento"

    def __str__(self) -> str:
        return f"{self.get_movement_type_display()} {self.patente} ({self.dni})"


class AnsesVerificationRecord(models.Model):
    id_cliente = models.BigIntegerField()
    dni = models.BigIntegerField()
    requested_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anses_verification_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("requested_by", "id_cliente")),
            models.Index(fields=("requested_by", "-created_at")),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("requested_by", "id_cliente"),
                name="uniq_anses_record_user_client",
            )
        ]
        verbose_name = "Consulta ANSES"
        verbose_name_plural = "Consultas ANSES"

    def __str__(self) -> str:
        return f"Cliente {self.id_cliente} (DNI {self.dni})"

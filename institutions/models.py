from django.core.exceptions import ValidationError
from django.db import models

from people.models import GuestType, PersonType


class Site(models.Model):
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)

    class Meta:
        verbose_name = "Sede"
        verbose_name_plural = "Sedes"
        ordering = ["name"]

    def __str__(self):
        return self.name


class AccessPoint(models.Model):
    site = models.ForeignKey("institutions.Site", on_delete=models.CASCADE, related_name="access_points")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "Acceso"
        verbose_name_plural = "Accesos"
        unique_together = ("site", "name")
        ordering = ["site__name", "name"]

    def __str__(self):
        return f"{self.site.name} - {self.name}"


class AccessDeviceType(models.TextChoices):
    TURNSTILE = "turnstile", "Molinetes"
    DOOR = "door", "Puerta"


class AccessDevice(models.Model):
    access_point = models.ForeignKey(
        "institutions.AccessPoint",
        on_delete=models.CASCADE,
        related_name="devices",
    )
    name = models.CharField(max_length=255)
    device_type = models.CharField(max_length=16, choices=AccessDeviceType.choices)
    has_credential_reader = models.BooleanField(default=False)
    has_qr_reader = models.BooleanField(default=False)
    has_facial_reader = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Dispositivo de acceso"
        verbose_name_plural = "Dispositivos de acceso"
        ordering = ["access_point__name", "name"]
        unique_together = ("access_point", "name")

    def __str__(self):
        return f"{self.name} ({self.get_device_type_display()})"


class Event(models.Model):
    name = models.CharField(max_length=255)
    site = models.ForeignKey("institutions.Site", on_delete=models.CASCADE, related_name="events")
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    allowed_person_types = models.JSONField(default=list, blank=True)
    allowed_guest_types = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-start_date", "name"]

    def clean(self):
        super().clean()
        invalid_person_types = [value for value in self.allowed_person_types if value not in PersonType.values]
        if invalid_person_types:
            raise ValidationError(
                {
                    "allowed_person_types": (
                        "Tipos de persona inválidos: " + ", ".join(sorted(set(invalid_person_types)))
                    )
                }
            )
        invalid_guest_types = [value for value in self.allowed_guest_types if value not in GuestType.values]
        if invalid_guest_types:
            raise ValidationError(
                {
                    "allowed_guest_types": (
                        "Tipos de invitado inválidos: " + ", ".join(sorted(set(invalid_guest_types)))
                    )
                }
            )

    def __str__(self):
        return f"{self.name} ({self.site.name})"

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


class DoorDeviceType(models.TextChoices):
    TURNSTILE = "turnstile", "Molinete"
    FACIAL = "facial", "Facial"
    CREDENTIAL_READER = "credential_reader", "Lector de credenciales"


class DeviceDirection(models.TextChoices):
    ENTRY = "entry", "Entrada"
    EXIT = "exit", "Salida"
    BOTH = "both", "Ambos"


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


class AccessDoor(models.Model):
    site = models.ForeignKey("institutions.Site", on_delete=models.CASCADE, related_name="doors")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=32)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["site__name", "name"]
        unique_together = ("site", "code")

    def __str__(self):
        return f"{self.site.name} - {self.name}"


class DoorDevice(models.Model):
    door = models.ForeignKey("institutions.AccessDoor", on_delete=models.CASCADE, related_name="devices")
    device_type = models.CharField(max_length=32, choices=DoorDeviceType.choices)
    vendor = models.CharField(max_length=128, blank=True)
    serial_number = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    direction = models.CharField(max_length=8, choices=DeviceDirection.choices, default=DeviceDirection.ENTRY)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["door__name", "id"]


class AccessZone(models.Model):
    site = models.ForeignKey("institutions.Site", on_delete=models.CASCADE, related_name="zones")
    name = models.CharField(max_length=255)
    ring_code = models.CharField(max_length=2)
    parent_zone = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["site__name", "ring_code"]
        unique_together = ("site", "ring_code")

    @property
    def ring_level(self):
        return int(self.ring_code[0])

    @property
    def ring_order(self):
        return int(self.ring_code[1])

    def clean(self):
        super().clean()
        errors = {}
        if len(self.ring_code or "") != 2 or not self.ring_code.isdigit():
            errors["ring_code"] = "El código de anillo debe tener exactamente 2 dígitos."
        if self.parent_zone and self.parent_zone.site_id != self.site_id:
            errors["parent_zone"] = "La zona padre debe pertenecer a la misma sede."
        if self.parent_zone and self.ring_level <= self.parent_zone.ring_level:
            errors["parent_zone"] = "La zona padre debe estar en un nivel jerárquico anterior."
        if errors:
            raise ValidationError(errors)


class DoorZoneControl(models.Model):
    door = models.ForeignKey("institutions.AccessDoor", on_delete=models.CASCADE, related_name="zone_controls")
    zone = models.ForeignKey("institutions.AccessZone", on_delete=models.CASCADE, related_name="door_controls")
    control_type = models.CharField(max_length=8, choices=DeviceDirection.choices, default=DeviceDirection.BOTH)

    class Meta:
        unique_together = ("door", "zone")


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

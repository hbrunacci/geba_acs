from django.core.exceptions import ValidationError
from django.db import models


class PersonType(models.TextChoices):
    MEMBER = "member", "Socio"
    EMPLOYEE = "employee", "Empleado"
    PROVIDER = "provider", "Proveedor"
    GUEST = "guest", "Invitado"


class GuestType(models.TextChoices):
    MEMBER_GUEST = "member_guest", "Invitado Acompañante Socio"
    EVENT_VISITOR = "event_visitor", "Invitado Visitante Evento"


class Person(models.Model):
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    dni = models.CharField(max_length=32, unique=True)
    address = models.CharField(max_length=255)
    phone = models.CharField(max_length=32)
    email = models.EmailField()
    credential_code = models.CharField(max_length=64, unique=True, null=True, blank=True)
    facial_enrolled = models.BooleanField(default=False)
    person_type = models.CharField(max_length=16, choices=PersonType.choices)
    guest_type = models.CharField(
        max_length=32,
        choices=GuestType.choices,
        null=True,
        blank=True,
        help_text="Requerido para personas de tipo invitado.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    def clean(self):
        super().clean()
        if self.person_type == PersonType.GUEST and not self.guest_type:
            raise ValidationError({"guest_type": "Debe seleccionar el tipo de invitado."})
        if self.person_type != PersonType.GUEST and self.guest_type:
            raise ValidationError({"guest_type": "Solo los invitados pueden tener un tipo de invitado."})

    def __str__(self):
        return f"{self.last_name}, {self.first_name}"


class GuestInvitation(models.Model):
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="guest_invitations",
    )
    event = models.ForeignKey(
        "institutions.Event",
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    guest_type = models.CharField(max_length=32, choices=GuestType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Invitación"
        verbose_name_plural = "Invitaciones"
        unique_together = ("person", "event")

    def clean(self):
        super().clean()
        if self.person.person_type != PersonType.GUEST:
            raise ValidationError({"person": "Solo se pueden invitar personas registradas como invitados."})
        if self.guest_type != self.person.guest_type:
            raise ValidationError(
                {"guest_type": "El tipo de invitado debe coincidir con el tipo configurado en la persona."}
            )
        if self.event and self.guest_type not in self.event.allowed_guest_types:
            raise ValidationError({"event": "El evento no admite invitados de este tipo."})

    def __str__(self):
        return f"{self.person} -> {self.event}"

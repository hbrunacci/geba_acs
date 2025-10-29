from rest_framework import serializers

from .models import GuestInvitation, GuestType, Person, PersonType


class PersonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = [
            "id",
            "first_name",
            "last_name",
            "dni",
            "address",
            "phone",
            "email",
            "credential_code",
            "facial_enrolled",
            "person_type",
            "guest_type",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("created_at", "updated_at")

    def validate(self, attrs):
        person_type = attrs.get("person_type", getattr(self.instance, "person_type", None))
        guest_type = attrs.get("guest_type", getattr(self.instance, "guest_type", None))
        if person_type == PersonType.GUEST and not guest_type:
            raise serializers.ValidationError(
                {"guest_type": "Debe seleccionar el tipo de invitado para una persona invitada."}
            )
        if person_type != PersonType.GUEST and guest_type:
            raise serializers.ValidationError(
                {"guest_type": "Solo los invitados pueden tener un tipo de invitado."}
            )
        return attrs


class GuestInvitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = GuestInvitation
        fields = ["id", "person", "event", "guest_type", "created_at"]
        read_only_fields = ("created_at",)

    def validate(self, attrs):
        person = attrs.get("person", getattr(self.instance, "person", None))
        guest_type = attrs.get("guest_type", getattr(self.instance, "guest_type", None))
        event = attrs.get("event", getattr(self.instance, "event", None))
        if person and person.person_type != PersonType.GUEST:
            raise serializers.ValidationError(
                {"person": "Solo se pueden invitar personas registradas como invitadas."}
            )
        if person and guest_type and person.guest_type != guest_type:
            raise serializers.ValidationError(
                {"guest_type": "El tipo de invitado debe coincidir con el configurado en la persona."}
            )
        if event and guest_type and guest_type not in event.allowed_guest_types:
            raise serializers.ValidationError(
                {"event": "El evento no admite invitados de este tipo."}
            )
        return attrs

from rest_framework import serializers

from access_control.models import BioStarDevice, BioStarUser

from access_control.models.models import ExternalAccessLogEntry, WhitelistEntry
from people.models import GuestType, PersonType


class WhitelistBatchCreateSerializer(serializers.Serializer):
    access_point_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=False,
    )
    site_id = serializers.IntegerField(min_value=1, required=False)
    event_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    person_type = serializers.ListField(
        child=serializers.ChoiceField(choices=PersonType.choices),
        required=False,
        allow_empty=False,
    )
    guest_type = serializers.ListField(
        child=serializers.ChoiceField(choices=GuestType.choices),
        required=False,
        allow_empty=False,
    )
    is_active = serializers.BooleanField(required=False)
    is_allowed = serializers.BooleanField(required=False, default=True)
    valid_from = serializers.DateField(required=False, allow_null=True)
    valid_until = serializers.DateField(required=False, allow_null=True)
    preview = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        access_point_ids = attrs.get("access_point_ids")
        site_id = attrs.get("site_id")
        if not access_point_ids and not site_id:
            raise serializers.ValidationError(
                "Debe enviar 'access_point_ids' o 'site_id' para definir los accesos objetivo."
            )
        if access_point_ids and site_id:
            raise serializers.ValidationError(
                "No puede combinar 'access_point_ids' con 'site_id'; seleccione solo uno."
            )

        valid_from = attrs.get("valid_from")
        valid_until = attrs.get("valid_until")
        if valid_from and valid_until and valid_until < valid_from:
            raise serializers.ValidationError(
                "'valid_until' debe ser posterior o igual a 'valid_from'."
            )

        person_types = attrs.get("person_type") or []
        guest_types = attrs.get("guest_type") or []
        if guest_types and PersonType.GUEST not in person_types and person_types:
            raise serializers.ValidationError(
                "'guest_type' solo se puede usar cuando 'person_type' incluye invitados."
            )
        return attrs


class WhitelistEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = WhitelistEntry
        fields = [
            "id",
            "person",
            "access_point",
            "event",
            "is_allowed",
            "valid_from",
            "valid_until",
            "start_time",
            "end_time",
            "days_of_week",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("created_at", "updated_at")

    def validate(self, attrs):
        instance = WhitelistEntry(**attrs)
        start_time = attrs.get("start_time")
        end_time = attrs.get("end_time")
        valid_from = attrs.get("valid_from")
        valid_until = attrs.get("valid_until")
        days_of_week = attrs.get("days_of_week")

        if (start_time and not end_time) or (end_time and not start_time):
            raise serializers.ValidationError(
                "Debe indicar hora de inicio y hora de fin juntas."
            )
        if start_time and end_time and start_time >= end_time:
            raise serializers.ValidationError(
                "La hora de inicio debe ser anterior a la hora de fin."
            )

        if valid_from and valid_until and valid_from > valid_until:
            raise serializers.ValidationError(
                "La fecha de inicio debe ser anterior o igual a la fecha de fin."
            )

        if days_of_week is not None:
            if not isinstance(days_of_week, list) or not days_of_week:
                raise serializers.ValidationError(
                    {"days_of_week": "Debe ser una lista no vacía de días de la semana."}
                )
            invalid_days = [
                day for day in days_of_week if not isinstance(day, int) or day not in range(7)
            ]
            if invalid_days:
                raise serializers.ValidationError(
                    {"days_of_week": "Los días deben estar entre 0 (lunes) y 6 (domingo)."}
                )
        if self.instance:
            for field, value in attrs.items():
                setattr(self.instance, field, value)
            self.instance.clean()
            return attrs
        instance.clean()
        return attrs


class ExternalAccessLogEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExternalAccessLogEntry
        fields = [
            "external_id",
            "tipo",
            "origen",
            "id_tarjeta",
            "id_cliente",
            "fecha",
            "resultado",
            "id_controlador",
            "id_acceso",
            "observacion",
            "tipo_registro",
            "id_cd_motivo",
            "flag_permite_paso",
            "fecha_paso_permitido",
            "id_controlador_paso_permitido",
            "synced_at",
        ]


class BioStarDeviceSerializer(serializers.ModelSerializer):
    device_group = serializers.CharField(source="device_group.name", default="")

    class Meta:
        model = BioStarDevice
        fields = (
            "device_id",
            "name",
            "device_type",
            "ip_addr",
            "status",
            "device_group",
            "last_synced_at",
        )


class BioStarUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = BioStarUser
        fields = (
            "user_id",
            "user_unique_id",
            "name",
            "email",
            "phone",
            "is_active",
            "last_seen_at",
            "last_synced_at",
        )

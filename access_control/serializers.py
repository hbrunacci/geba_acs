from rest_framework import serializers

from access_control.models import BioStarDevice, BioStarUser

from access_control.models.models import ExternalAccessLogEntry, WhitelistEntry

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

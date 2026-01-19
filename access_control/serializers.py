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
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("created_at", "updated_at")

    def validate(self, attrs):
        instance = WhitelistEntry(**attrs)
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

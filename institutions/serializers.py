from rest_framework import serializers

from people.models import GuestType, PersonType

from .models import AccessDevice, AccessDoor, AccessPoint, AccessZone, DoorDevice, DoorZoneControl, Event, Site


class SiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Site
        fields = ["id", "name", "address"]


class AccessPointSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccessPoint
        fields = ["id", "site", "name", "description"]


class AccessDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccessDevice
        fields = [
            "id",
            "access_point",
            "name",
            "device_type",
            "has_credential_reader",
            "has_qr_reader",
            "has_facial_reader",
        ]


class EventSerializer(serializers.ModelSerializer):
    allowed_person_types = serializers.ListField(
        child=serializers.ChoiceField(choices=PersonType.choices), required=False, default=list
    )
    allowed_guest_types = serializers.ListField(
        child=serializers.ChoiceField(choices=GuestType.choices), required=False, default=list
    )
    site_name = serializers.CharField(source="site.name", read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "name",
            "site",
            "site_name",
            "description",
            "start_date",
            "end_date",
            "start_time",
            "end_time",
            "allowed_person_types",
            "allowed_guest_types",
        ]


class AccessDoorSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccessDoor
        fields = ["id", "site", "name", "code", "is_active"]


class DoorDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = DoorDevice
        fields = ["id", "door", "device_type", "vendor", "serial_number", "ip_address", "direction", "is_active"]


class AccessZoneSerializer(serializers.ModelSerializer):
    ring_level = serializers.IntegerField(read_only=True)
    ring_order = serializers.IntegerField(read_only=True)

    class Meta:
        model = AccessZone
        fields = ["id", "site", "name", "ring_code", "ring_level", "ring_order", "parent_zone", "is_active"]


class DoorZoneControlSerializer(serializers.ModelSerializer):
    class Meta:
        model = DoorZoneControl
        fields = ["id", "door", "zone", "control_type"]

from rest_framework import serializers

from people.models import GuestType, PersonType

from .models import AccessDevice, AccessPoint, Event, Site


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

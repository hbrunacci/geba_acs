from rest_framework import serializers

from .models import WhitelistEntry


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

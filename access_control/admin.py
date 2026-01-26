from django.contrib import admin

from access_control.models import WhitelistEntry

from django.contrib import admin

from access_control.models import BioStar2Config, BioStarDevice


@admin.register(BioStar2Config)
class BioStar2ConfigAdmin(admin.ModelAdmin):
    list_display = ("base_url", "username", "verify_tls", "timeout_seconds", "session_obtained_at", "updated_at")
    readonly_fields = ("bs_session_id", "session_obtained_at", "updated_at")


@admin.register(BioStarDevice)
class BioStarDeviceAdmin(admin.ModelAdmin):
    list_display = ("device_id", "name", "device_type", "ip_addr", "status", "last_synced_at")
    search_fields = ("name", "device_type", "ip_addr", "device_id")
    list_filter = ("device_type", "status")


@admin.register(WhitelistEntry)
class WhitelistEntryAdmin(admin.ModelAdmin):
    list_display = (
        "person",
        "access_point",
        "event",
        "is_allowed",
        "valid_from",
        "valid_until",
        "start_time",
        "end_time",
        "recurrence",
    )
    list_filter = ("is_allowed", "access_point__site")
    search_fields = ("person__last_name", "access_point__name", "event__name")

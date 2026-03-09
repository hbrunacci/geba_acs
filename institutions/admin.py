from django.contrib import admin

from .models import AccessDevice, AccessDoor, AccessPoint, AccessZone, DoorDevice, DoorZoneControl, Event, Site


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("name", "address")
    search_fields = ("name", "address")


@admin.register(AccessPoint)
class AccessPointAdmin(admin.ModelAdmin):
    list_display = ("name", "site", "description")
    list_filter = ("site",)
    search_fields = ("name", "site__name")


@admin.register(AccessDevice)
class AccessDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "access_point",
        "device_type",
        "has_credential_reader",
        "has_qr_reader",
        "has_facial_reader",
    )
    list_filter = ("device_type", "access_point__site")
    search_fields = ("name", "access_point__name")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "site",
        "start_date",
        "end_date",
        "start_time",
        "end_time",
    )
    list_filter = ("site", "start_date")
    search_fields = ("name", "site__name")


@admin.register(AccessDoor)
class AccessDoorAdmin(admin.ModelAdmin):
    list_display = ("name", "site", "code", "is_active")
    list_filter = ("site", "is_active")


@admin.register(DoorDevice)
class DoorDeviceAdmin(admin.ModelAdmin):
    list_display = ("door", "device_type", "vendor", "serial_number", "direction", "is_active")
    list_filter = ("device_type", "direction", "is_active")


@admin.register(AccessZone)
class AccessZoneAdmin(admin.ModelAdmin):
    list_display = ("name", "site", "ring_code", "parent_zone", "is_active")
    list_filter = ("site", "is_active")


@admin.register(DoorZoneControl)
class DoorZoneControlAdmin(admin.ModelAdmin):
    list_display = ("door", "zone", "control_type")
    list_filter = ("control_type",)

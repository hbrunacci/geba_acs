from django.contrib import admin

from .models import (
    AccessDevice,
    AccessDoor,
    AccessPoint,
    AccessZone,
    DoorController,
    DoorDevice,
    DoorTurnstileGroup,
    DoorZoneControl,
    Event,
    Site,
)


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


class DoorControllerInline(admin.TabularInline):
    model = DoorController
    extra = 0


class DoorTurnstileGroupInline(admin.TabularInline):
    model = DoorTurnstileGroup
    extra = 0


@admin.register(AccessDoor)
class AccessDoorAdmin(admin.ModelAdmin):
    list_display = ("name", "site", "code", "xsys_id_acceso", "is_active")
    list_filter = ("site", "is_active")
    search_fields = ("name", "code")
    inlines = [DoorControllerInline, DoorTurnstileGroupInline]


@admin.register(DoorController)
class DoorControllerAdmin(admin.ModelAdmin):
    list_display = ("door", "id_controlador", "orden")
    list_filter = ("door",)
    search_fields = ("id_controlador",)


@admin.register(DoorTurnstileGroup)
class DoorTurnstileGroupAdmin(admin.ModelAdmin):
    list_display = ("nombre", "door", "id_controladores", "orden")
    list_filter = ("door",)
    search_fields = ("nombre",)


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

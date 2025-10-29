from django.contrib import admin

from .models import WhitelistEntry


@admin.register(WhitelistEntry)
class WhitelistEntryAdmin(admin.ModelAdmin):
    list_display = (
        "person",
        "access_point",
        "event",
        "is_allowed",
        "valid_from",
        "valid_until",
    )
    list_filter = ("is_allowed", "access_point__site")
    search_fields = ("person__last_name", "access_point__name", "event__name")

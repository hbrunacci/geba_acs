from django.contrib import admin

from .models import GuestInvitation, Person


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = (
        "last_name",
        "first_name",
        "dni",
        "person_type",
        "guest_type",
        "is_active",
    )
    search_fields = ("first_name", "last_name", "dni", "email")
    list_filter = ("person_type", "guest_type", "is_active")


@admin.register(GuestInvitation)
class GuestInvitationAdmin(admin.ModelAdmin):
    list_display = ("person", "event", "guest_type", "created_at")
    search_fields = ("person__last_name", "person__first_name", "event__name")
    list_filter = ("guest_type", "event__site")

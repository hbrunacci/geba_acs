from django.contrib import admin

from .models import (
    Cliente,
    DocumentType,
    GuestInvitation,
    Person,
    PersonCategory,
    PersonCategoryDocumentRequirement,
    PersonDocument,
)


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


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("id_cliente", "razon_social", "apellido", "nombre", "email", "activo")
    search_fields = ("id_cliente", "razon_social", "apellido", "nombre", "cuit", "email")
    list_filter = ("activo", "id_estado_cliente", "mercado")


@admin.register(PersonCategory)
class PersonCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_active",)


@admin.register(DocumentType)
class DocumentTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "requires_expiration", "is_active")
    search_fields = ("code", "name")
    list_filter = ("requires_expiration", "is_active")


@admin.register(PersonCategoryDocumentRequirement)
class PersonCategoryDocumentRequirementAdmin(admin.ModelAdmin):
    list_display = ("person_category", "document_type", "is_mandatory", "requires_expiration")
    list_filter = ("is_mandatory", "requires_expiration")


@admin.register(PersonDocument)
class PersonDocumentAdmin(admin.ModelAdmin):
    list_display = ("person", "document_type", "document_number", "issued_at", "expires_at")
    search_fields = ("person__last_name", "person__first_name", "document_number")

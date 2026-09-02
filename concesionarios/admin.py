from django.contrib import admin

from concesionarios.models import (
    Concesionario,
    Documento,
    Empresa,
    HorarioAcceso,
    HorarioFranja,
    TipoDocumento,
)


class FranjaInline(admin.TabularInline):
    model = HorarioFranja
    extra = 1


@admin.register(HorarioAcceso)
class HorarioAccesoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "resumen", "activo")
    search_fields = ("nombre", "descripcion")
    inlines = [FranjaInline]


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "cuit", "rubro", "horario", "activa")
    search_fields = ("nombre", "cuit")
    list_filter = ("activa",)


@admin.register(Concesionario)
class ConcesionarioAdmin(admin.ModelAdmin):
    list_display = ("id_cliente", "empresa", "cargo", "horario", "activo")
    search_fields = ("id_cliente", "cargo")
    list_filter = ("activo", "empresa")


@admin.register(TipoDocumento)
class TipoDocumentoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "codigo", "requiere_vencimiento", "dias_aviso",
                    "bloquea_acceso", "activo")
    search_fields = ("nombre", "codigo")
    list_filter = ("bloquea_acceso", "activo")


@admin.register(Documento)
class DocumentoAdmin(admin.ModelAdmin):
    list_display = ("id_cliente", "tipo", "fecha_vencimiento", "subido_por")
    search_fields = ("id_cliente", "numero")
    list_filter = ("tipo",)
    date_hierarchy = "fecha_vencimiento"

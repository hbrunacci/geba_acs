from __future__ import annotations

from django.contrib import admin

from xsys.models import SyncState, XsysNovedad, XsysSocio, XsysSocioFoto, XsysWhitelist


@admin.register(XsysSocio)
class XsysSocioAdmin(admin.ModelAdmin):
    list_display = ("id_cliente", "apellido", "nombre", "doc_nro", "credencial_nro", "activo", "ult_cuota_paga", "synced_at")
    search_fields = ("id_cliente", "doc_nro", "apellido", "nombre", "credencial_nro")
    list_filter = ("activo", "tipo_persona")
    ordering = ("apellido", "nombre")


@admin.register(XsysSocioFoto)
class XsysSocioFotoAdmin(admin.ModelAdmin):
    list_display = ("id_cliente", "nro", "fecha", "sha256", "synced_at")
    search_fields = ("id_cliente",)
    readonly_fields = ("sha256",)
    exclude = ("imagen",)


@admin.register(XsysWhitelist)
class XsysWhitelistAdmin(admin.ModelAdmin):
    list_display = ("id_cliente", "habilitado", "motivo", "detalle", "id_acceso", "fecha_calculo")
    search_fields = ("id_cliente", "motivo")
    list_filter = ("habilitado", "id_acceso")


@admin.register(XsysNovedad)
class XsysNovedadAdmin(admin.ModelAdmin):
    list_display = ("id_novedad", "id_cliente", "tipo", "estado_origen", "fecha", "processed_at")
    search_fields = ("id_novedad", "id_cliente")
    list_filter = ("tipo", "estado_origen")


@admin.register(SyncState)
class SyncStateAdmin(admin.ModelAdmin):
    list_display = ("stream", "last_id", "last_datetime", "last_run_finished_at", "last_run_ok", "rows_last_run")
    readonly_fields = ("stream",)

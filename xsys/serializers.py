from __future__ import annotations

from rest_framework import serializers

from xsys.models import XsysSocio, XsysSocioFoto, XsysWhitelist


class XsysSocioSerializer(serializers.ModelSerializer):
    nombre_completo = serializers.SerializerMethodField()
    foto_url = serializers.SerializerMethodField()
    foto_thumb_url = serializers.SerializerMethodField()

    class Meta:
        model = XsysSocio
        fields = (
            "id_cliente",
            "nombre_completo",
            "apellido",
            "nombre",
            "razon_social",
            "doc_nro",
            "sexo",
            "fecha_nac",
            "email",
            "activo",
            "tipo_persona",
            "credencial_nro",
            "ult_cuota_paga",
            "id_estado_cliente",
            "id_cliente_externo",
            "fecha_alta",
            "fecha_baja",
            "foto_url",
            "foto_thumb_url",
            "synced_at",
        )

    def get_nombre_completo(self, obj: XsysSocio) -> str:
        nombre = f"{obj.apellido}, {obj.nombre}".strip(", ")
        return nombre or obj.razon_social or f"Cliente {obj.id_cliente}"

    def _tiene_foto(self, obj: XsysSocio) -> bool:
        return XsysSocioFoto.objects.filter(id_cliente=obj.id_cliente).exists()

    def get_foto_url(self, obj: XsysSocio) -> str | None:
        return f"/api/xsys/socios/{obj.id_cliente}/foto/" if self._tiene_foto(obj) else None

    def get_foto_thumb_url(self, obj: XsysSocio) -> str | None:
        return f"/api/xsys/socios/{obj.id_cliente}/foto/?thumb=1" if self._tiene_foto(obj) else None


class XsysWhitelistSerializer(serializers.ModelSerializer):
    class Meta:
        model = XsysWhitelist
        fields = (
            "id_cliente",
            "habilitado",
            "motivo_code",
            "motivo",
            "detalle",
            "id_acceso",
            "fecha_calculo",
            "synced_at",
        )


class XsysSocioLookupSerializer(serializers.Serializer):
    """Respuesta combinada del lookup: socio + lista blanca + foto."""

    socio = XsysSocioSerializer()
    whitelist = XsysWhitelistSerializer(allow_null=True)
    foto_disponible = serializers.BooleanField()

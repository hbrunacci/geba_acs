"""Serializers de concesionarios: empresas, horarios y documentación."""

from __future__ import annotations

from rest_framework import serializers

from concesionarios.models import (
    Concesionario,
    Documento,
    Empresa,
    HorarioAcceso,
    HorarioFranja,
    TipoDocumento,
)

# Lo que se acepta adjuntar. La lista es corta a propósito: son papeles
# (escaneos y PDF), no un repositorio de archivos.
EXTENSIONES = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}
TAMANO_MAXIMO = 15 * 1024 * 1024  # 15 MB


class FranjaSerializer(serializers.ModelSerializer):
    class Meta:
        model = HorarioFranja
        fields = ("id", "dia_semana", "hora_desde", "hora_hasta")

    def validate(self, attrs):
        if attrs.get("hora_desde") == attrs.get("hora_hasta"):
            raise serializers.ValidationError(
                {"hora_hasta": "La hora de fin no puede ser igual a la de inicio."})
        return attrs


class HorarioSerializer(serializers.ModelSerializer):
    franjas = FranjaSerializer(many=True, required=False)
    # Atajo para el caso común: "Lun a Vie de 9 a 18" en un solo POST.
    dias = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6),
        required=False, write_only=True)
    hora_desde = serializers.TimeField(required=False, write_only=True)
    hora_hasta = serializers.TimeField(required=False, write_only=True)
    resumen = serializers.CharField(read_only=True)
    en_uso = serializers.SerializerMethodField()

    class Meta:
        model = HorarioAcceso
        fields = ("id", "nombre", "descripcion", "activo", "franjas", "resumen",
                  "en_uso", "dias", "hora_desde", "hora_hasta")

    def get_en_uso(self, obj) -> int:
        return obj.concesionarios.count() + obj.empresas.count()

    def validate(self, attrs):
        dias = attrs.get("dias")
        desde, hasta = attrs.get("hora_desde"), attrs.get("hora_hasta")
        if dias and not (desde and hasta):
            raise serializers.ValidationError(
                {"hora_desde": "Con una lista de días hay que mandar hora_desde y hora_hasta."})
        if dias and desde == hasta:
            raise serializers.ValidationError(
                {"hora_hasta": "La hora de fin no puede ser igual a la de inicio."})
        return attrs

    def _armar_franjas(self, horario, validated):
        """Reemplaza las franjas si vinieron; si no vino nada, no las toca."""
        dias = validated.pop("dias", None)
        desde = validated.pop("hora_desde", None)
        hasta = validated.pop("hora_hasta", None)
        franjas = validated.pop("franjas", None)
        if dias:
            horario.franjas.all().delete()
            HorarioFranja.objects.bulk_create([
                HorarioFranja(horario=horario, dia_semana=d, hora_desde=desde, hora_hasta=hasta)
                for d in sorted(set(dias))
            ])
        elif franjas is not None:
            horario.franjas.all().delete()
            HorarioFranja.objects.bulk_create([
                HorarioFranja(horario=horario, **f) for f in franjas
            ])

    def create(self, validated):
        datos = dict(validated)
        horario = HorarioAcceso.objects.create(**{
            k: v for k, v in datos.items()
            if k in ("nombre", "descripcion", "activo")})
        self._armar_franjas(horario, datos)
        return horario

    def update(self, instance, validated):
        datos = dict(validated)
        for campo in ("nombre", "descripcion", "activo"):
            if campo in datos:
                setattr(instance, campo, datos[campo])
        instance.save()
        self._armar_franjas(instance, datos)
        return instance


class EmpresaSerializer(serializers.ModelSerializer):
    horario_nombre = serializers.CharField(source="horario.nombre", read_only=True, default="")
    cantidad = serializers.SerializerMethodField()

    class Meta:
        model = Empresa
        fields = ("id", "nombre", "cuit", "rubro", "contacto_nombre", "contacto_email",
                  "contacto_telefono", "horario", "horario_nombre", "activa",
                  "observaciones", "cantidad")

    def get_cantidad(self, obj) -> int:
        return obj.concesionarios.count()


class TipoDocumentoSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoDocumento
        fields = ("id", "codigo", "nombre", "descripcion", "requiere_vencimiento",
                  "dias_aviso", "bloquea_acceso", "activo")


class ConcesionarioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Concesionario
        fields = ("id", "id_cliente", "empresa", "cargo", "horario", "activo",
                  "fecha_alta", "fecha_baja", "observaciones")

    def validate_id_cliente(self, valor):
        if valor <= 0:
            raise serializers.ValidationError("El legajo tiene que ser un número positivo.")
        return valor

    def validate(self, attrs):
        alta = attrs.get("fecha_alta") or getattr(self.instance, "fecha_alta", None)
        baja = attrs.get("fecha_baja") or getattr(self.instance, "fecha_baja", None)
        if alta and baja and baja < alta:
            raise serializers.ValidationError(
                {"fecha_baja": "La baja no puede ser anterior al alta."})
        return attrs


class DocumentoSerializer(serializers.ModelSerializer):
    tipo_nombre = serializers.CharField(source="tipo.nombre", read_only=True)
    bloquea_acceso = serializers.BooleanField(source="tipo.bloquea_acceso", read_only=True)
    estado = serializers.SerializerMethodField()
    dias = serializers.SerializerMethodField()
    archivo_url = serializers.SerializerMethodField()

    class Meta:
        model = Documento
        fields = ("id", "id_cliente", "tipo", "tipo_nombre", "numero", "archivo",
                  "archivo_nombre", "archivo_url", "fecha_emision", "fecha_vencimiento",
                  "observaciones", "subido_por", "created_at", "estado", "dias",
                  "bloquea_acceso")
        read_only_fields = ("archivo_nombre", "subido_por", "created_at")
        extra_kwargs = {"archivo": {"write_only": True, "required": False}}

    def get_estado(self, obj) -> str:
        return obj.estado()

    def get_dias(self, obj):
        return obj.dias_para_vencer()

    def get_archivo_url(self, obj):
        if not obj.archivo:
            return None
        return f"/api/concesionarios/documentos/{obj.id}/archivo/"

    def validate_archivo(self, archivo):
        if archivo is None:
            return archivo
        nombre = (getattr(archivo, "name", "") or "").lower()
        ext = "." + nombre.rsplit(".", 1)[1] if "." in nombre else ""
        if ext not in EXTENSIONES:
            raise serializers.ValidationError(
                "Formato no admitido. Se aceptan: " + ", ".join(sorted(EXTENSIONES)))
        if getattr(archivo, "size", 0) > TAMANO_MAXIMO:
            raise serializers.ValidationError("El archivo supera los 15 MB.")
        return archivo

    def validate(self, attrs):
        tipo = attrs.get("tipo") or getattr(self.instance, "tipo", None)
        vence = attrs.get("fecha_vencimiento", getattr(self.instance, "fecha_vencimiento", None))
        emision = attrs.get("fecha_emision", getattr(self.instance, "fecha_emision", None))
        if tipo and tipo.requiere_vencimiento and not vence:
            raise serializers.ValidationError(
                {"fecha_vencimiento": f"{tipo.nombre} requiere fecha de vencimiento."})
        if emision and vence and vence < emision:
            raise serializers.ValidationError(
                {"fecha_vencimiento": "El vencimiento no puede ser anterior a la emisión."})
        return attrs

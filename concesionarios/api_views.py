"""API de la pantalla de concesionarios.

Todo pide el rol de concesionarios (admin o el grupo). La descarga de un
adjunto también: son escaneos de DNI, ART y aptos médicos, así que el archivo no
se sirve por una URL estática adivinable sino por una vista que chequea permisos.
"""

from __future__ import annotations

from django.http import FileResponse, Http404
from rest_framework import status, views
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from common.roles import PuedeConcesionarios
from concesionarios import services
from concesionarios.models import (
    Concesionario,
    Documento,
    Empresa,
    HorarioAcceso,
    TipoDocumento,
)
from concesionarios.serializers import (
    ConcesionarioSerializer,
    DocumentoSerializer,
    EmpresaSerializer,
    HorarioSerializer,
    TipoDocumentoSerializer,
)


def _bool(valor, default=False) -> bool:
    if valor is None:
        return default
    return str(valor).lower() in ("1", "true", "t", "si", "sí", "yes", "on")


class _Base(views.APIView):
    permission_classes = [PuedeConcesionarios]


# ------------------------------------------------------------------- listado --
class ListadoAPI(_Base):
    """El listado con los filtros de la pantalla: empresa, DNI y apellido."""

    def get(self, request):
        empresa = request.query_params.get("empresa")
        filas = services.listar(
            empresa_id=int(empresa) if (empresa or "").isdigit() else None,
            doc=request.query_params.get("doc", ""),
            apellido=request.query_params.get("apellido", ""),
            solo_activos=_bool(request.query_params.get("solo_activos")),
            con_problemas=_bool(request.query_params.get("con_problemas")),
        )
        return Response({
            "count": len(filas),
            "results": filas,
            "resumen": {
                "con_vencidos": sum(1 for f in filas if f["documentos"]["vencidos"]),
                "por_vencer": sum(1 for f in filas
                                  if not f["documentos"]["vencidos"] and f["documentos"]["por_vencer"]),
                "bloqueados": sum(1 for f in filas if f["documentos"]["bloqueado"]),
                "sin_documentos": sum(1 for f in filas if not f["documentos"]["total"]),
            },
        })


class IngresosAPI(_Base):
    """Pasadas de los concesionarios en un rango, con foto, empresa y resultado."""

    def get(self, request):
        from datetime import date, timedelta

        from django.utils import timezone

        def fecha(nombre, default):
            valor = (request.query_params.get(nombre) or "").strip()
            if not valor:
                return default
            try:
                return date.fromisoformat(valor)
            except ValueError:
                return default

        hoy = timezone.localdate()
        desde = fecha("desde", hoy - timedelta(days=7))
        hasta = fecha("hasta", hoy)
        if hasta < desde:
            desde, hasta = hasta, desde
        empresa = request.query_params.get("empresa")
        datos = services.ingresos(
            desde=desde, hasta=hasta,
            empresa_id=int(empresa) if (empresa or "").isdigit() else None,
            solo_rechazos=_bool(request.query_params.get("solo_rechazos")),
        )
        if _bool(request.query_params.get("solo_rechazos")):
            datos["results"] = [f for f in datos["results"] if not f["permitido"]]
        if _bool(request.query_params.get("solo_alertas")):
            datos["results"] = [f for f in datos["results"] if f["alerta"]]
        datos["desde"], datos["hasta"] = desde, hasta
        return Response(datos)


# ---------------------------------------------------------------------- fotos
class FotoAPI(_Base):
    """La cara de la persona: la cargada a mano y, si no hay, la de xSys."""

    parser_classes = [MultiPartParser, FormParser]

    def get(self, request, id_cliente):
        from django.http import HttpResponse

        from concesionarios import fotos
        datos, tipo = fotos.bytes_para_mostrar(
            id_cliente, miniatura=not _bool(request.query_params.get("full")))
        if not datos:
            raise Http404
        respuesta = HttpResponse(datos, content_type=tipo or "image/jpeg")
        respuesta["Cache-Control"] = "private, max-age=60"
        return respuesta

    def post(self, request, id_cliente):
        from concesionarios import fotos
        archivo = request.data.get("archivo") or request.data.get("imagen")
        if archivo is None:
            return Response({"archivo": ["Falta la imagen."]},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            foto = fotos.guardar(
                id_cliente, archivo.read(),
                content_type=getattr(archivo, "content_type", ""),
                usuario=request.user.get_username())
        except fotos.FotoInvalida as exc:
            return Response({"archivo": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"id_cliente": foto.id_cliente, "subido_por": foto.subido_por,
                         "enrolada": False}, status=status.HTTP_201_CREATED)

    def delete(self, request, id_cliente):
        from concesionarios.models import FotoPersona
        FotoPersona.objects.filter(id_cliente=id_cliente).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EnrolarFotoAPI(_Base):
    """Manda la foto cargada al rostro de BioStar."""

    def post(self, request, id_cliente):
        from xsys.models import XsysSocio

        from concesionarios import fotos
        socio = XsysSocio.objects.filter(id_cliente=id_cliente).first()
        nombre = ""
        if socio:
            nombre = f"{socio.apellido} {socio.nombre}".strip() or socio.razon_social
        resultado = fotos.enrolar(id_cliente, nombre=nombre)
        codigo = status.HTTP_200_OK if resultado["ok"] else status.HTTP_502_BAD_GATEWAY
        return Response(resultado, status=codigo)


class CandidatosAPI(_Base):
    """Socios con categoría CONCESIONARIO en xSys que todavía no están cargados."""

    def get(self, request):
        return Response({"results": services.candidatos_sin_registrar(
            busqueda=request.query_params.get("q", ""))})


# ------------------------------------------------------- ABM de concesionarios
class ConcesionariosAPI(_Base):
    def post(self, request):
        ser = ConcesionarioSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=status.HTTP_201_CREATED)


class ConcesionarioDetalleAPI(_Base):
    def _obj(self, pk) -> Concesionario:
        try:
            return Concesionario.objects.select_related("empresa", "horario").get(pk=pk)
        except Concesionario.DoesNotExist:
            raise Http404

    def get(self, request, pk):
        obj = self._obj(pk)
        from xsys.models import XsysSocio
        socio = XsysSocio.objects.filter(id_cliente=obj.id_cliente).first()
        docs = Documento.objects.filter(id_cliente=obj.id_cliente).select_related("tipo")
        horario = obj.horario_vigente
        return Response({
            "id": obj.id,
            "persona": services.datos_persona(socio, obj.id_cliente),
            "empresa": EmpresaSerializer(obj.empresa).data,
            "cargo": obj.cargo,
            "activo": obj.activo,
            "fecha_alta": obj.fecha_alta,
            "fecha_baja": obj.fecha_baja,
            "observaciones": obj.observaciones,
            "horario_id": obj.horario_id,
            "horario": ({"id": horario.id, "nombre": horario.nombre,
                         "resumen": horario.resumen, "propio": obj.horario_id is not None}
                        if horario else None),
            "permite_ahora": obj.permite_horario(),
            "documentos": DocumentoSerializer(docs, many=True).data,
        })

    def put(self, request, pk):
        obj = self._obj(pk)
        ser = ConcesionarioSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)

    def delete(self, request, pk):
        self._obj(pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------- ABM genérico
class _Coleccion(_Base):
    modelo = None
    serializer = None
    orden = ()

    def get(self, request):
        qs = self.modelo.objects.all()
        if self.orden:
            qs = qs.order_by(*self.orden)
        return Response({"results": self.serializer(qs, many=True).data})

    def post(self, request):
        ser = self.serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=status.HTTP_201_CREATED)


class _Item(_Base):
    modelo = None
    serializer = None

    def _obj(self, pk):
        try:
            return self.modelo.objects.get(pk=pk)
        except self.modelo.DoesNotExist:
            raise Http404

    def put(self, request, pk):
        ser = self.serializer(self._obj(pk), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)

    def delete(self, request, pk):
        from django.db.models import ProtectedError
        try:
            self._obj(pk).delete()
        except ProtectedError:
            return Response(
                {"detail": "No se puede borrar: tiene registros asociados. "
                           "Desactivalo en vez de borrarlo."},
                status=status.HTTP_409_CONFLICT)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmpresasAPI(_Coleccion):
    modelo, serializer, orden = Empresa, EmpresaSerializer, ("nombre",)


class EmpresaDetalleAPI(_Item):
    modelo, serializer = Empresa, EmpresaSerializer


class HorariosAPI(_Coleccion):
    modelo, serializer, orden = HorarioAcceso, HorarioSerializer, ("nombre",)

    def get(self, request):
        qs = HorarioAcceso.objects.prefetch_related("franjas").order_by("nombre")
        return Response({"results": HorarioSerializer(qs, many=True).data})


class HorarioDetalleAPI(_Item):
    modelo, serializer = HorarioAcceso, HorarioSerializer


class TiposDocumentoAPI(_Coleccion):
    modelo, serializer, orden = TipoDocumento, TipoDocumentoSerializer, ("nombre",)


class TipoDocumentoDetalleAPI(_Item):
    modelo, serializer = TipoDocumento, TipoDocumentoSerializer


# ------------------------------------------------------------------ documentos
class DocumentosAPI(_Base):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        id_cliente = request.query_params.get("id_cliente")
        qs = Documento.objects.select_related("tipo")
        if (id_cliente or "").isdigit():
            qs = qs.filter(id_cliente=int(id_cliente))
        return Response({"results": DocumentoSerializer(qs, many=True).data})

    def post(self, request):
        ser = DocumentoSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        archivo = ser.validated_data.get("archivo")
        doc = ser.save(
            archivo_nombre=(getattr(archivo, "name", "") or "")[:255],
            subido_por=(request.user.get_username() if request.user else "")[:150],
        )
        return Response(DocumentoSerializer(doc).data, status=status.HTTP_201_CREATED)


class DocumentoDetalleAPI(_Base):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _obj(self, pk) -> Documento:
        try:
            return Documento.objects.select_related("tipo").get(pk=pk)
        except Documento.DoesNotExist:
            raise Http404

    def put(self, request, pk):
        ser = DocumentoSerializer(self._obj(pk), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        doc = ser.save()
        return Response(DocumentoSerializer(doc).data)

    def delete(self, request, pk):
        doc = self._obj(pk)
        archivo = doc.archivo
        doc.delete()
        if archivo:
            archivo.delete(save=False)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DocumentoArchivoAPI(_Base):
    """Descarga del adjunto, detrás del permiso. No hay URL pública del archivo."""

    def get(self, request, pk):
        doc = Documento.objects.filter(pk=pk).select_related("tipo").first()
        if doc is None or not doc.archivo:
            raise Http404
        try:
            handle = doc.archivo.open("rb")
        except FileNotFoundError:  # el registro quedó pero el archivo no está
            raise Http404
        nombre = doc.archivo_nombre or doc.archivo.name.rsplit("/", 1)[-1]
        return FileResponse(handle, as_attachment=False, filename=nombre)

from __future__ import annotations

from django.http import HttpResponse
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from xsys.models import XsysSocio, XsysSocioFoto, XsysWhitelist
from xsys.serializers import (
    XsysSocioLookupSerializer,
    XsysSocioSerializer,
    XsysWhitelistSerializer,
)
from xsys.services.access import resolver_acceso, resolver_socio


def _resolve_socio(*, id_cliente=None, doc=None, credencial=None) -> XsysSocio | None:
    return resolver_socio(id_cliente=id_cliente, doc=doc, credencial=credencial)


def _flag(value, default=True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off")


class AccesoResolverAPI(APIView):
    """GET /api/xsys/acceso/?id=&doc=&credencial=[&online=1]

    Resuelve localmente si el socio puede ingresar (sin distinción de puerta). Si
    es negativo y ``online`` está activo (default), re-verifica en xSys si el
    parámetro que lo invalidó cambió.
    """

    def get(self, request):
        id_cliente = request.query_params.get("id")
        doc = request.query_params.get("doc")
        credencial = request.query_params.get("credencial")
        if not any([id_cliente, doc, credencial]):
            return Response(
                {"detail": "Indique al menos uno de: id, doc, credencial."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        resultado = resolver_acceso(
            id_cliente=id_cliente,
            doc=doc,
            credencial=credencial,
            verificar_online=_flag(request.query_params.get("online")),
        )
        if not resultado.get("found"):
            return Response(resultado, status=status.HTTP_404_NOT_FOUND)
        return Response(resultado)


class SocioLookupAPI(APIView):
    """GET /api/xsys/socios/lookup/?id=&doc=&credencial= → socio + whitelist + foto."""

    def get(self, request):
        id_cliente = request.query_params.get("id")
        doc = request.query_params.get("doc")
        credencial = request.query_params.get("credencial")
        if not any([id_cliente, doc, credencial]):
            return Response(
                {"detail": "Indique al menos uno de: id, doc, credencial."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        socio = _resolve_socio(id_cliente=id_cliente, doc=doc, credencial=credencial)
        if socio is None:
            return Response({"detail": "Socio no encontrado."}, status=status.HTTP_404_NOT_FOUND)

        whitelist = XsysWhitelist.objects.filter(id_cliente=socio.id_cliente).first()
        foto_disponible = XsysSocioFoto.objects.filter(id_cliente=socio.id_cliente).exists()

        payload = {"socio": socio, "whitelist": whitelist, "foto_disponible": foto_disponible}
        return Response(XsysSocioLookupSerializer(payload, context={"request": request}).data)


class SocioSearchAPI(ListAPIView):
    """GET /api/xsys/socios/?q= → búsqueda por nombre/apellido/doc/credencial."""

    serializer_class = XsysSocioSerializer

    def get_queryset(self):
        qs = XsysSocio.objects.all()
        q = (self.request.query_params.get("q") or "").strip()
        if q:
            from django.db.models import Q

            filtro = (
                Q(apellido__icontains=q)
                | Q(nombre__icontains=q)
                | Q(razon_social__icontains=q)
                | Q(credencial_nro__icontains=q)
            )
            if q.isdigit():
                filtro |= Q(doc_nro=int(q)) | Q(id_cliente=int(q))
            qs = qs.filter(filtro)
        return qs.order_by("apellido", "nombre")


class SocioWhitelistAPI(APIView):
    """GET /api/xsys/socios/<id_cliente>/whitelist/ → estado de lista blanca."""

    def get(self, request, id_cliente: int):
        wl = XsysWhitelist.objects.filter(id_cliente=id_cliente).first()
        if wl is None:
            return Response(
                {"detail": "Sin cálculo de lista blanca para este socio."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(XsysWhitelistSerializer(wl).data)


def _content_type(data: bytes) -> str:
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"BM":
        return "image/bmp"
    return "application/octet-stream"


class SocioFotoAPI(APIView):
    """GET /api/xsys/socios/<id_cliente>/foto/[?nro=][?thumb=1] → bytes de la imagen."""

    def get(self, request, id_cliente: int):
        nro = request.query_params.get("nro")
        thumb = request.query_params.get("thumb") in ("1", "true", "yes")
        qs = XsysSocioFoto.objects.filter(id_cliente=id_cliente)
        foto = qs.filter(nro=nro).first() if nro else qs.order_by("nro").first()
        if foto is None or not foto.imagen:
            return HttpResponse(status=status.HTTP_404_NOT_FOUND)

        data = None
        if thumb:
            data = bytes(foto.thumbnail) if foto.thumbnail else None
            if data is None:
                from xsys.services.images import make_thumbnail

                data = make_thumbnail(bytes(foto.imagen))
                if data:
                    # Persistir la miniatura para próximas consultas.
                    foto.thumbnail = data
                    foto.save(update_fields=["thumbnail"])
        if data is None:
            data = bytes(foto.imagen)  # fallback: imagen completa

        response = HttpResponse(data, content_type=_content_type(data))
        response["Cache-Control"] = "private, max-age=300"
        response["Content-Length"] = str(len(data))
        return response

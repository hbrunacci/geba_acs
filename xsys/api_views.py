from __future__ import annotations

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from access_control.models.models import ExternalAccessLogEntry

from xsys.models import (
    PantallaPuerta,
    XsysAcceso,
    XsysMotivo,
    XsysSocio,
    XsysSocioFoto,
    XsysWhitelist,
)
from xsys.serializers import (
    XsysSocioLookupSerializer,
    XsysSocioSerializer,
    XsysWhitelistSerializer,
)
from xsys.services.access import resolver_acceso, resolver_socio


def _client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "0.0.0.0"


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


# ----------------------------------------------------------------------------
# Monitor de puerta (kiosco): endpoints abiertos (AllowAny), pantalla
# identificada por TOKEN (header X-Pantalla-Token). La IP se guarda solo como
# dato informativo del lugar.
# ----------------------------------------------------------------------------

def _pantalla_token(request) -> str:
    return (request.META.get("HTTP_X_PANTALLA_TOKEN", "") or "").strip()[:64]


def _registrar_pantalla(request) -> PantallaPuerta | None:
    """Upsert de la pantalla por token; None si no vino token."""
    token = _pantalla_token(request)
    if not token:
        return None
    ua = (request.META.get("HTTP_USER_AGENT", "") or "")[:255]
    pantalla, _ = PantallaPuerta.objects.get_or_create(token=token)
    PantallaPuerta.objects.filter(pk=pantalla.pk).update(
        last_seen=timezone.now(), user_agent=ua, ip=_client_ip(request)
    )
    pantalla.refresh_from_db()
    return pantalla


def _evento_payload(ev: ExternalAccessLogEntry) -> dict:
    socio = XsysSocio.objects.filter(pk=ev.id_cliente).first() if ev.id_cliente else None
    tiene_foto = bool(socio) and XsysSocioFoto.objects.filter(id_cliente=socio.id_cliente).exists()
    mensaje = ""
    if ev.id_cd_motivo:
        motivo = XsysMotivo.objects.filter(pk=ev.id_cd_motivo).first()
        if motivo:
            mensaje = motivo.mensaje_pantalla
    if not mensaje:
        mensaje = (ev.observacion or "").strip()
    return {
        "id_es": ev.external_id,
        "fecha": ev.fecha.isoformat() if ev.fecha else None,
        "resultado": ev.resultado,
        "permitido": ev.resultado == "S",
        "mensaje": mensaje,
        "id_cliente": ev.id_cliente,
        "nombre": (
            (f"{socio.apellido}, {socio.nombre}".strip(", ") or socio.razon_social)
            if socio else ""
        ),
        "foto_url": f"/api/xsys/socios/{socio.id_cliente}/foto/" if tiene_foto else None,
    }


class PuertasListAPI(APIView):
    """GET /api/xsys/puertas/ → lista de puertas activas (para la hamburguesa)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        puertas = [
            {"id_acceso": a.id_acceso, "descripcion": a.descripcion or f"Acceso {a.id_acceso}"}
            for a in XsysAcceso.objects.filter(activo=1).order_by("descripcion")
        ]
        return Response({"puertas": puertas})


class PuertaSeleccionarAPI(APIView):
    """POST /api/xsys/puerta/seleccionar/ {id_acceso} → ata el token a esa puerta."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        token = _pantalla_token(request)
        if not token:
            return Response({"detail": "Falta el token de pantalla."}, status=status.HTTP_400_BAD_REQUEST)
        id_acceso = request.data.get("id_acceso")
        if id_acceso in (None, ""):
            return Response({"detail": "id_acceso requerido."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            id_acceso = int(id_acceso)
        except (TypeError, ValueError):
            return Response({"detail": "id_acceso inválido."}, status=status.HTTP_400_BAD_REQUEST)
        if not XsysAcceso.objects.filter(pk=id_acceso).exists():
            return Response({"detail": "La puerta no existe en el espejo local."}, status=status.HTTP_404_NOT_FOUND)
        nombre = (request.data.get("nombre") or "").strip()[:60]
        defaults = {"id_acceso": id_acceso, "last_seen": timezone.now(), "ip": _client_ip(request)}
        if nombre:
            defaults["nombre"] = nombre
        PantallaPuerta.objects.update_or_create(token=token, defaults=defaults)
        return Response({"ok": True, "token": token, "id_acceso": id_acceso})


class PuertaUltimoAPI(APIView):
    """GET /api/xsys/puerta/ultimo/ → último ingreso de la puerta de este token.

    Se identifica por el header ``X-Pantalla-Token``. Todo se resuelve del espejo
    local. Si el token no tiene puerta configurada, devuelve la lista de puertas
    para elegir en la hamburguesa.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        pantalla = _registrar_pantalla(request)
        if pantalla is None:
            return Response({"detail": "Falta el token de pantalla."}, status=status.HTTP_400_BAD_REQUEST)
        if not pantalla.id_acceso:
            return Response({
                "configurada": False,
                "ip": pantalla.ip,
                "puertas": [
                    {"id_acceso": a.id_acceso, "descripcion": a.descripcion or f"Acceso {a.id_acceso}"}
                    for a in XsysAcceso.objects.filter(activo=1).order_by("descripcion")
                ],
            })

        acceso = XsysAcceso.objects.filter(pk=pantalla.id_acceso).first()
        ev = (
            ExternalAccessLogEntry.objects
            .filter(id_acceso=pantalla.id_acceso, tipo="E")
            .order_by("-external_id")
            .first()
        )
        return Response({
            "configurada": True,
            "ip": pantalla.ip,
            "puerta": {
                "id_acceso": pantalla.id_acceso,
                "descripcion": (acceso.descripcion if acceso else f"Acceso {pantalla.id_acceso}"),
            },
            "evento": _evento_payload(ev) if ev else None,
        })

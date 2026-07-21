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
    PuertaMolinete,
    XsysAcceso,
    XsysContrato,
    XsysControlador,
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
from xsys.services.cuota import cuota_al_dia


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

    # Pública: la pantalla de puerta (sin login) carga las fotos por esta API.
    permission_classes = [AllowAny]
    authentication_classes = []

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


# Cuántos ingresos de historial se devuelven por columna (para paginar de a 5).
HISTORIAL_LEN = 50


def _tipo_lectura(id_controlador, controladores: dict) -> str:
    """'facial' si el controlador es un facial (Tipo_Cont F/W), si no 'credencial'."""
    ctrl = controladores.get(id_controlador)
    if ctrl and (ctrl.tipo_cont or "").upper() in ("F", "W"):
        return "facial"
    return "credencial"


def _evento_payload(ev: ExternalAccessLogEntry, socios: dict, fotos: set, motivos: dict, controladores: dict | None = None) -> dict:
    socio = socios.get(ev.id_cliente)
    tiene_foto = ev.id_cliente in fotos
    # Mensaje original de xSys (motivo de pantalla u observación).
    mensaje_original = ""
    if ev.id_cd_motivo and ev.id_cd_motivo in motivos:
        mensaje_original = motivos[ev.id_cd_motivo].mensaje_pantalla
    if not mensaje_original:
        mensaje_original = (ev.observacion or "").strip()

    permitido = ev.resultado == "S"
    # Mensaje + estado deducidos localmente según la cuota.
    #   estado: "ok" (verde) / "no" (rojo) / "anomalia" (amarillo).
    al_dia = cuota_al_dia(socio.ult_cuota_paga) if socio else False
    if permitido and not al_dia:
        # Anomalía: xSys dejó pasar pero la cuota está vencida -> alertar al operador.
        estado = "anomalia"
        mensaje = "Acceso Concedido · Cuota Vencida"
    elif not al_dia:
        estado = "no"
        mensaje = "Cuota Vencida"
    elif permitido:
        estado = "ok"
        mensaje = "Acceso Concedido"
    else:
        estado = "no"
        mensaje = "Chequear Oficina de Socios"

    foto_url = f"/api/xsys/socios/{ev.id_cliente}/foto/" if tiene_foto else None
    return {
        "id_es": ev.external_id,
        "fecha": ev.fecha.isoformat() if ev.fecha else None,
        "resultado": ev.resultado,
        "permitido": permitido,
        "cuota_al_dia": al_dia,
        "estado": estado,
        "lectura": _tipo_lectura(ev.id_controlador, controladores or {}),
        "mensaje": mensaje,
        "mensaje_original": mensaje_original,
        "id_cliente": ev.id_cliente,
        "nombre": (
            (f"{socio.apellido}, {socio.nombre}".strip(", ") or socio.razon_social)
            if socio else ""
        ),
        "categoria": (socio.categoria if socio else ""),
        "ult_cuota_paga": (socio.ult_cuota_paga.isoformat() if socio and socio.ult_cuota_paga else None),
        "foto_url": foto_url,
        "foto_thumb_url": (foto_url + "?thumb=1") if foto_url else None,
    }


def _accesos_disponibles() -> list[dict]:
    return [
        {"id_acceso": a.id_acceso, "descripcion": a.descripcion or f"Acceso {a.id_acceso}"}
        for a in XsysAcceso.objects.filter(activo=1).order_by("descripcion")
    ]


class PuertasListAPI(APIView):
    """GET /api/xsys/puertas/ → lista de accesos activos (para la hamburguesa)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"puertas": _accesos_disponibles()})


class PuertaSeleccionarAPI(APIView):
    """POST /api/xsys/puerta/seleccionar/ {id_acceso} → puerta que muestra el token."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        token = _pantalla_token(request)
        if not token:
            return Response({"detail": "Falta el token de pantalla."}, status=status.HTTP_400_BAD_REQUEST)
        id_acceso = request.data.get("id_acceso")
        if id_acceso in (None, ""):  # compat: aceptar id_accesos[0]
            lst = request.data.get("id_accesos")
            if isinstance(lst, (list, tuple)) and lst:
                id_acceso = lst[0]
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


def _columnas_de_puerta(id_acceso: int) -> list[dict]:
    """Columnas (molinetes) de una puerta: los grupos administrables definidos, o
    fallback automático = un molinete por controlador activo del acceso."""
    grupos = list(PuertaMolinete.objects.filter(id_acceso=id_acceso).order_by("orden", "id"))
    if grupos:
        return [
            {"key": f"g{g.id}", "nombre": g.nombre, "controladores": [int(c) for c in (g.id_controladores or [])]}
            for g in grupos
        ]
    return [
        {"key": f"c{c.id_controlador}", "nombre": c.descripcion or f"Ctrl {c.id_controlador}",
         "controladores": [c.id_controlador]}
        for c in XsysControlador.objects.filter(id_acceso=id_acceso, activo=1).order_by("descripcion")
    ]


class PuertaEstadoAPI(APIView):
    """GET /api/xsys/puerta/estado/ → estado por columna (una por molinete de la puerta).

    Cada columna: ``ultimo`` (quién está accediendo) + ``historial``. Del espejo
    local; identificado por header X-Pantalla-Token.
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
                "puertas": _accesos_disponibles(),
            })

        puerta = pantalla.id_acceso
        acceso = XsysAcceso.objects.filter(pk=puerta).first()
        cols_def = _columnas_de_puerta(puerta)

        # Eventos por columna (por conjunto de controladores del molinete).
        eventos_por_col = []
        for cd in cols_def:
            ctrls = cd["controladores"]
            evs = list(
                ExternalAccessLogEntry.objects
                .filter(id_controlador__in=ctrls, tipo="E")
                .order_by("-external_id")[: HISTORIAL_LEN + 1]
            ) if ctrls else []
            eventos_por_col.append(evs)

        # Resolución en lote de socios / fotos / motivos (evita N+1).
        todos = [e for evs in eventos_por_col for e in evs]
        cids = {e.id_cliente for e in todos if e.id_cliente}
        mids = {e.id_cd_motivo for e in todos if e.id_cd_motivo}
        ctrl_ids = {e.id_controlador for e in todos if e.id_controlador}
        socios = {s.id_cliente: s for s in XsysSocio.objects.filter(pk__in=cids)}
        fotos = set(XsysSocioFoto.objects.filter(id_cliente__in=cids).values_list("id_cliente", flat=True))
        motivos = {m.id_cd_motivo: m for m in XsysMotivo.objects.filter(pk__in=mids)}
        ctrls = {c.id_controlador: c for c in XsysControlador.objects.filter(pk__in=ctrl_ids)}

        columnas = []
        for cd, evs in zip(cols_def, eventos_por_col):
            columnas.append({
                "key": cd["key"],
                "nombre": cd["nombre"],
                "controladores": cd["controladores"],
                "ultimo": _evento_payload(evs[0], socios, fotos, motivos, ctrls) if evs else None,
                "historial": [_evento_payload(e, socios, fotos, motivos, ctrls) for e in evs[1:]],
            })
        return Response({
            "configurada": True,
            "ip": pantalla.ip,
            "nombre": pantalla.nombre or (acceso.descripcion if acceso else ""),
            "puerta": {"id_acceso": puerta, "descripcion": (acceso.descripcion if acceso else f"Acceso {puerta}")},
            "columnas": columnas,
        })


# ----------------------------------------------------------------------------
# Administración de molinetes (columnas) por puerta. Requiere login.
# ----------------------------------------------------------------------------

class ControladoresPorAccesoAPI(APIView):
    """GET /api/xsys/config/controladores/?id_acceso= → controladores de la puerta."""

    def get(self, request):
        try:
            id_acceso = int(request.query_params.get("id_acceso"))
        except (TypeError, ValueError):
            return Response({"detail": "id_acceso requerido."}, status=status.HTTP_400_BAD_REQUEST)
        ctrls = [
            {"id_controlador": c.id_controlador, "descripcion": c.descripcion or f"Ctrl {c.id_controlador}",
             "tipo_cont": c.tipo_cont, "activo": c.activo}
            for c in XsysControlador.objects.filter(id_acceso=id_acceso).order_by("descripcion")
        ]
        return Response({"id_acceso": id_acceso, "controladores": ctrls})


def _molinete_dict(m: PuertaMolinete) -> dict:
    return {"id": m.id, "id_acceso": m.id_acceso, "nombre": m.nombre,
            "id_controladores": m.id_controladores or [], "orden": m.orden}


class MolinetesConfigAPI(APIView):
    """GET ?id_acceso= (listar) / POST (crear) molinetes de una puerta."""

    def get(self, request):
        try:
            id_acceso = int(request.query_params.get("id_acceso"))
        except (TypeError, ValueError):
            return Response({"detail": "id_acceso requerido."}, status=status.HTTP_400_BAD_REQUEST)
        molinetes = [_molinete_dict(m) for m in PuertaMolinete.objects.filter(id_acceso=id_acceso).order_by("orden", "id")]
        return Response({"id_acceso": id_acceso, "molinetes": molinetes})

    def post(self, request):
        d = request.data
        try:
            id_acceso = int(d.get("id_acceso"))
        except (TypeError, ValueError):
            return Response({"detail": "id_acceso requerido."}, status=status.HTTP_400_BAD_REQUEST)
        nombre = (d.get("nombre") or "").strip()[:60]
        if not nombre:
            return Response({"detail": "nombre requerido."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ctrls = [int(c) for c in (d.get("id_controladores") or [])]
        except (TypeError, ValueError):
            return Response({"detail": "id_controladores inválido."}, status=status.HTTP_400_BAD_REQUEST)
        orden = int(d.get("orden") or PuertaMolinete.objects.filter(id_acceso=id_acceso).count())
        m = PuertaMolinete.objects.create(id_acceso=id_acceso, nombre=nombre, id_controladores=ctrls, orden=orden)
        return Response(_molinete_dict(m), status=status.HTTP_201_CREATED)


class MolineteConfigDetailAPI(APIView):
    """PUT / DELETE /api/xsys/config/molinetes/<id>/."""

    def put(self, request, mid: int):
        m = PuertaMolinete.objects.filter(pk=mid).first()
        if not m:
            return Response(status=status.HTTP_404_NOT_FOUND)
        d = request.data
        if "nombre" in d:
            m.nombre = (d.get("nombre") or "").strip()[:60] or m.nombre
        if "id_controladores" in d:
            try:
                m.id_controladores = [int(c) for c in (d.get("id_controladores") or [])]
            except (TypeError, ValueError):
                return Response({"detail": "id_controladores inválido."}, status=status.HTTP_400_BAD_REQUEST)
        if "orden" in d:
            try:
                m.orden = int(d.get("orden"))
            except (TypeError, ValueError):
                pass
        m.save()
        return Response(_molinete_dict(m))

    def delete(self, request, mid: int):
        PuertaMolinete.objects.filter(pk=mid).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MolinetesAutoAPI(APIView):
    """POST /api/xsys/config/molinetes/auto/ {id_acceso} → crea un molinete por
    cada controlador activo del acceso (si no hay molinetes definidos)."""

    def post(self, request):
        try:
            id_acceso = int(request.data.get("id_acceso"))
        except (TypeError, ValueError):
            return Response({"detail": "id_acceso requerido."}, status=status.HTTP_400_BAD_REQUEST)
        existentes = set()
        for m in PuertaMolinete.objects.filter(id_acceso=id_acceso):
            existentes.update(int(c) for c in (m.id_controladores or []))
        creados = 0
        base = PuertaMolinete.objects.filter(id_acceso=id_acceso).count()
        for c in XsysControlador.objects.filter(id_acceso=id_acceso, activo=1).order_by("descripcion"):
            if c.id_controlador in existentes:
                continue
            PuertaMolinete.objects.create(
                id_acceso=id_acceso, nombre=c.descripcion or f"Ctrl {c.id_controlador}",
                id_controladores=[c.id_controlador], orden=base + creados,
            )
            creados += 1
        return Response({"creados": creados})


class SocioDetalleAPI(APIView):
    """GET /api/xsys/socios/<id_cliente>/detalle/ → datos del socio para el modal
    del monitor (foto, categoría, última cuota, contratos activos)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, id_cliente: int):
        socio = XsysSocio.objects.filter(pk=id_cliente).first()
        tiene_foto = XsysSocioFoto.objects.filter(id_cliente=id_cliente).exists()
        contratos = [
            {
                "descripcion": c.descripcion,
                "fecha_alta": c.fecha_alta.isoformat() if c.fecha_alta else None,
            }
            for c in XsysContrato.objects.filter(id_cliente=id_cliente, activo=1).order_by("descripcion")
        ]
        return Response({
            "id_cliente": id_cliente,
            "nombre": (
                (f"{socio.apellido}, {socio.nombre}".strip(", ") or socio.razon_social)
                if socio else ""
            ),
            "categoria": (socio.categoria if socio else ""),
            "ult_cuota_paga": (socio.ult_cuota_paga.isoformat() if socio and socio.ult_cuota_paga else None),
            "foto_url": f"/api/xsys/socios/{id_cliente}/foto/" if tiene_foto else None,
            "contratos": contratos,
        })

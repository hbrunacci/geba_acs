from __future__ import annotations

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from access_control.models import BiostarAccessEvent
from access_control.models.models import ExternalAccessLogEntry
from common.roles import PuedeConfigPuertas
from institutions.models import AccessDoor, DoorController, DoorTurnstileGroup

from xsys.models import (
    PantallaPuerta,
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
from xsys.services import foto_fetch
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
        if not foto_disponible:
            foto_fetch.request_foto(socio.id_cliente)  # buscar en xSys async

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
        "doc_nro": (socio.doc_nro if socio else None),
        "nombre": (
            (f"{socio.apellido}, {socio.nombre}".strip(", ") or socio.razon_social)
            if socio else ""
        ),
        "categoria": (socio.categoria if socio else ""),
        "ult_cuota_paga": (socio.ult_cuota_paga.isoformat() if socio and socio.ult_cuota_paga else None),
        "foto_url": foto_url,
        "foto_thumb_url": (foto_url + "?thumb=1") if foto_url else None,
    }


def _facial_evento_payload(ev: BiostarAccessEvent, socios: dict, fotos: set) -> dict:
    """Payload de un acceso facial BioStar, con la MISMA forma que _evento_payload
    para poder fusionarlo en la misma columna del visor. La identidad del equipo
    (``facial_equipo``) es el dato que xSys no tiene: viene del log de BioStar."""
    socio = socios.get(ev.id_cliente)
    tiene_foto = ev.id_cliente in fotos
    permitido = bool(ev.permitido)
    al_dia = cuota_al_dia(socio.ult_cuota_paga) if socio else False
    if permitido and not al_dia:
        estado, mensaje = "anomalia", "Acceso Concedido · Cuota Vencida"
    elif not permitido:
        estado, mensaje = "no", "Acceso Denegado"
    elif not al_dia:
        estado, mensaje = "no", "Cuota Vencida"
    else:
        estado, mensaje = "ok", "Acceso Concedido"
    foto_url = f"/api/xsys/socios/{ev.id_cliente}/foto/" if tiene_foto else None
    return {
        # id_es negativo: no colisiona con los external_id (positivos) de xSys.
        "id_es": -ev.id,
        "fecha": ev.fecha.isoformat() if ev.fecha else None,
        "resultado": "S" if permitido else "N",
        "permitido": permitido,
        "cuota_al_dia": al_dia,
        "estado": estado,
        "lectura": "facial",
        "mensaje": mensaje,
        "mensaje_original": ev.event_name,
        "id_cliente": ev.id_cliente,
        "doc_nro": (socio.doc_nro if socio else None),
        "nombre": (
            (f"{socio.apellido}, {socio.nombre}".strip(", ") or socio.razon_social)
            if socio else ""
        ),
        "categoria": (socio.categoria if socio else ""),
        "ult_cuota_paga": (socio.ult_cuota_paga.isoformat() if socio and socio.ult_cuota_paga else None),
        "foto_url": foto_url,
        "foto_thumb_url": (foto_url + "?thumb=1") if foto_url else None,
        "facial_equipo": ev.device_name,
    }


def _puertas_disponibles() -> list[dict]:
    """Puertas locales activas (para la hamburguesa del monitor)."""
    return [
        {"id": d.id, "nombre": d.name, "xsys_id_acceso": d.xsys_id_acceso}
        for d in AccessDoor.objects.filter(is_active=True).order_by("name")
    ]


class PuertasListAPI(APIView):
    """GET /api/xsys/puertas/ → puertas activas (para la hamburguesa)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"puertas": _puertas_disponibles()})


class PuertaSeleccionarAPI(APIView):
    """POST /api/xsys/puerta/seleccionar/ {puerta_id} → puerta que muestra el token."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        token = _pantalla_token(request)
        if not token:
            return Response({"detail": "Falta el token de pantalla."}, status=status.HTTP_400_BAD_REQUEST)
        puerta_id = request.data.get("puerta_id")
        if puerta_id in (None, ""):  # compat: aceptar id_acceso viejo o id_accesos[0]
            puerta_id = request.data.get("id_acceso")
            lst = request.data.get("id_accesos")
            if puerta_id in (None, "") and isinstance(lst, (list, tuple)) and lst:
                puerta_id = lst[0]
        if puerta_id in (None, ""):
            return Response({"detail": "puerta_id requerido."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            puerta_id = int(puerta_id)
        except (TypeError, ValueError):
            return Response({"detail": "puerta_id inválido."}, status=status.HTTP_400_BAD_REQUEST)
        door = AccessDoor.objects.filter(pk=puerta_id).first()
        if door is None:
            return Response({"detail": "La puerta no existe."}, status=status.HTTP_404_NOT_FOUND)
        nombre = (request.data.get("nombre") or "").strip()[:60]
        defaults = {"door": door, "last_seen": timezone.now(), "ip": _client_ip(request)}
        if nombre:
            defaults["nombre"] = nombre
        PantallaPuerta.objects.update_or_create(token=token, defaults=defaults)
        return Response({"ok": True, "token": token, "puerta_id": door.id})


def _columnas_de_puerta(door: AccessDoor) -> list[dict]:
    """Columnas de una puerta: los grupos de molinetes definidos, o fallback
    automático = una columna por cada controlador asignado a la puerta."""
    grupos = list(door.turnstile_groups.order_by("orden", "id"))
    if grupos:
        return [
            {"key": f"g{g.id}", "nombre": g.nombre,
             "controladores": [int(c) for c in (g.id_controladores or [])],
             "biostar_devices": [int(d) for d in (g.biostar_device_ids or [])]}
            for g in grupos
        ]
    asignados = list(door.controllers.order_by("orden", "id"))
    descs = {
        c.id_controlador: c
        for c in XsysControlador.objects.filter(pk__in=[a.id_controlador for a in asignados])
    }
    cols = []
    for a in asignados:
        x = descs.get(a.id_controlador)
        nombre = x.descripcion if (x and x.descripcion) else f"Ctrl {a.id_controlador}"
        cols.append({"key": f"c{a.id_controlador}", "nombre": nombre,
                     "controladores": [a.id_controlador], "biostar_devices": []})
    return cols


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
        door = pantalla.door
        if door is None:
            return Response({
                "configurada": False,
                "ip": pantalla.ip,
                "puertas": _puertas_disponibles(),
            })

        cols_def = _columnas_de_puerta(door)

        # El monitor muestra solo los accesos del día en curso (hora local).
        hoy = timezone.localdate()

        # Por columna: eventos xSys (por controlador) + accesos faciales BioStar
        # (por device). Los faciales son la única fuente con identidad por-equipo.
        xsys_por_col = []
        facial_por_col = []
        for cd in cols_def:
            ctrls = cd["controladores"]
            xs = list(
                ExternalAccessLogEntry.objects
                .filter(id_controlador__in=ctrls, tipo="E", fecha__date=hoy)
                .order_by("-external_id")[: HISTORIAL_LEN + 1]
            ) if ctrls else []
            devs = cd.get("biostar_devices") or []
            fx = list(
                BiostarAccessEvent.objects
                .filter(device_id__in=devs, id_cliente__isnull=False, fecha__date=hoy)
                .order_by("-fecha")[: HISTORIAL_LEN + 1]
            ) if devs else []
            xsys_por_col.append(xs)
            facial_por_col.append(fx)

        # Resolución en lote de socios / fotos / motivos (ambas fuentes; evita N+1).
        todos_x = [e for evs in xsys_por_col for e in evs]
        todos_f = [e for evs in facial_por_col for e in evs]
        cids = {e.id_cliente for e in todos_x if e.id_cliente}
        cids |= {e.id_cliente for e in todos_f if e.id_cliente}
        mids = {e.id_cd_motivo for e in todos_x if e.id_cd_motivo}
        ctrl_ids = {e.id_controlador for e in todos_x if e.id_controlador}
        socios = {s.id_cliente: s for s in XsysSocio.objects.filter(pk__in=cids)}
        fotos = set(XsysSocioFoto.objects.filter(id_cliente__in=cids).values_list("id_cliente", flat=True))
        # Fallback async: los socios sin foto local se buscan en xSys en segundo
        # plano; la foto aparecerá en un refresco posterior.
        foto_fetch.request_many(cids - fotos)
        motivos = {m.id_cd_motivo: m for m in XsysMotivo.objects.filter(pk__in=mids)}
        ctrls = {c.id_controlador: c for c in XsysControlador.objects.filter(pk__in=ctrl_ids)}

        columnas = []
        for cd, xs, fx in zip(cols_def, xsys_por_col, facial_por_col):
            # (fecha, payload) para poder ordenar la mezcla por tiempo (desc).
            items = [(e.fecha, _evento_payload(e, socios, fotos, motivos, ctrls)) for e in xs]
            items += [(e.fecha, _facial_evento_payload(e, socios, fotos)) for e in fx]
            items.sort(key=lambda t: t[0], reverse=True)
            payloads = [p for _, p in items[: HISTORIAL_LEN + 1]]
            columnas.append({
                "key": cd["key"],
                "nombre": cd["nombre"],
                "controladores": cd["controladores"],
                "biostar_devices": cd.get("biostar_devices") or [],
                "ultimo": payloads[0] if payloads else None,
                "historial": payloads[1:],
            })
        return Response({
            "configurada": True,
            "ip": pantalla.ip,
            "nombre": pantalla.nombre or door.name,
            "puerta": {"id": door.id, "nombre": door.name, "xsys_id_acceso": door.xsys_id_acceso},
            "columnas": columnas,
        })


class AccesosBuscarAPI(APIView):
    """GET /api/xsys/accesos/buscar/?q= → accesos de HOY en esta puerta que matchean.

    Busca por apellido, N° de socio (Id_Cliente) o DNI (Doc_Nro). Devuelve todas
    las ocurrencias del día indicando por qué molinete pasó cada una. Usa el
    espejo local; identificada por X-Pantalla-Token (misma puerta del monitor).
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from django.db.models import Q

        pantalla = _registrar_pantalla(request)
        if pantalla is None or pantalla.door is None:
            return Response({"detail": "La pantalla no tiene una puerta asignada."},
                            status=status.HTTP_400_BAD_REQUEST)
        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            return Response({"q": q, "resultados": []})

        door = pantalla.door
        cols_def = _columnas_de_puerta(door)
        # Mapa controlador -> nombre del molinete (columna) para etiquetar cada acceso.
        ctrl_a_molinete: dict[int, str] = {}
        for cd in cols_def:
            for c in cd["controladores"]:
                ctrl_a_molinete[c] = cd["nombre"]
        if not ctrl_a_molinete:
            return Response({"q": q, "resultados": []})

        # Socios que matchean el texto: apellido/nombre siempre; si es numérico,
        # también por N° de socio (Id_Cliente) o DNI (Doc_Nro).
        criterio = Q(apellido__icontains=q) | Q(nombre__icontains=q)
        if q.isdigit():
            criterio |= Q(id_cliente=int(q)) | Q(doc_nro=int(q))
        socios = {s.id_cliente: s for s in XsysSocio.objects.filter(criterio)[:300]}
        if not socios:
            return Response({"q": q, "resultados": []})

        hoy = timezone.localdate()
        evs = list(
            ExternalAccessLogEntry.objects
            .filter(id_controlador__in=ctrl_a_molinete.keys(), tipo="E",
                    fecha__date=hoy, id_cliente__in=socios.keys())
            .order_by("-external_id")[:300]
        )
        cids = {e.id_cliente for e in evs if e.id_cliente}
        con_foto = set(
            XsysSocioFoto.objects.filter(id_cliente__in=cids).values_list("id_cliente", flat=True)
        )

        resultados = []
        for ev in evs:
            s = socios.get(ev.id_cliente)
            foto_url = f"/api/xsys/socios/{ev.id_cliente}/foto/" if ev.id_cliente in con_foto else None
            resultados.append({
                "id_es": ev.external_id,
                "fecha": ev.fecha.isoformat() if ev.fecha else None,
                "molinete": ctrl_a_molinete.get(ev.id_controlador, ""),
                "id_cliente": ev.id_cliente,
                "doc_nro": (s.doc_nro if s else None),
                "nombre": (
                    (f"{s.apellido}, {s.nombre}".strip(", ") or s.razon_social) if s else ""
                ),
                "resultado": ev.resultado,
                "permitido": ev.resultado == "S",
                "foto_thumb_url": (foto_url + "?thumb=1") if foto_url else None,
            })
        return Response({"q": q, "puerta": door.name, "resultados": resultados})


# ----------------------------------------------------------------------------
# Armado de puertas (requiere login). Flujo en 3 pasos:
#   1) alta de la puerta            -> PuertasConfigAPI / PuertaConfigDetailAPI
#   2) asignar controladores xSys   -> PuertaControladoresAPI (+ catálogo)
#   3) definir grupos de molinetes  -> MolinetesConfigAPI / *DetailAPI / *AutoAPI
# ----------------------------------------------------------------------------

def _puerta_dict(d: AccessDoor) -> dict:
    return {
        "id": d.id,
        "nombre": d.name,
        "code": d.code,
        "xsys_id_acceso": d.xsys_id_acceso,
        "is_active": d.is_active,
        "controladores": list(d.controllers.order_by("orden", "id").values_list("id_controlador", flat=True)),
        "molinetes": d.turnstile_groups.count(),
    }


def _int_or_none(value):
    s = str(value).strip() if value is not None else ""
    return int(s) if s.lstrip("-").isdigit() else None


class _ConfigPuertasAPIView(APIView):
    """Base de las APIs de configuración de puertas/molinetes.

    Solo accesible por administradores o por el grupo Configuración de Puertas.
    """

    permission_classes = [PuedeConfigPuertas]


class PuertasConfigAPI(_ConfigPuertasAPIView):
    """GET (listar todas las puertas) / POST (alta de puerta)."""

    def get(self, request):
        return Response({"puertas": [_puerta_dict(d) for d in AccessDoor.objects.all().order_by("name")]})

    def post(self, request):
        d = request.data
        nombre = (d.get("nombre") or "").strip()[:255]
        if not nombre:
            return Response({"detail": "nombre requerido."}, status=status.HTTP_400_BAD_REQUEST)
        door = AccessDoor.objects.create(
            name=nombre,
            code=(d.get("code") or "").strip()[:32],
            xsys_id_acceso=_int_or_none(d.get("xsys_id_acceso")),
        )
        return Response(_puerta_dict(door), status=status.HTTP_201_CREATED)


class PuertaConfigDetailAPI(_ConfigPuertasAPIView):
    """PUT / DELETE /api/xsys/config/puertas/<id>/."""

    def put(self, request, pid: int):
        door = AccessDoor.objects.filter(pk=pid).first()
        if not door:
            return Response(status=status.HTTP_404_NOT_FOUND)
        d = request.data
        if "nombre" in d:
            door.name = (d.get("nombre") or "").strip()[:255] or door.name
        if "code" in d:
            door.code = (d.get("code") or "").strip()[:32]
        if "xsys_id_acceso" in d:
            door.xsys_id_acceso = _int_or_none(d.get("xsys_id_acceso"))
        if "is_active" in d:
            door.is_active = _flag(d.get("is_active"))
        door.save()
        return Response(_puerta_dict(door))

    def delete(self, request, pid: int):
        AccessDoor.objects.filter(pk=pid).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ControladoresXsysAPI(_ConfigPuertasAPIView):
    """GET /api/xsys/config/controladores-xsys/?id_acceso= → catálogo de
    controladores de xSys para asignar a una puerta. Sin id_acceso: todos.
    Incluye la lista de accesos activos para poder filtrar en la UI."""

    def get(self, request):
        qs = XsysControlador.objects.all()
        id_acceso = request.query_params.get("id_acceso")
        if id_acceso not in (None, ""):
            try:
                qs = qs.filter(id_acceso=int(id_acceso))
            except (TypeError, ValueError):
                return Response({"detail": "id_acceso inválido."}, status=status.HTTP_400_BAD_REQUEST)
        ctrls = [
            {"id_controlador": c.id_controlador, "id_acceso": c.id_acceso,
             "descripcion": c.descripcion or f"Ctrl {c.id_controlador}",
             "tipo_cont": c.tipo_cont, "activo": c.activo, "ip": c.ip}
            for c in qs.order_by("id_acceso", "descripcion")
        ]
        accesos = [
            {"id_acceso": a.id_acceso, "descripcion": a.descripcion or f"Acceso {a.id_acceso}"}
            for a in XsysAcceso.objects.filter(activo=1).order_by("descripcion")
        ]
        return Response({"controladores": ctrls, "accesos": accesos})


class BiostarDevicesCatalogAPI(_ConfigPuertasAPIView):
    """GET /api/xsys/config/biostar-devices/ → catálogo de faciales BioStar para
    asignar a un grupo de molinetes. Trae en vivo de BioStar; si no responde,
    cae al espejo local de eventos (equipos ya vistos)."""

    def get(self, request):
        try:
            from access_control.services.biostar2_client import BioStar2Client

            client = BioStar2Client.from_db_and_env()
            d = client.list_devices()
            rows = (d.get("DeviceCollection") or d).get("rows") or []
            devices = []
            for r in rows:
                try:
                    dev_id = int(r.get("id"))
                except (TypeError, ValueError):
                    continue
                ip = r.get("ip_address") or (r.get("lan") or {}).get("ip") or ""
                devices.append({"device_id": dev_id, "name": r.get("name") or "", "ip": ip})
            devices.sort(key=lambda x: x["name"])
            return Response({"devices": devices})
        except Exception as exc:  # BioStar no disponible: fallback al espejo local
            from access_control.models import BiostarAccessEvent

            vistos = (
                BiostarAccessEvent.objects.values("device_id", "device_name")
                .distinct().order_by("device_name")
            )
            devices = [
                {"device_id": v["device_id"], "name": v["device_name"], "ip": ""}
                for v in vistos
            ]
            return Response({"devices": devices, "warning": f"BioStar no disponible: {exc}"})


class PuertaControladoresAPI(_ConfigPuertasAPIView):
    """GET / PUT los controladores asignados a la puerta (el pool del paso 2).

    GET  → controladores del pool, enriquecidos con datos de xSys.
    PUT {id_controladores:[...]} → reemplaza el pool completo (y limpia de los
    grupos los controladores que dejaron de estar asignados)."""

    def get(self, request, pid: int):
        door = AccessDoor.objects.filter(pk=pid).first()
        if not door:
            return Response(status=status.HTTP_404_NOT_FOUND)
        asignados = list(door.controllers.order_by("orden", "id"))
        descs = {
            c.id_controlador: c
            for c in XsysControlador.objects.filter(pk__in=[a.id_controlador for a in asignados])
        }
        out = []
        for a in asignados:
            x = descs.get(a.id_controlador)
            out.append({
                "id_controlador": a.id_controlador,
                "descripcion": (x.descripcion if x and x.descripcion else f"Ctrl {a.id_controlador}"),
                "tipo_cont": (x.tipo_cont if x else ""),
                "activo": (x.activo if x else None),
                "ip": (x.ip if x else ""),
            })
        return Response({"puerta_id": door.id, "controladores": out})

    def put(self, request, pid: int):
        door = AccessDoor.objects.filter(pk=pid).first()
        if not door:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            ids = [int(c) for c in (request.data.get("id_controladores") or [])]
        except (TypeError, ValueError):
            return Response({"detail": "id_controladores inválido."}, status=status.HTTP_400_BAD_REQUEST)
        ids = list(dict.fromkeys(ids))  # dedup preservando orden
        door.controllers.all().delete()
        DoorController.objects.bulk_create(
            [DoorController(door=door, id_controlador=cid, orden=i) for i, cid in enumerate(ids)]
        )
        # Limpiar de los grupos los controladores que ya no están en el pool.
        for g in door.turnstile_groups.all():
            filtrados = [c for c in (g.id_controladores or []) if c in ids]
            if filtrados != list(g.id_controladores or []):
                g.id_controladores = filtrados
                g.save(update_fields=["id_controladores", "updated_at"])
        return Response({"puerta_id": door.id, "id_controladores": ids})


def _molinete_dict(m: DoorTurnstileGroup) -> dict:
    return {"id": m.id, "puerta_id": m.door_id, "nombre": m.nombre,
            "id_controladores": m.id_controladores or [],
            "biostar_device_ids": m.biostar_device_ids or [], "orden": m.orden}


def _parse_biostar_device_ids(value) -> list[int]:
    """Normaliza una lista de device_id de BioStar a ints (dedup, sin vacíos)."""
    out: list[int] = []
    for v in (value or []):
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(out))


class MolinetesConfigAPI(_ConfigPuertasAPIView):
    """GET ?puerta_id= (listar) / POST (crear) grupos de molinetes de una puerta."""

    def get(self, request):
        try:
            pid = int(request.query_params.get("puerta_id"))
        except (TypeError, ValueError):
            return Response({"detail": "puerta_id requerido."}, status=status.HTTP_400_BAD_REQUEST)
        molinetes = [
            _molinete_dict(m)
            for m in DoorTurnstileGroup.objects.filter(door_id=pid).order_by("orden", "id")
        ]
        return Response({"puerta_id": pid, "molinetes": molinetes})

    def post(self, request):
        d = request.data
        try:
            pid = int(d.get("puerta_id"))
        except (TypeError, ValueError):
            return Response({"detail": "puerta_id requerido."}, status=status.HTTP_400_BAD_REQUEST)
        door = AccessDoor.objects.filter(pk=pid).first()
        if not door:
            return Response({"detail": "La puerta no existe."}, status=status.HTTP_404_NOT_FOUND)
        nombre = (d.get("nombre") or "").strip()[:60]
        if not nombre:
            return Response({"detail": "nombre requerido."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ctrls = [int(c) for c in (d.get("id_controladores") or [])]
        except (TypeError, ValueError):
            return Response({"detail": "id_controladores inválido."}, status=status.HTTP_400_BAD_REQUEST)
        # Solo controladores que pertenezcan al pool de la puerta.
        pool = set(door.controllers.values_list("id_controlador", flat=True))
        ctrls = [c for c in ctrls if c in pool]
        # Faciales BioStar: no tienen "pool", se guardan tal cual (device_id globales).
        biostar = _parse_biostar_device_ids(d.get("biostar_device_ids"))
        orden = int(d.get("orden") or door.turnstile_groups.count())
        m = DoorTurnstileGroup.objects.create(
            door=door, nombre=nombre, id_controladores=ctrls,
            biostar_device_ids=biostar, orden=orden,
        )
        return Response(_molinete_dict(m), status=status.HTTP_201_CREATED)


class MolineteConfigDetailAPI(_ConfigPuertasAPIView):
    """PUT / DELETE /api/xsys/config/molinetes/<id>/."""

    def put(self, request, mid: int):
        m = DoorTurnstileGroup.objects.filter(pk=mid).first()
        if not m:
            return Response(status=status.HTTP_404_NOT_FOUND)
        d = request.data
        if "nombre" in d:
            m.nombre = (d.get("nombre") or "").strip()[:60] or m.nombre
        if "id_controladores" in d:
            try:
                ctrls = [int(c) for c in (d.get("id_controladores") or [])]
            except (TypeError, ValueError):
                return Response({"detail": "id_controladores inválido."}, status=status.HTTP_400_BAD_REQUEST)
            pool = set(m.door.controllers.values_list("id_controlador", flat=True))
            m.id_controladores = [c for c in ctrls if c in pool]
        if "biostar_device_ids" in d:
            m.biostar_device_ids = _parse_biostar_device_ids(d.get("biostar_device_ids"))
        if "orden" in d:
            try:
                m.orden = int(d.get("orden"))
            except (TypeError, ValueError):
                pass
        m.save()
        return Response(_molinete_dict(m))

    def delete(self, request, mid: int):
        DoorTurnstileGroup.objects.filter(pk=mid).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MolinetesAutoAPI(_ConfigPuertasAPIView):
    """POST /api/xsys/config/molinetes/auto/ {puerta_id} → crea un grupo por cada
    controlador asignado a la puerta que todavía no esté en algún grupo."""

    def post(self, request):
        try:
            pid = int(request.data.get("puerta_id"))
        except (TypeError, ValueError):
            return Response({"detail": "puerta_id requerido."}, status=status.HTTP_400_BAD_REQUEST)
        door = AccessDoor.objects.filter(pk=pid).first()
        if not door:
            return Response({"detail": "La puerta no existe."}, status=status.HTTP_404_NOT_FOUND)
        existentes = set()
        for g in door.turnstile_groups.all():
            existentes.update(int(c) for c in (g.id_controladores or []))
        asignados = list(door.controllers.order_by("orden", "id"))
        descs = {
            c.id_controlador: c
            for c in XsysControlador.objects.filter(pk__in=[a.id_controlador for a in asignados])
        }
        creados = 0
        base = door.turnstile_groups.count()
        for a in asignados:
            if a.id_controlador in existentes:
                continue
            x = descs.get(a.id_controlador)
            nombre = x.descripcion if (x and x.descripcion) else f"Ctrl {a.id_controlador}"
            DoorTurnstileGroup.objects.create(
                door=door, nombre=nombre[:60], id_controladores=[a.id_controlador], orden=base + creados,
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
        if not tiene_foto:
            foto_fetch.request_foto(id_cliente)  # buscar en xSys async
        contratos = [
            {
                "descripcion": c.descripcion,
                "fecha_alta": c.fecha_alta.isoformat() if c.fecha_alta else None,
            }
            for c in XsysContrato.objects.filter(id_cliente=id_cliente, activo=1).order_by("descripcion")
        ]
        return Response({
            "id_cliente": id_cliente,
            "doc_nro": (socio.doc_nro if socio else None),
            "nombre": (
                (f"{socio.apellido}, {socio.nombre}".strip(", ") or socio.razon_social)
                if socio else ""
            ),
            "categoria": (socio.categoria if socio else ""),
            "ult_cuota_paga": (socio.ult_cuota_paga.isoformat() if socio and socio.ult_cuota_paga else None),
            "foto_url": f"/api/xsys/socios/{id_cliente}/foto/" if tiene_foto else None,
            "contratos": contratos,
        })

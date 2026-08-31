from __future__ import annotations

import datetime
import hashlib
import logging
import re
from pathlib import Path

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from access_control.models import BiostarAccessEvent, SocioAviso
from access_control.models.models import ExternalAccessLogEntry
from common.roles import PuedeConfigPuertas
from institutions.models import AccessDoor, DoorController, DoorTurnstileGroup

from xsys.models import (
    PantallaPuerta,
    XsysAcceso,
    XsysBajaRevision,
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
from xsys.services import contratos as contratos_svc
from xsys.services import foto_fetch
from xsys.services.access import resolver_acceso, resolver_socio
from xsys.services.cuota import cuota_al_dia
from xsys.services.diagnostico import diagnosticar

logger = logging.getLogger(__name__)


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

        # Primero SOLO los metadatos, sin traer el blob: una pantalla muestra una
        # y otra vez las mismas caras, así que la enorme mayoría de los pedidos se
        # puede resolver con un 304. Antes se leía la imagen entera de la base en
        # cada request y las fotos competían con los polls del visor por los
        # mismos workers; cuando no llegaban, la tarjeta quedaba sin cara.
        meta = (qs.filter(nro=nro) if nro else qs.order_by("nro")).values(
            "id", "sha256", "synced_at").first()
        if meta is None:
            return HttpResponse(status=status.HTTP_404_NOT_FOUND)

        marca = meta["sha256"] or (meta["synced_at"].isoformat() if meta["synced_at"] else "0")
        etag = '"%s-%s-%s"' % (meta["id"], marca[:16], "t" if thumb else "f")
        if request.headers.get("If-None-Match") == etag:
            resp = HttpResponse(status=304)
            resp["ETag"] = etag
            resp["Cache-Control"] = "private, max-age=86400"
            return resp

        foto = XsysSocioFoto.objects.filter(pk=meta["id"]).first()
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
        # Un día de caché, pero con ETag: la foto de un socio no cambia salvo que
        # la reemplacen, y ahí cambia el sha256 y con él el ETag.
        response["Cache-Control"] = "private, max-age=86400"
        response["ETag"] = etag
        response["Content-Length"] = str(len(data))
        return response


# ----------------------------------------------------------------------------
# Monitor de puerta (kiosco): endpoints abiertos (AllowAny), pantalla
# identificada por TOKEN (header X-Pantalla-Token). La IP se guarda solo como
# dato informativo del lugar.
# ----------------------------------------------------------------------------

def _pantalla_token(request) -> str:
    return (request.META.get("HTTP_X_PANTALLA_TOKEN", "") or "").strip()[:64]


# Cada cuánto se anota que una pantalla sigue viva. El visor sondea 2 veces por
# segundo: escribir en cada poll eran 3 queries y un UPDATE por request, o sea
# ~16 escrituras por segundo sobre la misma tabla con 8 pantallas, sólo para
# refrescar un "last_seen" que nadie mira al segundo.
_LATIDO_PANTALLA_SEG = 30


def _registrar_pantalla(request) -> PantallaPuerta | None:
    """Upsert de la pantalla por token; None si no vino token."""
    token = _pantalla_token(request)
    if not token:
        return None
    pantalla, creada = PantallaPuerta.objects.get_or_create(token=token)
    ahora = timezone.now()
    vencido = (
        creada
        or pantalla.last_seen is None
        or (ahora - pantalla.last_seen).total_seconds() >= _LATIDO_PANTALLA_SEG
    )
    if vencido:
        ua = (request.META.get("HTTP_USER_AGENT", "") or "")[:255]
        ip = _client_ip(request)
        PantallaPuerta.objects.filter(pk=pantalla.pk).update(
            last_seen=ahora, user_agent=ua, ip=ip
        )
        # Se actualiza en memoria en vez de releer: ``get_or_create`` ya trajo la
        # fila completa y el ``refresh_from_db`` era una tercera query por poll.
        pantalla.last_seen, pantalla.user_agent, pantalla.ip = ahora, ua, ip
    return pantalla


# Cuántos ingresos de historial se devuelven por columna (para paginar de a 5).
HISTORIAL_LEN = 50

# Días que se pueden mirar hacia atrás en el visor, incluido hoy. Coincide con la
# retención local: CD_ES se espeja sólo la última semana y los eventos faciales
# se purgan a los 7 días, así que más atrás sólo habría columnas vacías.
_DIAS_HISTORIAL = 7


def _dia_pedido(request) -> tuple[datetime.date, datetime.date, datetime.date]:
    """(día a mostrar, mínimo permitido, hoy) a partir de ``?fecha=YYYY-MM-DD``.

    Se acota en el servidor —no en la pantalla— para que ningún cliente pueda
    pedir más atrás de lo que se conserva: CD_ES y los eventos faciales se purgan
    a los 7 días y más atrás sólo habría columnas vacías. Una fecha ilegible o
    futura cae en hoy.
    """
    hoy = timezone.localdate()
    minimo = hoy - datetime.timedelta(days=_DIAS_HISTORIAL - 1)
    pedida = (request.query_params.get("fecha") or "").strip()
    if not pedida:
        return hoy, minimo, hoy
    try:
        dia = datetime.date.fromisoformat(pedida)
    except ValueError:
        return hoy, minimo, hoy
    return min(max(dia, minimo), hoy), minimo, hoy


def _version_visor() -> str:
    """Huella del template del monitor, para que las pantallas se auto-recarguen.

    Los molinetes son kioscos que quedan abiertos días: sin esto, un cambio en el
    visor no se ve hasta que alguien va y aprieta F5 en cada pantalla. El valor
    viaja en cada respuesta de estado y la pantalla se recarga sola si cambió.
    """
    global _VERSION_CACHE
    ruta = Path(__file__).resolve().parent / "templates" / "xsys" / "puerta_monitor.html"
    try:
        marca = str(ruta.stat().st_mtime_ns)
    except OSError:
        return ""
    if _VERSION_CACHE[0] != marca:
        _VERSION_CACHE = (marca, hashlib.md5(marca.encode()).hexdigest()[:8])
    return _VERSION_CACHE[1]


# (mtime del template, hash corto) — evita stat+hash en cada poll de cada pantalla.
_VERSION_CACHE: tuple[str, str] = ("", "")


def _tipo_lectura(id_controlador, controladores: dict) -> str:
    """'facial' si el controlador es un facial (Tipo_Cont F/W), si no 'credencial'."""
    ctrl = controladores.get(id_controlador)
    if ctrl and (ctrl.tipo_cont or "").upper() in ("F", "W"):
        return "facial"
    return "credencial"


# Motivos de habilitación que NO dependen de la cuota social: la categoría del
# socio (205) o el acceso master (202). Los vitalicios +71, olímpicos, honorarios
# y empleados entran por su categoría y para ellos la cuota social es VOLUNTARIA:
# pueden deber, pero nunca están "con la cuota vencida". Se toma del motivo con el
# que xSys los habilita en vez de una lista de categorías, para no quedar
# desactualizado cuando el club agregue una.
_MOTIVOS_SIN_CUOTA = {202, 204, 205}

# Rechazos por el QR dinámico de la app: inválido (114), vencido (115) o ya usado
# (116). El motivo es técnico y exacto, y no dice nada de la cuota. Desde que xSys
# identifica al socio en el rechazo por QR vencido (31/08/2026) hay que separarlos
# a mano: si no, al que sólo tardó en pasar el código el visor le contestaría
# "Cuota Vencida" o "Chequear Oficina de Socios" y lo mandaría a hacer un trámite
# que no necesita.
_MOTIVOS_QR = {114, 115, 116}

# Categorías (Clientes.Id_Tipo_Cli) que directamente NO tienen cuota social que
# pagar: concesionarios, alumnos y docentes del instituto, profesores,
# proveedores, visitas y no socios. A ellos "Cuota Vencida" es siempre un falso
# positivo, entren o no: su habilitación sale del contrato o del producto, no de
# la cuota. Va por id y no por descripción porque el id es lo que usa xSys en
# CD_Accesos_Cli_Tipos y la descripción la renombran.
#
# NO está acá a propósito: EMPLEADO (1006), que sí registra cuota en el 99% de
# los casos y además ya queda exento por el motivo 205.
_CATEGORIAS_SIN_CUOTA = {
    1015,  # CONCESIONARIO
    1018,  # INVITADOS
    1103,  # NO SOCIO
    1113,  # VISITA
    1127,  # ALUMNO IGSM
    1131,  # PROFESORES
    1132,  # PROVEEDORES
    1135,  # DOCENTE IGSM
}


def _cuota_no_aplica(cids, socios: dict | None = None) -> dict[int, str]:
    """{id_cliente: detalle} de los socios cuya cuota social es voluntaria.

    Dos caminos, porque cubren casos distintos:

    - Por **motivo** de habilitación (master, contrato, categoría): sólo aplica a
      quien la whitelist tiene como habilitado.
    - Por **categoría**: aplica también al rechazado. Un concesionario sin
      contrato vigente no tiene fila habilitada, así que sin esto caía en el
      ``else`` genérico y el visor lo marcaba "Cuota Vencida" — una cuota que no
      existe. El motivo real del rechazo lo pone xSys.
    """
    from xsys.models import XsysWhitelist

    exentos = {
        w.id_cliente: (w.detalle or w.motivo or "")
        for w in XsysWhitelist.objects.filter(
            id_cliente__in=cids, habilitado=True, motivo_code__in=_MOTIVOS_SIN_CUOTA)
        .only("id_cliente", "motivo", "detalle")
    }
    for cid, socio in (socios or {}).items():
        if socio is not None and socio.id_tipo_cli in _CATEGORIAS_SIN_CUOTA:
            exentos.setdefault(cid, socio.categoria or "sin cuota social")
    return exentos


_BARRERAS_CACHE: tuple[float, set[int]] = (0.0, set())


def _accesos_barrera() -> set[int]:
    """Ids de acceso que son barreras de auto.

    Se deducen del dato, no de una lista fija: en xSys los accesos de AUTO son
    justamente los que tienen ``Flag_Ult_Cuota_Paga`` distinto de 0 (son los
    únicos que gatean por cuota).
    """
    global _BARRERAS_CACHE
    import time as _t

    from xsys.models import XsysAcceso

    # Se cachea 5 minutos: son 27 filas que no cambian nunca, y el visor pedía
    # esta lista 16 veces por segundo entre todas las pantallas.
    ahora = _t.monotonic()
    if _BARRERAS_CACHE[1] and (ahora - _BARRERAS_CACHE[0]) < 300:
        return _BARRERAS_CACHE[1]
    valor = set(
        XsysAcceso.objects.exclude(flag_ult_cuota_paga=0)
        .exclude(flag_ult_cuota_paga=None)
        .values_list("id_acceso", flat=True)
    )
    _BARRERAS_CACHE = (ahora, valor)
    return valor


def _bajas_en_revision(cids) -> set[int]:
    """Socios que pasan pese a figurar dados de baja, porque la baja está en duda.

    xSys ya los deja pasar (``CP_SCA_RegistrarAcceso`` saltea el rechazo por
    persona inactiva para los que están en ``CD_Clientes_Baja_Revision``). Acá se
    los marca para que el operador lo vea: la persona entra, pero su ficha está
    mal y hay que mandarla a Socios.
    """
    ids = {int(c) for c in cids if c}
    if not ids:
        return set()
    return set(
        XsysBajaRevision.objects
        .filter(id_cliente__in=ids, en_revision=True)
        .values_list("id_cliente", flat=True)
    )


def _ingresos_hoy_por_habilitacion(eventos, barreras: set[int]) -> dict[tuple, int]:
    """Cuántas veces ingresó hoy cada socio por barrera CON LA MISMA habilitación.

    La habilitación es lo que xSys dejó escrito en ``Observacion`` (p. ej.
    "Habilit. por Produc. Comprado CUOTA SOCIAL"), que es el contrato/producto
    con el que se le abrió. Se cuenta por (socio, habilitación) para que dos
    contratos distintos lleven cuentas separadas.
    """
    claves = {
        (e.id_cliente, (e.observacion or "").strip())
        for e in eventos
        if e.id_cliente and e.id_acceso in barreras
    }
    if not claves:
        return {}
    hoy = timezone.localdate()
    socios = {c[0] for c in claves}
    cuenta: dict[tuple, int] = {}
    filas = (
        ExternalAccessLogEntry.objects
        .filter(id_cliente__in=socios, id_acceso__in=barreras,
                tipo="E", resultado="S", fecha__date=hoy)
        .values_list("id_cliente", "observacion")
    )
    for cid, obs in filas:
        clave = (cid, (obs or "").strip())
        cuenta[clave] = cuenta.get(clave, 0) + 1
    return {c: cuenta.get(c, 0) for c in claves}


# Prefijos con que xSys arma la observación cuando habilita por producto. Lo que
# viene después es el nombre del producto y, en las barreras, ese producto ES la
# cochera ("COCHERA OMBUES VIP Nro.17"). El más largo va primero: "por Titular"
# empieza igual que el otro y si no, nunca matchearía.
_PREFIJOS_PRODUCTO = (
    "Habilit. por Produc. Comprado por Titular ",
    "Habilit. por Produc. Comprado ",
)


# "Nro.17", "Nro. 83", "Nro.14 Bis": el separador es igual de irregular en todo
# el maestro de productos, así que se parte por ahí en vez de confiar en el
# formato.
_RE_COCHERA_NRO = re.compile(r"\bNro\.?\s*", re.IGNORECASE)


def partes_cochera(descripcion: str) -> tuple[str, str]:
    """('17', 'OMBUES VIP') a partir de 'COCHERA OMBUES VIP Nro.17'.

    El número es EL dato: es lo que el de la barrera necesita para saber a dónde
    mandar el auto, y en la descripción completa queda al final y perdido entre
    palabras que se repiten en todas ("COCHERA", "MENSUAL", "Nro."). Se devuelve
    separado para poder mostrarlo primero y grande.
    """
    txt = (descripcion or "").strip()
    if not txt:
        return "", ""
    partes = _RE_COCHERA_NRO.split(txt, 1)
    nombre = partes[0].strip()
    # El punto final es ruido de carga: "Nro.1." y "Nro. 112." existen así.
    numero = partes[1].strip().rstrip(".").strip() if len(partes) > 1 else ""
    if nombre.upper().startswith("COCHERA "):
        nombre = nombre[len("COCHERA "):].strip()
    return numero, nombre


def _cochera_de(ev) -> str:
    """Cochera con la que entró, sacada de la observación de xSys.

    No se puede usar ``mensaje_original``: ese prefiere el texto de pantalla del
    motivo, que es genérico ("Habilit. por Produc. Comprado") y justamente pierde
    el nombre del producto. La observación cruda sí lo trae.
    """
    obs = (ev.observacion or "").strip()
    for prefijo in _PREFIJOS_PRODUCTO:
        if obs.startswith(prefijo):
            return obs[len(prefijo):].strip()
    return ""


def _mensaje_no_registrado(id_tarjeta: str) -> str:
    """Mensaje para la lectura que no corresponde a ninguna persona.

    xSys dice "La Persona es inválida", que al de la puerta no le sirve: suena a
    que la persona está mal y en realidad lo que pasa es que ese documento o esa
    credencial no están cargados. Se distingue por la forma de lo leído —los
    documentos son numéricos y las credenciales, hexadecimales— y se muestra el
    número para que el operador pueda decírselo a quien está enfrente.
    """
    tag = (id_tarjeta or "").strip()
    if not tag:
        return "Lectura no reconocida"
    if tag.isdigit():
        return f"Documento no registrado: {tag}"
    return f"Credencial no registrada: {tag}"


def _evento_payload(ev: ExternalAccessLogEntry, socios: dict, fotos: set, motivos: dict, controladores: dict | None = None, avisos_por_socio: dict | None = None, contratos_por_socio: dict | None = None, barreras: set | None = None, ingresos_hoy: dict | None = None, sin_cuota: dict | None = None, en_revision: set | None = None) -> dict:
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
    # Para quien entra por su categoría la cuota social es voluntaria: marcarle
    # "Cuota Vencida" era un falso positivo (su deuda se ve en los contratos).
    exento = (sin_cuota or {}).get(ev.id_cliente)
    # Sin socio no hay cuota que juzgar, y hay DOS motivos distintos para que
    # falte, que no significan lo mismo:
    #   - id_cliente = 0: xSys no reconoció lo que se leyó. No existe esa
    #     persona; lo que corresponde es decir que el documento/credencial no
    #     está registrado.
    #   - id_cliente > 0 pero no está en el espejo: xSys sí la conoce (p. ej. un
    #     socio dado de baja, que el espejo no replica). Ahí el motivo de xSys
    #     —"PERSONA DESACT. ..."— es exacto y se muestra tal cual.
    # En los dos casos "Cuota Vencida" es inventarle un motivo que no existe.
    no_registrado = not ev.id_cliente
    sin_identificar = socio is None
    al_dia = (True if (exento or sin_identificar)
              else cuota_al_dia(socio.ult_cuota_paga))
    # El rechazo por QR manda sobre la cuota: el socio puede estar impecable y
    # aun así no entrar porque el código de la app se le venció. El texto exacto
    # ("QR vencido: generá uno nuevo en la app y pasalo enseguida") lo pone xSys.
    rechazo_qr = (not permitido) and ev.id_cd_motivo in _MOTIVOS_QR
    if rechazo_qr:
        estado = "no"
        mensaje = mensaje_original or "QR vencido: generá uno nuevo en la app"
    elif permitido and not al_dia:
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
        # Al que no paga cuota, el genérico no le dice nada al de la puerta: el
        # motivo de xSys ("contrato vencido", "no cumple ninguna condición") sí.
        if no_registrado:
            mensaje = _mensaje_no_registrado(ev.id_tarjeta)
        elif (exento or sin_identificar) and mensaje_original:
            mensaje = mensaje_original
        else:
            mensaje = "Chequear Oficina de Socios"

    # Figura dado de baja, pero la baja está en revisión y xSys lo dejó pasar.
    # Prevalece sobre "Acceso Concedido": la persona entra, pero su ficha está
    # mal y alguien tiene que mandarla a Socios. No pisa un rechazo: si por otro
    # motivo no pasó, ese motivo es el que importa.
    revision = bool(en_revision and ev.id_cliente in en_revision)
    if revision and permitido:
        estado = "anomalia"
        mensaje = "Pasa · Figura dado de baja por error"

    # La credencial ya estaba reservada en otro molinete: prevalece sobre
    # cualquier otro mensaje, porque es el motivo por el que no debe pasar.
    if ev.conflicto_molinete:
        estado = "no"
        mensaje = f"Paso pendiente Molinete: {ev.conflicto_molinete}"

    foto_url = f"/api/xsys/socios/{ev.id_cliente}/foto/" if tiene_foto else None
    cochera = _cochera_de(ev) if (barreras and ev.id_acceso in barreras) else ""
    cochera_nro, cochera_nombre = partes_cochera(cochera)
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
        "avisos": (avisos_por_socio or {}).get(ev.id_cliente) or [],
        "contratos": (contratos_por_socio or {}).get(ev.id_cliente) or [],
        "conflicto_molinete": ev.conflicto_molinete or "",
        # No vacío: la cuota social de este socio es voluntaria (y por qué).
        "cuota_voluntaria": exento or "",
        # Figura dado de baja por el proceso masivo del 28/08/2026, todavía sin
        # revisar. Entra igual; hay que mandarlo a Socios.
        "baja_en_revision": revision,
        "es_barrera": bool(barreras and ev.id_acceso in barreras),
        # En una barrera el producto que habilita es la cochera. Se manda sólo
        # ahí: en un molinete peatonal el producto es "CUOTA SOCIAL" y no aporta.
        # El número va aparte porque es el dato que se mira: la descripción
        # completa lo esconde al final.
        "cochera": cochera,
        "cochera_nro": cochera_nro,
        "cochera_nombre": cochera_nombre,
        # Veces que ingresó hoy por barrera con ESTA misma habilitación.
        "ingresos_hoy": (ingresos_hoy or {}).get(
            (ev.id_cliente, (ev.observacion or "").strip())),
    }


def _facial_evento_payload(ev: BiostarAccessEvent, socios: dict, fotos: set, avisos_por_socio: dict | None = None, contratos_por_socio: dict | None = None, sin_cuota: dict | None = None, en_revision: set | None = None) -> dict:
    """Payload de un acceso facial BioStar, con la MISMA forma que _evento_payload
    para poder fusionarlo en la misma columna del visor. La identidad del equipo
    (``facial_equipo``) es el dato que xSys no tiene: viene del log de BioStar."""
    socio = socios.get(ev.id_cliente)
    tiene_foto = ev.id_cliente in fotos
    permitido = bool(ev.permitido)
    exento = (sin_cuota or {}).get(ev.id_cliente)
    # Igual que en los eventos de xSys: sin socio identificado no hay cuota que
    # juzgar. Acá pasa con los AUTH_FAILED_TIMEOUT, que el equipo registra sin
    # usuario porque no llegó a reconocer a nadie.
    sin_identificar = socio is None
    al_dia = (True if (exento or sin_identificar)
              else cuota_al_dia(socio.ult_cuota_paga))
    if permitido and not al_dia:
        estado, mensaje = "anomalia", "Acceso Concedido · Cuota Vencida"
    elif not permitido and sin_identificar:
        # El equipo no llegó a reconocer a nadie (AUTH_FAILED_TIMEOUT): no negó a
        # una persona, no la vio. "Acceso Denegado" hacía pensar que el socio
        # estaba mal cuando lo que falla es la lectura del rostro.
        estado, mensaje = "no", "Rostro no reconocido"
    elif not permitido:
        estado, mensaje = "no", "Acceso Denegado"
    elif not al_dia:
        estado, mensaje = "no", "Cuota Vencida"
    else:
        estado, mensaje = "ok", "Acceso Concedido"
    # Igual que en los eventos de xSys: entra, pero la ficha está mal.
    revision = bool(en_revision and ev.id_cliente in en_revision)
    if revision and permitido:
        estado, mensaje = "anomalia", "Pasa · Figura dado de baja por error"
    if ev.conflicto_molinete:
        estado = "no"
        mensaje = f"Paso pendiente Molinete: {ev.conflicto_molinete}"
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
        "avisos": (avisos_por_socio or {}).get(ev.id_cliente) or [],
        "contratos": (contratos_por_socio or {}).get(ev.id_cliente) or [],
        "baja_en_revision": revision,
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


def _firma_estado(cols_def, dia) -> str:
    """Firma barata del estado de una puerta para un día.

    Cambia cuando entra un movimiento nuevo (por cualquiera de las dos fuentes),
    cuando se deja un aviso a un socio o cuando cambia el visor. Incluye además
    una ventana de 30 s para que los datos que no dependen de un evento —deuda,
    cuota, contratos— no queden congelados indefinidamente en pantalla.
    """
    import time as _t

    from django.db.models import Max

    ctrls = [c for cd in cols_def for c in cd["controladores"]]
    devs = [d for cd in cols_def for d in (cd.get("biostar_devices") or [])]

    max_cdes = (
        ExternalAccessLogEntry.objects.filter(id_controlador__in=ctrls, tipo="E", fecha__date=dia)
        .aggregate(m=Max("external_id"))["m"] if ctrls else None
    )
    max_bio = (
        BiostarAccessEvent.objects.filter(device_id__in=devs, synced_at__date=dia)
        .aggregate(m=Max("id"))["m"] if devs else None
    )
    max_aviso = SocioAviso.objects.aggregate(m=Max("id"))["m"]
    ventana = int(_t.time() // 30)
    return '"%s-%s-%s-%s-%s-%s"' % (
        _version_visor(), dia.isoformat(), max_cdes or 0, max_bio or 0, max_aviso or 0, ventana)


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
                "visor_version": _version_visor(),
            })

        cols_def = _columnas_de_puerta(door)

        # Día a mostrar. Por defecto hoy; con ?fecha=YYYY-MM-DD se puede navegar
        # hacia atrás hasta donde llega la retención local (CD_ES y los eventos
        # faciales se purgan a los 7 días, así que más atrás sólo habría columnas
        # vacías). Se acota acá y no en el navegador para que no dependa de lo que
        # mande la pantalla.
        dia, minimo, hoy = _dia_pedido(request)

        # --- Respuesta condicional -------------------------------------------
        # Las pantallas sondean 2 veces por segundo y casi siempre no pasó nada:
        # armar la respuesta entera (21 queries) para devolver lo mismo era lo que
        # saturaba los workers y dejaba a las fotos sin atender. Se calcula una
        # firma barata (2 agregados) y, si no cambió, se contesta 304: el
        # navegador reusa el cuerpo que ya tiene y el visor ni se entera.
        firma = _firma_estado(cols_def, dia)
        if request.headers.get("If-None-Match") == firma:
            resp = Response(status=status.HTTP_304_NOT_MODIFIED)
            resp["ETag"] = firma
            resp["Cache-Control"] = "no-cache"
            return resp

        # Por columna: eventos xSys (por controlador) + accesos faciales BioStar
        # (por device). Los faciales son la única fuente con identidad por-equipo.
        xsys_por_col = []
        facial_por_col = []
        for cd in cols_def:
            ctrls = cd["controladores"]
            xs = list(
                ExternalAccessLogEntry.objects
                .filter(id_controlador__in=ctrls, tipo="E", fecha__date=dia)
                .order_by("-external_id")[: HISTORIAL_LEN + 1]
            ) if ctrls else []
            devs = cd.get("biostar_devices") or []
            # Se filtra/ordena por hora de INGESTA (synced_at ≈ tiempo real del
            # poll, ~1-2s), NO por la hora reportada por BioStar: su server_datetime
            # viene ~3h atrasado y algunos equipos driftean el reloj, lo que hacía
            # que los pasos faciales aparecieran tarde (o cayeran en otro día).
            fx = list(
                BiostarAccessEvent.objects
                .filter(device_id__in=devs, id_cliente__isnull=False, synced_at__date=dia)
                .order_by("-synced_at")[: HISTORIAL_LEN + 1]
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
        # Socios que no están en el espejo local (p.ej. inactivos): traerlos en
        # segundo plano desde xSys para resolver el nombre en el próximo refresco.
        faltantes_socio = cids - set(socios)
        if faltantes_socio:
            from xsys.services import socio_fetch

            socio_fetch.request_many(faltantes_socio)
        fotos = set(XsysSocioFoto.objects.filter(id_cliente__in=cids).values_list("id_cliente", flat=True))
        # Fallback async: los socios sin foto local se buscan en xSys en segundo
        # plano; la foto aparecerá en un refresco posterior.
        foto_fetch.request_many(cids - fotos)
        motivos = {m.id_cd_motivo: m for m in XsysMotivo.objects.filter(pk__in=mids)}
        ctrls = {c.id_controlador: c for c in XsysControlador.objects.filter(pk__in=ctrl_ids)}
        # Avisos locales por socio (los que se dejan en /diag-facial): se muestran
        # en el visor cuando ese socio pasa.
        avisos_por_socio: dict = {}
        for a in SocioAviso.objects.filter(id_cliente__in=cids).order_by("-created_at"):
            avisos_por_socio.setdefault(a.id_cliente, []).append(a.texto)
        # Contratos vigentes + último pago de cada uno (una sola query al espejo).
        contratos_por_socio = contratos_svc.resumen_por_socio(cids)
        # Barreras: se muestra cuántas veces entró hoy con la misma habilitación.
        sin_cuota = _cuota_no_aplica(cids, socios)
        en_revision = _bajas_en_revision(cids)
        barreras = _accesos_barrera()
        todos_xsys = [e for col in xsys_por_col for e in col]
        # Sólo se calcula si esta puerta realmente tiene accesos de barrera: en
        # los molinetes peatonales (la mayoría) era una query por poll para nada.
        ingresos_hoy = (
            _ingresos_hoy_por_habilitacion(todos_xsys, barreras)
            if any(e.id_acceso in barreras for e in todos_xsys) else {}
        )

        columnas = []
        for cd, xs, fx in zip(cols_def, xsys_por_col, facial_por_col):
            # (fecha, payload) para poder ordenar la mezcla por tiempo (desc).
            items = [(e.fecha, _evento_payload(e, socios, fotos, motivos, ctrls, avisos_por_socio, contratos_por_socio, barreras, ingresos_hoy, sin_cuota, en_revision)) for e in xs]
            # Los faciales se ubican en la línea de tiempo por su hora de ingesta
            # (real), no por la hora de BioStar (atrasada). Los xSys sí por fecha.
            items += [(e.synced_at, _facial_evento_payload(e, socios, fotos, avisos_por_socio, contratos_por_socio, sin_cuota, en_revision)) for e in fx]
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
        respuesta = Response({
            "configurada": True,
            "ip": pantalla.ip,
            "nombre": pantalla.nombre or door.name,
            "puerta": {"id": door.id, "nombre": door.name, "xsys_id_acceso": door.xsys_id_acceso},
            "columnas": columnas,
            "visor_version": _version_visor(),
            # Navegación por día: la pantalla usa esto para pintar la fecha y
            # habilitar/deshabilitar las flechas sin conocer la retención.
            "dia": dia.isoformat(),
            "dia_es_hoy": dia == hoy,
            "dia_min": minimo.isoformat(),
            "dia_max": hoy.isoformat(),
        })
        respuesta["ETag"] = firma
        respuesta["Cache-Control"] = "no-cache"   # revalidar siempre, pero barato
        return respuesta


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

        # El buscador mira el MISMO día que está mostrando el visor: si no, al
        # navegar a un día pasado la búsqueda devolvería el de hoy.
        hoy, _minimo, _real = _dia_pedido(request)
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


def _ultimos_reportes_xsys(ids) -> dict:
    """{id_controlador: última Fecha (ISO) en CD_ES}, consultado EN VIVO a xSys.

    Se consulta en vivo (no el espejo local, que solo guarda ~7 días) para poder
    detectar lectores que dejaron de reportar hace mucho. Devuelve {} ante error
    o sin ids, para no romper la pantalla de config si xSys no responde.
    """
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return {}
    try:
        from xsys.services.mssql import get_config, xsys_cursor

        ph = ",".join(str(i) for i in ids)
        with xsys_cursor(get_config(None)) as cur:
            cur.execute(
                f"SELECT Id_Controlador, MAX(Fecha) FROM CD_ES "
                f"WHERE Id_Controlador IN ({ph}) GROUP BY Id_Controlador"
            )
            return {int(r[0]): (r[1].isoformat() if r[1] else None) for r in cur.fetchall()}
    except Exception:
        return {}


class ControladoresXsysAPI(_ConfigPuertasAPIView):
    """GET /api/xsys/config/controladores-xsys/?id_acceso= → catálogo de
    controladores de xSys para asignar a una puerta. Sin id_acceso: todos.
    Incluye la lista de accesos activos para poder filtrar en la UI, y la última
    vez que cada controlador reportó a xSys (para detectar lectores caídos)."""

    def get(self, request):
        qs = XsysControlador.objects.all()
        id_acceso = request.query_params.get("id_acceso")
        if id_acceso not in (None, ""):
            try:
                qs = qs.filter(id_acceso=int(id_acceso))
            except (TypeError, ValueError):
                return Response({"detail": "id_acceso inválido."}, status=status.HTTP_400_BAD_REQUEST)
        ctrls_list = list(qs.order_by("id_acceso", "descripcion"))
        ultimos = _ultimos_reportes_xsys([c.id_controlador for c in ctrls_list])
        ctrls = [
            {"id_controlador": c.id_controlador, "id_acceso": c.id_acceso,
             "descripcion": c.descripcion or f"Ctrl {c.id_controlador}",
             "tipo_cont": c.tipo_cont, "activo": c.activo, "ip": c.ip,
             "ultimo_reporte": ultimos.get(c.id_controlador)}
            for c in ctrls_list
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


# Cuántos avisos previos se muestran en el modal del visor. Son pantallas chicas
# y el operador necesita saber si ya se le dijo algo, no el historial completo
# (ese está en /avisos/).
AVISOS_EN_MODAL = 3


def _avisos_recientes(id_cliente: int, limite: int = AVISOS_EN_MODAL) -> list[dict]:
    return [
        {
            "tipo": a.tipo,
            "texto": a.texto,
            "creado_por": a.creado_por,
            "created_at": a.created_at.isoformat(),
        }
        for a in SocioAviso.objects.filter(id_cliente=id_cliente)
        .order_by("-created_at")[:limite]
    ]


def _tipos_avisados_hoy(id_cliente: int) -> list[str]:
    """Tipos de aviso que este socio ya recibió HOY.

    El día es el del club (``TIME_ZONE`` es Buenos Aires y ``USE_TZ`` está en
    True, así que ``localdate`` no arrastra el UTC del contenedor).
    """
    return list(
        SocioAviso.objects.filter(id_cliente=id_cliente, created_at__date=timezone.localdate())
        .values_list("tipo", flat=True)
        .distinct()
    )


class PantallaAvisoAPI(APIView):
    """POST /api/xsys/socios/<id_cliente>/aviso/ → deja un aviso desde el monitor.

    La API de avisos de ``access_control`` exige sesión iniciada, y el visor es un
    kiosco sin login: acá se identifica por el token de pantalla, igual que el
    resto del monitor. Solo admite los avisos de un toque (texto fijo del
    servidor), no notas libres — no hay teclado en el molinete.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, id_cliente: int):
        pantalla = _registrar_pantalla(request)
        if pantalla is None:
            return Response({"detail": "Falta el token de pantalla."},
                            status=status.HTTP_400_BAD_REQUEST)
        tipo = (request.data.get("tipo") or "").strip()
        texto = SocioAviso.TEXTOS_PREDEFINIDOS.get(tipo)
        if not texto:
            return Response(
                {"detail": f"Tipo de aviso desconocido: {tipo!r}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Un aviso por tipo y por día: el socio pasa varias veces y por varios
        # molinetes, y sin esto juntaba el mismo aviso repetido. Se valida acá y
        # no sólo en el botón porque hay una pantalla por puerta: dos operadores
        # pueden estar mirando al mismo socio a la vez.
        if SocioAviso.objects.filter(
            id_cliente=id_cliente, tipo=tipo, created_at__date=timezone.localdate()
        ).exists():
            return Response(
                {"detail": "Ya se le dejó este aviso hoy.", "code": "duplicado_hoy",
                 "avisos": _avisos_recientes(id_cliente),
                 "avisos_hoy": _tipos_avisados_hoy(id_cliente)},
                status=status.HTTP_409_CONFLICT,
            )
        # Se identifica la pantalla que lo dejó, que es lo único que se sabe del
        # operador en un kiosco sin login.
        origen = (pantalla.nombre or (pantalla.door.name if pantalla.door else "") or "monitor")
        aviso = SocioAviso.objects.create(
            id_cliente=id_cliente, tipo=tipo, texto=texto,
            creado_por=f"monitor: {origen}"[:150],
        )
        return Response(
            {"id": aviso.id, "tipo": aviso.tipo, "texto": aviso.texto,
             "created_at": aviso.created_at.isoformat(),
             "avisos": _avisos_recientes(id_cliente),
             "avisos_hoy": _tipos_avisados_hoy(id_cliente)},
            status=status.HTTP_201_CREATED,
        )


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
            "avisos": _avisos_recientes(id_cliente),
            "avisos_hoy": _tipos_avisados_hoy(id_cliente),
        })


class DiagnosticoAccesoAPI(APIView):
    """GET /api/xsys/diagnostico/?doc=… | ?id_cliente=…

    Por qué esta persona entra o no entra, con el detalle que hace falta para
    resolverlo sin abrir xSys. Consulta EN VIVO, así que exige login: muestra
    deuda y comprobantes, y además cada llamada pega contra el SQL del club.
    """

    permission_classes = [PuedeConfigPuertas]

    def get(self, request):
        doc = (request.query_params.get("doc") or "").strip()
        raw_id = (request.query_params.get("id_cliente") or "").strip()
        id_cliente = int(raw_id) if raw_id.isdigit() else None
        if not doc and not id_cliente:
            return Response(
                {"detail": "Indicá un documento (doc) o un número de socio (id_cliente)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            return Response(diagnosticar(doc=doc or None, id_cliente=id_cliente))
        except Exception as exc:  # la VPN o el SQL de xSys pueden no responder
            logger.warning("diagnostico: falló la consulta (doc=%r id=%r): %s", doc, id_cliente, exc)
            return Response(
                {"detail": f"No se pudo consultar xSys: {exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

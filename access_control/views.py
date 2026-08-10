from __future__ import annotations

import re
from datetime import timedelta

from django.shortcuts import render
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils.dateparse import parse_date

from common.roles import admin_requerido, puertas_requerido
from django.db.utils import OperationalError
from django.db.models.functions import ExtractHour, TruncDate

from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.settings import api_settings

from access_control.models.models import AccessEvent, ExternalAccessLogEntry, ParkingMovement, WhitelistEntry
from access_control.serializers import AccessEventSerializer, ExternalAccessLogEntrySerializer, WhitelistEntrySerializer

from rest_framework import status, viewsets
from people.models import Cliente

from access_control.services import ClientLookupError, MSSQLClientLookupService
from access_control.services.diag_facial import DiagFacialError, diagnosticar
from access_control.services.intelectron.api3000_console import COMMAND_CATALOG
from xsys.models import XsysAcceso, XsysControlador, XsysMotivo, XsysSocio


def _parking_quota_access_status(ult_cuota_paga):
    if not ult_cuota_paga:
        return {
            "can_enter": False,
            "access_until": None,
        }

    if timezone.is_aware(ult_cuota_paga):
        quota_date = timezone.localtime(ult_cuota_paga).date()
    else:
        quota_date = ult_cuota_paga.date()
    access_start = quota_date.replace(day=1)
    access_until = access_start + timedelta(days=60)
    can_enter = timezone.localdate() <= access_until
    return {
        "can_enter": can_enter,
        "access_until": access_until,
    }


def _parking_stay_duration_seconds(stay_duration):
    if stay_duration is None:
        return None
    return int(stay_duration.total_seconds())


@puertas_requerido
def diag_facial_console(request):
    """Diagnóstico de un socio que no puede entrar por facial.

    Busca por DNI o número de socio y muestra, en una sola pantalla, todo lo que
    hace falta para responder un reclamo: datos del socio, foto, estado en
    BioStar, si está cargado en cada equipo, eventos/errores y últimos accesos.
    Comparte la lógica con el comando ``manage.py diag_facial``.
    """

    consulta = (request.GET.get("q") or "").strip()
    modo = request.GET.get("modo") or "dni"
    try:
        dias = max(1, min(60, int(request.GET.get("dias") or 10)))
    except ValueError:
        dias = 10

    contexto = {"consulta": consulta, "modo": modo, "dias": dias,
                "reportes": [], "avisos": [], "error": None}

    if consulta:
        numeros = [t for t in re.split(r"[\s,;]+", consulta) if t]
        invalidos = [t for t in numeros if not t.isdigit()]
        if invalidos:
            contexto["error"] = "Solo números (DNI o número de socio): " + ", ".join(invalidos[:5])
        else:
            valores = [int(t) for t in numeros]
            try:
                datos = diagnosticar(
                    dnis=valores if modo == "dni" else [],
                    ids_cliente=valores if modo == "socio" else [],
                    dias=dias,
                )
                contexto["reportes"] = datos["reportes"]
                contexto["avisos"] = datos["avisos"]
                # Adjuntar a cada reporte los avisos ya guardados del socio.
                from access_control.models import SocioAviso

                for r in contexto["reportes"]:
                    cid = r["socio"]["id_cliente"]
                    r["avisos_socio"] = list(
                        SocioAviso.objects.filter(id_cliente=cid).order_by("-created_at")[:20]
                    )
            except DiagFacialError as exc:
                contexto["error"] = str(exc)

    return render(request, "access_control/diag_facial.html", contexto)


@puertas_requerido
def avisos_pendientes(request):
    """Pantalla para la oficina de Socios: avisos pendientes de notificar.

    Lista los avisos dejados en el diagnóstico de facial (``SocioAviso``), con los
    datos del socio, y permite marcarlos como notificados/resueltos.
    """
    from access_control.models import SocioAviso
    from xsys.models import XsysSocio, XsysSocioFoto
    from xsys.services import socio_fetch

    estado = request.GET.get("estado") or "pendientes"
    qs = SocioAviso.objects.all().order_by("-created_at")
    if estado != "todos":
        qs = qs.filter(resuelto=False)
    avisos = list(qs[:400])

    cids = {a.id_cliente for a in avisos}
    socios = {s.id_cliente: s for s in XsysSocio.objects.filter(pk__in=cids)}
    faltan = cids - set(socios)
    if faltan:
        socio_fetch.request_many(faltan)
    fotos = set(XsysSocioFoto.objects.filter(id_cliente__in=cids).values_list("id_cliente", flat=True))

    filas = []
    for a in avisos:
        s = socios.get(a.id_cliente)
        nombre = (f"{s.apellido}, {s.nombre}".strip(", ") or s.razon_social) if s else ""
        filas.append({
            "aviso": a,
            "nombre": nombre,
            "doc": (s.doc_nro if s else None),
            "categoria": (s.categoria if s else ""),
            "foto_url": (f"/api/xsys/socios/{a.id_cliente}/foto/?thumb=1" if a.id_cliente in fotos else None),
        })

    contexto = {
        "filas": filas,
        "estado": estado,
        "pendientes": SocioAviso.objects.filter(resuelto=False).count(),
    }
    return render(request, "access_control/avisos_pendientes.html", contexto)


@admin_requerido
def biostar_devices_console(request):
    """Consola web para ver lectores BioStar."""
    return render(request, "access_control/biostar_devices.html")


@admin_requerido
def biostar_users_console(request):
    """Consola web para ver personas BioStar."""
    return render(request, "access_control/biostar_users.html")


@admin_requerido
def external_access_console(request):
    """Consola web para ver y sincronizar movimientos externos."""
    return render(request, "access_control/external_access_console.html")



@admin_requerido
def parking_movements_console(request):
    """Consola para registrar ingresos y salidas de automóviles."""
    return render(request, "access_control/parking_movements_console.html")

@admin_requerido
def access_reports_console(request):
    """Consola visual para reportes de accesos."""
    return render(request, "access_control/access_reports_console.html")


@admin_requerido
def anses_verification_console(request):
    """Consola para verificar situación ANSES de socios de +90 años."""
    return render(request, "access_control/anses_verification_console.html")


@admin_requerido
def api3000_test_console(request):
    """Consola de pruebas de funciones del wrapper API3000."""
    return render(
        request,
        "access_control/api3000_test_console.html",
        {"command_catalog": COMMAND_CATALOG},
    )


@admin_requerido
def intelektron_admin(request):
    """Administrador de equipos Intelektron (molinetes API-3000)."""
    return render(
        request,
        "access_control/intelektron_admin.html",
        {"command_catalog": COMMAND_CATALOG},
    )


class WhitelistEntryViewSet(viewsets.ModelViewSet):
    queryset = WhitelistEntry.objects.select_related(
        "person",
        "access_point",
        "access_point__site",
        "event",
    ).all()
    serializer_class = WhitelistEntrySerializer


class AccessEventViewSet(viewsets.ModelViewSet):
    queryset = AccessEvent.objects.select_related("person", "site", "category").all()
    serializer_class = AccessEventSerializer


class ExternalAccessLogView(APIView):
    """Devuelve los últimos ingresos sincronizados localmente."""

    def get(self, request):
        limit_param = request.query_params.get("limit")
        limit_value = None
        if limit_param is not None:
            try:
                limit_value = int(limit_param)
            except ValueError:
                return Response(
                    {"detail": _("El parámetro 'limit' debe ser un número entero.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if limit_value <= 0:
                return Response(
                    {"detail": _("El parámetro 'limit' debe ser mayor que cero.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        queryset = ExternalAccessLogEntry.objects.all()
        paginator = PageNumberPagination()
        paginator.page_size = api_settings.PAGE_SIZE
        if limit_value is not None:
            paginator.page_size = limit_value
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = ExternalAccessLogEntrySerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


DIAS_REPORTE_DEFAULT = 5

# En CD_ES el resultado es 'S' (paso permitido) o 'E' (rechazado). No hay otros
# valores en uso, así que "rechazado" = todo lo que no sea 'S'.
RESULTADO_PERMITIDO = "S"


def _rango_reporte(request):
    """Rango de fechas del reporte: ?desde=&hasta= (ISO) o los últimos N días."""
    hoy = timezone.localdate()
    desde = parse_date(request.query_params.get("desde") or "") or hoy - timedelta(days=DIAS_REPORTE_DEFAULT - 1)
    hasta = parse_date(request.query_params.get("hasta") or "") or hoy
    if desde > hasta:
        desde, hasta = hasta, desde
    return desde, hasta


def _accesos_qs(request):
    """Accesos del período, con los filtros opcionales de la consola aplicados.

    Se usa ``ExternalAccessLogEntry`` (espejo local de CD_ES), que es donde están
    los accesos reales; ``AccessEvent`` quedó sin uso.
    """
    desde, hasta = _rango_reporte(request)
    qs = ExternalAccessLogEntry.objects.filter(fecha__date__range=(desde, hasta))
    acceso = request.query_params.get("acceso")
    if acceso:
        qs = qs.filter(id_acceso=acceso)
    categoria = (request.query_params.get("categoria") or "").strip()
    if categoria:
        socios = XsysSocio.objects.filter(categoria=categoria).values("id_cliente")
        qs = qs.filter(id_cliente__in=Subquery(socios))
    return qs, desde, hasta


def _con_categoria(qs):
    """Anota la categoría del socio resolviéndola en SQL (evita traer los ids)."""
    return qs.annotate(
        categoria=Subquery(
            XsysSocio.objects.filter(id_cliente=OuterRef("id_cliente")).values("categoria")[:1]
        )
    )


def _totales(qs):
    total = qs.count()
    permitidos = qs.filter(resultado=RESULTADO_PERMITIDO).count()
    return {"total": total, "permitidos": permitidos, "rechazados": total - permitidos}


class AccessByCategoryReportView(APIView):
    """Accesos agrupados por categoría de socio, más los totales del período."""

    def get(self, request):
        qs, desde, hasta = _accesos_qs(request)
        by_category = list(
            _con_categoria(qs).values("categoria").annotate(total=Count("id")).order_by("-total")[:40]
        )
        totals_by_day = list(
            qs.annotate(day=TruncDate("fecha")).values("day").annotate(total=Count("id")).order_by("day")
        )
        return Response({
            "desde": desde, "hasta": hasta,
            **_totales(qs),
            "by_category": by_category,
            "totals_by_day": totals_by_day,
        })


class AccessBySiteReportView(APIView):
    """Accesos por acceso/sede de xSys y, dentro, por molinete."""

    def get(self, request):
        qs, desde, hasta = _accesos_qs(request)
        by_site = list(
            qs.annotate(
                acceso=Subquery(
                    XsysAcceso.objects.filter(id_acceso=OuterRef("id_acceso")).values("descripcion")[:1]
                )
            )
            .values("id_acceso", "acceso")
            .annotate(
                total=Count("id"),
                permitidos=Count("id", filter=Q(resultado=RESULTADO_PERMITIDO)),
            )
            .order_by("-total")
        )
        by_controlador = list(
            qs.annotate(
                molinete=Subquery(
                    XsysControlador.objects.filter(id_controlador=OuterRef("id_controlador"))
                    .values("descripcion")[:1]
                )
            )
            .values("id_controlador", "molinete")
            .annotate(total=Count("id"))
            .order_by("-total")[:30]
        )
        for row in by_site:
            row["rechazados"] = row["total"] - row["permitidos"]
        return Response({"desde": desde, "hasta": hasta, "sites": by_site, "molinetes": by_controlador})


class AccessDenialsReportView(APIView):
    """Rechazos del período agrupados por motivo (por qué no pudo pasar)."""

    def get(self, request):
        qs, desde, hasta = _accesos_qs(request)
        rechazos = qs.exclude(resultado=RESULTADO_PERMITIDO)
        motivos = list(
            rechazos.annotate(
                motivo=Subquery(
                    XsysMotivo.objects.filter(id_cd_motivo=OuterRef("id_cd_motivo"))
                    .values("descripcion")[:1]
                )
            )
            .values("id_cd_motivo", "motivo")
            .annotate(total=Count("id"))
            .order_by("-total")[:20]
        )
        return Response({"desde": desde, "hasta": hasta, "total_rechazos": rechazos.count(), "motivos": motivos})


class AccessHeatmapReportView(APIView):
    """Distribución día / hora de los accesos del período."""

    def get(self, request):
        qs, desde, hasta = _accesos_qs(request)
        matrix = list(
            qs.annotate(day=TruncDate("fecha"), hour=ExtractHour("fecha"))
            .values("day", "hour")
            .annotate(total=Count("id"))
            .order_by("day", "hour")
        )
        return Response({"desde": desde, "hasta": hasta, "heatmap": matrix})


class ParkingClienteLookupView(APIView):
    """Busca la última cuota paga por DNI en tabla de clientes."""

    def get(self, request):
        dni = request.query_params.get("dni")
        if not dni:
            return Response({"detail": "El parámetro 'dni' es obligatorio."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            dni_value = int(dni)
        except (TypeError, ValueError):
            return Response({"detail": "El parámetro 'dni' debe ser numérico."}, status=status.HTTP_400_BAD_REQUEST)

        cliente = Cliente.objects.filter(doc_nro=dni_value).values("id_cliente", "doc_nro", "ult_cuota_paga").first()
        source = "local"

        if not cliente:
            try:
                cliente = MSSQLClientLookupService().fetch_by_dni(dni_value)
                source = "mssql"
            except ClientLookupError:
                cliente = None

        if not cliente:
            return Response({"found": False, "dni": dni_value, "ult_cuota_paga": None})

        ult_cuota_paga = cliente.get("ult_cuota_paga")
        access_status = _parking_quota_access_status(ult_cuota_paga)
        return Response({
            "found": True,
            "source": source,
            "dni": cliente.get("doc_nro"),
            "id_cliente": cliente.get("id_cliente"),
            "ult_cuota_paga": ult_cuota_paga.isoformat() if ult_cuota_paga else None,
            "can_enter": access_status["can_enter"],
            "access_until": access_status["access_until"].isoformat() if access_status["access_until"] else None,
        })


class ParkingMovementView(APIView):
    """Registro y consulta de movimientos de estacionamiento."""

    def get(self, request):
        try:
            items = list(ParkingMovement.objects.all().values("id", "dni", "patente", "movement_type", "ult_cuota_paga", "created_at")[:50])
        except OperationalError:
            return Response(
                {
                    "detail": (
                        "La tabla de movimientos de estacionamiento no está disponible. "
                        "Ejecute las migraciones pendientes."
                    )
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        items = list(
            ParkingMovement.objects.all().values(
                "id",
                "dni",
                "patente",
                "movement_type",
                "ult_cuota_paga",
                "created_at",
                "exit_at",
                "stay_duration",
            )[:100]
        )
        payload = []
        for item in items:
            payload.append({
                "id": item["id"],
                "dni": item["dni"],
                "patente": item["patente"],
                "movement_type": item["movement_type"],
                "ult_cuota_paga": item["ult_cuota_paga"].isoformat() if item["ult_cuota_paga"] else None,
                "created_at": item["created_at"].isoformat() if item["created_at"] else None,
                "exit_at": item["exit_at"].isoformat() if item["exit_at"] else None,
                "stay_duration_seconds": _parking_stay_duration_seconds(item["stay_duration"]),
            })
        return Response(payload)

    def post(self, request):
        dni = request.data.get("dni")
        patente = (request.data.get("patente") or "").strip().upper()
        movement_type = request.data.get("movement_type")

        if not dni or not patente or not movement_type:
            return Response({"detail": "Los campos dni, patente y movement_type son obligatorios."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            dni_value = int(dni)
        except (TypeError, ValueError):
            return Response({"detail": "El campo dni debe ser numérico."}, status=status.HTTP_400_BAD_REQUEST)

        valid_types = {choice for choice, _ in ParkingMovement.MovementType.choices}
        if movement_type not in valid_types:
            return Response({"detail": "movement_type debe ser 'entry' o 'exit'."}, status=status.HTTP_400_BAD_REQUEST)

        cliente = Cliente.objects.filter(doc_nro=dni_value).values("ult_cuota_paga").first()
        try:
            movement = ParkingMovement.objects.create(
                dni=dni_value,
                patente=patente,
                movement_type=movement_type,
                ult_cuota_paga=cliente.get("ult_cuota_paga") if cliente else None,
            )
        except OperationalError:
            return Response(
                {
                    "detail": (
                        "La tabla de movimientos de estacionamiento no está disponible. "
                        "Ejecute las migraciones pendientes."
                    )
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "id": movement.id,
                "dni": movement.dni,
                "patente": movement.patente,
                "movement_type": movement.movement_type,
                "ult_cuota_paga": movement.ult_cuota_paga.isoformat() if movement.ult_cuota_paga else None,
                "created_at": movement.created_at.isoformat(),
                "exit_at": movement.exit_at.isoformat() if movement.exit_at else None,
                "stay_duration_seconds": _parking_stay_duration_seconds(movement.stay_duration),
            },
            status=status.HTTP_201_CREATED,
        )


class ParkingMovementMarkExitView(APIView):
    """Marca la salida de un ingreso y calcula permanencia."""

    def post(self, request, movement_id):
        movement = ParkingMovement.objects.filter(id=movement_id).first()
        if not movement:
            return Response({"detail": "Movimiento no encontrado."}, status=status.HTTP_404_NOT_FOUND)

        if movement.movement_type != ParkingMovement.MovementType.ENTRY:
            return Response({"detail": "Solo se puede marcar salida para ingresos."}, status=status.HTTP_400_BAD_REQUEST)

        if movement.exit_at:
            return Response({"detail": "Este ingreso ya tiene salida registrada."}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        movement.exit_at = now
        movement.stay_duration = now - movement.created_at
        movement.save(update_fields=["exit_at", "stay_duration"])

        return Response({
            "id": movement.id,
            "dni": movement.dni,
            "patente": movement.patente,
            "movement_type": movement.movement_type,
            "created_at": movement.created_at.isoformat() if movement.created_at else None,
            "exit_at": movement.exit_at.isoformat() if movement.exit_at else None,
            "stay_duration_seconds": _parking_stay_duration_seconds(movement.stay_duration),
        })


@admin_requerido
def pollers_dashboard(request):
    """Panel de salud: estado de los pollers, datos del espejo y últimos movimientos."""
    from access_control.models import (
        BiostarAccessEvent,
        BiostarPollState,
        ExternalAccessLogEntry,
        IntelektronEvent,
    )
    from xsys.models import (
        SyncState,
        XsysControlador,
        XsysSocio,
        XsysSocioFoto,
        XsysWhitelist,
    )

    now = timezone.now()

    def aw(ts):
        """Normaliza a datetime aware (o None)."""
        if not ts:
            return None
        try:
            if timezone.is_naive(ts):
                return timezone.make_aware(ts, timezone.get_current_timezone())
        except Exception:
            return None
        return ts

    def edad_min(ts):
        ts = aw(ts)
        if ts is None:
            return None
        return round((now - ts).total_seconds() / 60.0, 1)

    def estado(edad, ok=True, warn=30, err=180):
        if edad is None:
            return "sindato"
        if not ok or edad > err:
            return "error"
        if edad > warn:
            return "stale"
        return "ok"

    pollers = []

    # --- xSys sync streams (novedades / cd_es / fotos / whitelist) ---
    try:
        for s in SyncState.objects.all().order_by("stream"):
            ult = s.last_run_finished_at or s.last_datetime
            edad = edad_min(ult)
            pollers.append({
                "grupo": "xSys sync",
                "nombre": s.stream,
                "detalle": (f"last_id={s.last_id}" if s.last_id else
                            (aw(s.last_datetime).strftime("%d/%m %H:%M") if s.last_datetime else "—")),
                "ultima": ult,
                "edad_min": edad,
                "rows": s.rows_last_run,
                "error": (s.last_error or "")[:180],
                "estado": estado(edad, s.last_run_ok),
            })
    except Exception as exc:  # pragma: no cover - tabla ausente
        pollers.append({"grupo": "xSys sync", "nombre": "(no disponible)", "detalle": str(exc)[:100],
                        "ultima": None, "edad_min": None, "rows": 0, "error": str(exc)[:180], "estado": "error"})

    # --- BioStar poller ---
    try:
        bps = BiostarPollState.objects.order_by("-updated_at").first()
        edad = edad_min(bps.updated_at if bps else None)
        pollers.append({
            "grupo": "BioStar",
            "nombre": "biostar_poll",
            "detalle": (f"last_event_id={bps.last_event_id}" if bps else "sin estado"),
            "ultima": bps.updated_at if bps else None,
            "edad_min": edad,
            "rows": BiostarAccessEvent.objects.count(),
            "error": "",
            "estado": estado(edad),
        })
    except Exception as exc:  # pragma: no cover
        pollers.append({"grupo": "BioStar", "nombre": "biostar_poll", "detalle": str(exc)[:100],
                        "ultima": None, "edad_min": None, "rows": 0, "error": str(exc)[:180], "estado": "error"})

    # --- Intelektron listener (sin state model: se infiere del último evento) ---
    try:
        last = IntelektronEvent.objects.order_by("-created_at").first()
        edad = edad_min(last.created_at if last else None)
        pollers.append({
            "grupo": "Intelektron",
            "nombre": "intelektron_listener",
            "detalle": (last.device_ip if last else "sin eventos"),
            "ultima": last.created_at if last else None,
            "edad_min": edad,
            "rows": IntelektronEvent.objects.count(),
            "error": "",
            "estado": estado(edad),
        })
    except Exception as exc:  # pragma: no cover
        pollers.append({"grupo": "Intelektron", "nombre": "intelektron_listener", "detalle": str(exc)[:100],
                        "ultima": None, "edad_min": None, "rows": 0, "error": str(exc)[:180], "estado": "error"})

    # --- Movimientos externos (CD_ES) ---
    try:
        last = ExternalAccessLogEntry.objects.order_by("-fecha").first()
        edad = edad_min(last.fecha if last else None)
        pollers.append({
            "grupo": "Movimientos externos",
            "nombre": "sync_external_access_logs",
            "detalle": (f"external_id={last.external_id}" if last else "sin datos"),
            "ultima": last.fecha if last else None,
            "edad_min": edad,
            "rows": ExternalAccessLogEntry.objects.count(),
            "error": "",
            "estado": estado(edad, warn=120, err=1440),
        })
    except Exception as exc:  # pragma: no cover
        pollers.append({"grupo": "Movimientos externos", "nombre": "sync_external_access_logs", "detalle": str(exc)[:100],
                        "ultima": None, "edad_min": None, "rows": 0, "error": str(exc)[:180], "estado": "error"})

    # --- Datos del espejo ---
    def _c(fn):
        try:
            return fn()
        except Exception:
            return "—"

    datos = {
        "whitelist_hab": _c(lambda: XsysWhitelist.objects.filter(habilitado=True).count()),
        "whitelist_tot": _c(lambda: XsysWhitelist.objects.count()),
        "socios": _c(lambda: XsysSocio.objects.count()),
        "controladores": _c(lambda: XsysControlador.objects.count()),
        "molinetes_ip": _c(lambda: XsysControlador.objects.filter(tipo_cont="K").exclude(ip="").count()),
        "fotos": _c(lambda: XsysSocioFoto.objects.count()),
    }

    # --- Historial de movimientos (merge de las 3 fuentes) ---
    movimientos = []
    try:
        for e in BiostarAccessEvent.objects.order_by("-fecha")[:30]:
            movimientos.append({"ts": aw(e.fecha), "origen": "Facial", "id_cliente": e.id_cliente,
                                "equipo": e.device_name or e.device_id, "evento": e.event_name,
                                "permitido": e.permitido})
    except Exception:
        pass
    try:
        for e in IntelektronEvent.objects.order_by("-created_at")[:30]:
            ev = (e.event_name or "") + (f" / {e.direction_name}" if e.direction_name else "")
            movimientos.append({"ts": aw(e.device_time or e.created_at), "origen": "Molinete",
                                "id_cliente": e.access_id, "equipo": e.device_ip, "evento": ev.strip(" /"),
                                "permitido": None})
    except Exception:
        pass
    try:
        for e in ExternalAccessLogEntry.objects.order_by("-fecha")[:30]:
            movimientos.append({"ts": aw(e.fecha), "origen": "Externo", "id_cliente": e.id_cliente,
                                "equipo": e.id_controlador, "evento": e.resultado, "permitido": None})
    except Exception:
        pass
    movimientos = sorted([m for m in movimientos if m["ts"]], key=lambda m: m["ts"], reverse=True)[:40]

    resumen = {
        "ok": sum(1 for p in pollers if p["estado"] == "ok"),
        "stale": sum(1 for p in pollers if p["estado"] == "stale"),
        "error": sum(1 for p in pollers if p["estado"] in ("error", "sindato")),
        "total": len(pollers),
    }

    contexto = {"pollers": pollers, "datos": datos, "movimientos": movimientos,
                "resumen": resumen, "ahora": now}
    return render(request, "access_control/pollers_dashboard.html", contexto)

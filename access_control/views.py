from __future__ import annotations

from datetime import timedelta
import ipaddress
import re
import subprocess

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Count
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


MAX_PING_COUNT = 5
MAX_PING_TIMEOUT_SECONDS = 10
MAX_RAW_SUMMARY_LENGTH = 280


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


@login_required
def biostar_devices_console(request):
    """Consola web para ver lectores BioStar."""
    return render(request, "access_control/biostar_devices.html")


@login_required
def biostar_users_console(request):
    """Consola web para ver personas BioStar."""
    return render(request, "access_control/biostar_users.html")


@login_required
def external_access_console(request):
    """Consola web para ver y sincronizar movimientos externos."""
    return render(request, "access_control/external_access_console.html")



@login_required
def parking_movements_console(request):
    """Consola para registrar ingresos y salidas de automóviles."""
    return render(request, "access_control/parking_movements_console.html")

@login_required
def access_reports_console(request):
    """Consola visual para reportes de accesos."""
    return render(request, "access_control/access_reports_console.html")


@login_required
def anses_verification_console(request):
    """Consola para verificar situación ANSES de socios de +90 años."""
    return render(request, "access_control/anses_verification_console.html")


@login_required
def api3000_test_console(request):
    """Consola de pruebas de conectividad para API3000."""
    return render(request, "access_control/api3000_test_console.html")


def _validate_ipv4(value: str):
    if not value:
        raise ValueError("El campo 'ip' es obligatorio.")
    try:
        candidate = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("El campo 'ip' debe ser una IP válida.") from exc
    if candidate.version != 4:
        raise ValueError("Solo se soportan direcciones IPv4.")
    return str(candidate)


def _validate_ping_params(data):
    ip = _validate_ipv4(str(data.get("ip", "")).strip())
    count = int(data.get("count", 1) or 1)
    timeout = float(data.get("timeout", 1) or 1)
    if count < 1 or count > MAX_PING_COUNT:
        raise ValueError(f"El campo 'count' debe estar entre 1 y {MAX_PING_COUNT}.")
    if timeout <= 0 or timeout > MAX_PING_TIMEOUT_SECONDS:
        raise ValueError(f"El campo 'timeout' debe estar entre 0 y {MAX_PING_TIMEOUT_SECONDS} segundos.")
    return ip, count, timeout


def _extract_latency_ms(output: str):
    avg_regex = re.search(r"=\s*\d+(?:\.\d+)?/(\d+(?:\.\d+)?)/", output)
    if avg_regex:
        return float(avg_regex.group(1))
    first_match = re.search(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", output)
    if first_match:
        return float(first_match.group(1))
    return None


def _run_safe_ping(ip: str, count: int, timeout: float):
    timeout_int = max(1, int(round(timeout)))
    cmd = ["ping", "-c", str(count), "-W", str(timeout_int), ip]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=(count * timeout_int) + 2,
        check=False,
    )
    raw_summary = (proc.stdout or proc.stderr or "")[:MAX_RAW_SUMMARY_LENGTH]
    return {
        "reachable": proc.returncode == 0,
        "latency_ms": _extract_latency_ms(proc.stdout or ""),
        "raw_summary": raw_summary,
    }


class ACSTestPingView(APIView):
    """Ejecuta ping de forma controlada para validar conectividad."""

    def post(self, request):
        try:
            ip, count, timeout = _validate_ping_params(request.data)
            result = _run_safe_ping(ip, count, timeout)
        except (ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except subprocess.TimeoutExpired:
            return Response(
                {
                    "reachable": False,
                    "latency_ms": None,
                    "raw_summary": "Ping superó el tiempo máximo de espera.",
                },
                status=status.HTTP_200_OK,
            )

        return Response(result)


class ACSTestCommandView(APIView):
    """Endpoint de prueba que revalida conectividad antes de simular envío de comando."""

    def post(self, request):
        try:
            ip, _count, timeout = _validate_ping_params(request.data)
        except (ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        command = str(request.data.get("command", "")).strip()
        if not command:
            return Response({"detail": "El campo 'command' es obligatorio."}, status=status.HTTP_400_BAD_REQUEST)

        connectivity = _run_safe_ping(ip, count=1, timeout=timeout)
        if not connectivity["reachable"]:
            return Response(
                {
                    "accepted": False,
                    "detail": "No se puede enviar el comando porque el host no respondió al ping de validación.",
                    "ping": connectivity,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "accepted": True,
                "detail": "Comando validado y aceptado para procesamiento.",
                "ip": ip,
                "timeout": timeout,
                "command": command,
                "ping": connectivity,
            }
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


class AccessByCategoryReportView(APIView):
    def get(self, request):
        site_id = request.query_params.get("site")
        if not site_id:
            return Response({"detail": "El parámetro 'site' es obligatorio."}, status=400)
        end_date = timezone.localdate()
        start_date = end_date - timezone.timedelta(days=4)

        base_qs = AccessEvent.objects.filter(site_id=site_id, occurred_at__date__range=(start_date, end_date))
        category_id = request.query_params.get("category")
        if category_id:
            base_qs = base_qs.filter(category_id=category_id)

        totals_by_day = list(
            base_qs.annotate(day=TruncDate("occurred_at")).values("day").annotate(total=Count("id")).order_by("day")
        )
        by_category = list(
            base_qs.values("category__id", "category__name").annotate(total=Count("id")).order_by("-total")
        )
        return Response({"site": int(site_id), "start_date": start_date, "end_date": end_date, "totals_by_day": totals_by_day, "by_category": by_category})


class AccessBySiteReportView(APIView):
    def get(self, request):
        end_date = timezone.localdate()
        start_date = end_date - timezone.timedelta(days=4)
        qs = AccessEvent.objects.filter(occurred_at__date__range=(start_date, end_date))
        rows = list(qs.values("site__id", "site__name").annotate(total=Count("id")).order_by("-total"))
        return Response({"start_date": start_date, "end_date": end_date, "sites": rows})


class AccessHeatmapReportView(APIView):
    def get(self, request):
        site_id = request.query_params.get("site")
        if not site_id:
            return Response({"detail": "El parámetro 'site' es obligatorio."}, status=400)
        end_date = timezone.localdate()
        start_date = end_date - timezone.timedelta(days=4)
        qs = AccessEvent.objects.filter(site_id=site_id, occurred_at__date__range=(start_date, end_date))
        category_id = request.query_params.get("category")
        if category_id:
            qs = qs.filter(category_id=category_id)
        matrix = list(
            qs.annotate(day=TruncDate("occurred_at"), hour=ExtractHour("occurred_at"))
            .values("day", "hour")
            .annotate(total=Count("id"))
            .order_by("day", "hour")
        )
        return Response({"site": int(site_id), "start_date": start_date, "end_date": end_date, "heatmap": matrix})


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

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Count
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
        if not cliente:
            return Response({"found": False, "dni": dni_value, "ult_cuota_paga": None})

        ult_cuota_paga = cliente.get("ult_cuota_paga")
        return Response({
            "found": True,
            "dni": cliente.get("doc_nro"),
            "id_cliente": cliente.get("id_cliente"),
            "ult_cuota_paga": ult_cuota_paga.isoformat() if ult_cuota_paga else None,
        })


class ParkingMovementView(APIView):
    """Registro y consulta de movimientos de estacionamiento."""

    def get(self, request):
        items = list(ParkingMovement.objects.all().values("id", "dni", "patente", "movement_type", "ult_cuota_paga", "created_at")[:50])
        payload = []
        for item in items:
            payload.append({
                "id": item["id"],
                "dni": item["dni"],
                "patente": item["patente"],
                "movement_type": item["movement_type"],
                "ult_cuota_paga": item["ult_cuota_paga"].isoformat() if item["ult_cuota_paga"] else None,
                "created_at": item["created_at"].isoformat() if item["created_at"] else None,
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
        movement = ParkingMovement.objects.create(
            dni=dni_value,
            patente=patente,
            movement_type=movement_type,
            ult_cuota_paga=cliente.get("ult_cuota_paga") if cliente else None,
        )

        return Response(
            {
                "id": movement.id,
                "dni": movement.dni,
                "patente": movement.patente,
                "movement_type": movement.movement_type,
                "ult_cuota_paga": movement.ult_cuota_paga.isoformat() if movement.ult_cuota_paga else None,
                "created_at": movement.created_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )

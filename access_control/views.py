from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from django.utils.translation import gettext_lazy as _

from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.settings import api_settings

from access_control.models.models import ExternalAccessLogEntry, WhitelistEntry
from access_control.serializers import ExternalAccessLogEntrySerializer, WhitelistEntrySerializer

from rest_framework import status, viewsets


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



class WhitelistEntryViewSet(viewsets.ModelViewSet):
    queryset = WhitelistEntry.objects.select_related(
        "person",
        "access_point",
        "access_point__site",
        "event",
    ).all()
    serializer_class = WhitelistEntrySerializer


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

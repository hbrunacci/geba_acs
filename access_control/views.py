from django.utils.translation import gettext_lazy as _
from rest_framework import status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ExternalAccessLogEntry, WhitelistEntry
from .serializers import ExternalAccessLogEntrySerializer, WhitelistEntrySerializer


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
        if limit_value is not None:
            queryset = queryset[:limit_value]

        serializer = ExternalAccessLogEntrySerializer(queryset, many=True)
        return Response(serializer.data)

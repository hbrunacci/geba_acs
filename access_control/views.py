from django.utils.translation import gettext_lazy as _
from rest_framework import status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from access_control.models.models import ExternalAccessLogEntry, WhitelistEntry
from access_control.serializers import ExternalAccessLogEntrySerializer, WhitelistEntrySerializer

from rest_framework import permissions, status, views

from access_control.models import BioStarDevice
from access_control.serializers import BioStarDeviceSerializer
from access_control.services.biostar2_client import BioStar2Client


class BioStarDeviceListView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = BioStarDevice.objects.order_by("name", "device_id")
        return Response(BioStarDeviceSerializer(qs, many=True).data)


class BioStarDeviceSyncView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        client = BioStar2Client.from_db_and_env()
        payload = client.list_devices()
        # si querés, acá podés llamar a una función reusable que haga el upsert
        # (para no duplicar lógica con el management command).
        return Response({"ok": True, "payload": payload}, status=status.HTTP_200_OK)


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

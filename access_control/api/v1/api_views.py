from __future__ import annotations

from rest_framework.response import Response
from rest_framework.views import APIView

from access_control.models.models import ExternalAccessLogEntry, WhitelistEntry
from access_control.serializers import ExternalAccessLogEntrySerializer, WhitelistEntrySerializer

from rest_framework import permissions, status, views, viewsets

from access_control.models import BioStarDevice, BioStarUser
from access_control.serializers import BioStarDeviceSerializer, BioStarUserSerializer

from access_control.services.biostar2_client import BioStar2Client

from django.core.management import call_command



class BioStarDeviceListAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = BioStarDevice.objects.order_by("name", "device_id")
        return Response(BioStarDeviceSerializer(qs, many=True).data)


class BioStarDeviceSyncAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        call_command("biostar_sync_devices")
        return Response({"ok": True}, status=status.HTTP_200_OK)


class BioStarUserListAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = BioStarUser.objects.order_by("name", "user_id")
        return Response(BioStarUserSerializer(qs, many=True).data)


class BioStarUserSyncAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        call_command("biostar_sync_users")
        return Response({"ok": True}, status=status.HTTP_200_OK)


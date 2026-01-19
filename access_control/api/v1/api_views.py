from __future__ import annotations

import asyncio

from rest_framework.response import Response
from rest_framework.views import APIView

from access_control.models.models import ExternalAccessLogEntry, WhitelistEntry
from access_control.serializers import ExternalAccessLogEntrySerializer, WhitelistEntrySerializer

from rest_framework import permissions, status, views, viewsets

from access_control.models import BioStarDevice, BioStarUser
from access_control.serializers import BioStarDeviceSerializer, BioStarUserSerializer

from access_control.services.biostar2_client import BioStar2Client

from access_control.services import ExternalAccessLogError, ExternalAccessLogSynchronizer

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


class BioStarDeviceUsersAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, device_id: int):
        client = BioStar2Client.from_db_and_env()
        payload = client.list_device_users(device_id, limit=1, offset=0)
        return Response(payload, status=status.HTTP_200_OK)


class BioStarUserSearchAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        search_text = (request.data.get("search_text") or "").strip()
        if not search_text:
            return Response(
                {"detail": "El parámetro 'search_text' es obligatorio."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user_group_id = str(request.data.get("user_group_id") or "1")
        limit = int(request.data.get("limit") or 50)
        offset = int(request.data.get("offset") or 0)
        order_by = request.data.get("order_by") or "user_id:false"
        client = BioStar2Client.from_db_and_env()
        payload = client.search_users_v2(
            search_text=search_text,
            user_group_id=user_group_id,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )
        return Response(payload, status=status.HTTP_200_OK)


class ExternalAccessLogSyncAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        limit_value = None
        limit_param = request.data.get("limit")
        if limit_param is not None:
            try:
                limit_value = int(limit_param)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "El parámetro 'limit' debe ser un número entero."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if limit_value <= 0:
                return Response(
                    {"detail": "El parámetro 'limit' debe ser mayor que cero."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        synchronizer = ExternalAccessLogSynchronizer(limit=limit_value)
        try:
            synced = asyncio.run(synchronizer.sync_once())
        except ExternalAccessLogError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response(
                {"detail": "Error inesperado al sincronizar movimientos externos."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({"synced": synced}, status=status.HTTP_200_OK)

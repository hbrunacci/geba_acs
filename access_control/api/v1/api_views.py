from __future__ import annotations

import asyncio

from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.settings import api_settings

from access_control.models.models import ExternalAccessLogEntry, WhitelistEntry
from access_control.serializers import (
    ExternalAccessLogEntrySerializer,
    WhitelistBatchCreateSerializer,
    WhitelistEntrySerializer,
)

from rest_framework import permissions, status, views

from access_control.models import BioStarDevice, BioStarUser
from institutions.models import AccessPoint, Event
from people.models import Person, PersonType
from access_control.serializers import BioStarDeviceSerializer, BioStarUserSerializer

from access_control.services.biostar2_client import BioStar2Client

from access_control.services import ExternalAccessLogError, ExternalAccessLogSynchronizer

from django.core.management import call_command



class BioStarDeviceListAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get(self, request):
        qs = BioStarDevice.objects.order_by("name", "device_id")
        paginator = PageNumberPagination()
        paginator.page_size = api_settings.PAGE_SIZE
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = BioStarDeviceSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class BioStarDeviceSyncAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        call_command("biostar_sync_devices")
        return Response({"ok": True}, status=status.HTTP_200_OK)


class BioStarUserListAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get(self, request):
        qs = BioStarUser.objects.order_by("name", "user_id")
        paginator = PageNumberPagination()
        paginator.page_size = api_settings.PAGE_SIZE
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = BioStarUserSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class BioStarUserSyncAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        call_command("biostar_sync_users")
        return Response({"ok": True}, status=status.HTTP_200_OK)


class BioStarDeviceUsersAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, device_id: int):
        client = BioStar2Client.from_db_and_env()
        document = (request.query_params.get("document") or "").strip()
        limit_param = request.query_params.get("limit")
        offset_param = request.query_params.get("offset")
        try:
            limit = int(limit_param) if limit_param is not None else 1
        except (TypeError, ValueError):
            return Response(
                {"detail": "El parámetro 'limit' debe ser numérico."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            offset = int(offset_param) if offset_param is not None else 0
        except (TypeError, ValueError):
            return Response(
                {"detail": "El parámetro 'offset' debe ser numérico."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        def normalize_doc(value: object) -> str:
            if value is None:
                return ""
            raw = str(value).strip()
            digits = "".join(ch for ch in raw if ch.isdigit())
            return digits if digits else raw.lower()

        def extract_collection(payload: dict) -> dict:
            return (
                payload.get("DeviceUserCollection")
                or payload.get("device_user_collection")
                or payload.get("UserCollection")
                or payload.get("user_collection")
                or {}
            )

        def match_document(row: dict, doc_value: str) -> bool:
            normalized = normalize_doc(doc_value)
            if not normalized:
                return False
            for key in ("user_unique_id", "user_unique_id_str", "user_id", "id", "name"):
                candidate = row.get(key)
                if candidate is None:
                    continue
                if normalize_doc(candidate) == normalized:
                    return True
            return False

        if not document:
            payload = client.list_device_users(device_id, limit=limit, offset=offset)
            return Response(payload, status=status.HTTP_200_OK)

        limit = 200 if limit <= 0 else limit
        offset = 0 if offset < 0 else offset
        matched = None
        total = None
        scanned = 0
        while True:
            payload = client.list_device_users(device_id, limit=limit, offset=offset)
            collection = extract_collection(payload)
            rows = collection.get("rows") or []
            scanned += len(rows)
            if total is None:
                total = collection.get("total")
            for row in rows:
                if match_document(row, document):
                    matched = row
                    break
            if matched or not rows:
                break
            if total is not None and offset + limit >= total:
                break
            offset += limit

        return Response(
            {
                "device_id": device_id,
                "document": document,
                "total": total,
                "scanned": scanned,
                "match": matched,
            },
            status=status.HTTP_200_OK,
        )


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


class WhitelistBatchCreateAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = WhitelistBatchCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        access_point_ids = data.get("access_point_ids")
        site_id = data.get("site_id")
        event_id = data.get("event_id")
        is_allowed = data.get("is_allowed", True)
        valid_from = data.get("valid_from")
        valid_until = data.get("valid_until")
        preview = data.get("preview", False)

        event = None
        if event_id is not None:
            event = Event.objects.filter(id=event_id).first()
            if not event:
                return Response(
                    {"detail": "El evento indicado no existe."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        access_points = AccessPoint.objects.select_related("site")
        if access_point_ids:
            access_points = access_points.filter(id__in=access_point_ids)
        else:
            access_points = access_points.filter(site_id=site_id)
        if event:
            access_points = access_points.filter(site_id=event.site_id)
        access_points = list(access_points)

        if not access_points:
            return Response(
                {"detail": "No se encontraron accesos para los filtros enviados."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        persons = Person.objects.all()
        person_types = data.get("person_type")
        if person_types:
            persons = persons.filter(person_type__in=person_types)
        guest_types = data.get("guest_type")
        if guest_types:
            persons = persons.filter(guest_type__in=guest_types)
        if "is_active" in data:
            persons = persons.filter(is_active=data["is_active"])

        if event:
            event_filter = Q()
            if event.allowed_person_types:
                event_filter |= Q(person_type__in=event.allowed_person_types)
            if event.allowed_guest_types:
                event_filter |= Q(
                    person_type=PersonType.GUEST,
                    guest_type__in=event.allowed_guest_types,
                )
            if event_filter:
                persons = persons.filter(event_filter)

        persons = list(persons)

        if preview:
            return Response(
                {
                    "preview": True,
                    "people": [
                        {
                            "id": person.id,
                            "first_name": person.first_name,
                            "last_name": person.last_name,
                            "dni": person.dni,
                            "person_type": person.person_type,
                            "guest_type": person.guest_type,
                            "is_active": person.is_active,
                        }
                        for person in persons
                    ],
                },
                status=status.HTTP_200_OK,
            )

        now = timezone.now()
        created_entries = []
        updated_entries = []
        created_count = 0
        updated_count = 0

        try:
            with transaction.atomic():
                for person in persons:
                    for access_point in access_points:
                        entry = WhitelistEntry.objects.filter(
                            person=person,
                            access_point=access_point,
                            event_id=event.id if event else None,
                        ).first()
                        if entry:
                            entry.is_allowed = is_allowed
                            entry.valid_from = valid_from
                            entry.valid_until = valid_until
                            entry.updated_at = now
                            entry.clean()
                            entry.save(
                                update_fields=[
                                    "is_allowed",
                                    "valid_from",
                                    "valid_until",
                                    "updated_at",
                                ]
                            )
                            updated_entries.append(entry)
                            updated_count += 1
                        else:
                            entry = WhitelistEntry(
                                person=person,
                                access_point=access_point,
                                event_id=event.id if event else None,
                                is_allowed=is_allowed,
                                valid_from=valid_from,
                                valid_until=valid_until,
                                created_at=now,
                                updated_at=now,
                            )
                            entry.clean()
                            entry.save()
                            created_entries.append(entry)
                            created_count += 1
        except ValidationError as exc:
            return Response(
                {"detail": exc.message_dict if hasattr(exc, "message_dict") else exc.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "preview": False,
                "created": created_count,
                "updated": updated_count,
                "created_entries": WhitelistEntrySerializer(created_entries, many=True).data,
                "updated_entries": WhitelistEntrySerializer(updated_entries, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
import zipfile
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.settings import api_settings

from access_control.models.models import AnsesVerificationRecord, ExternalAccessLogEntry, WhitelistEntry
from access_control.serializers import (
    ExternalAccessLogEntrySerializer,
    WhitelistBatchCreateSerializer,
    WhitelistEntrySerializer,
)

from rest_framework import permissions, status, views

from access_control.models import BioStarDevice, BioStarUser
from institutions.models import AccessPoint, Event
from people.models import Cliente, Person, PersonType
from access_control.serializers import BioStarDeviceSerializer, BioStarUserSerializer

from access_control.services.biostar2_client import BioStar2Client

from access_control.services import (
    AnsesVerificationError,
    AnsesVerificationService,
    ClientLookupError,
    ExternalAccessLogError,
    ExternalAccessLogSynchronizer,
)
from access_control.services.intelectron.api3000_console import (
    COMMAND_CATALOG,
    execute_command,
    validate_base_payload,
    validate_command_params,
)

from django.core.management import call_command

ANSES_ERROR_MESSAGE = "ACERCATE A UNA OFICINA DE ANSES CON DOCUMENTACIÓN QUE ACREDITE IDENTIDAD"
ANSES_SUCCESS_SNIPPET = "constancia generada."
ANSES_RESULT_PATTERN = re.compile(r"^(?:OK|ERROR) DNI (?P<dni>\d+): (?P<message>.+)$", re.MULTILINE)

ANSES_BACKGROUND_JOBS: dict[str, dict] = {}
ANSES_BACKGROUND_LOCK = threading.Lock()


def _map_anses_status(message: str) -> str:
    lowered = (message or "").strip().lower()
    if ANSES_SUCCESS_SNIPPET in lowered:
        return AnsesVerificationRecord.VerificationStatus.GENERATED
    if ANSES_ERROR_MESSAGE.lower() in lowered:
        return AnsesVerificationRecord.VerificationStatus.OFFICE_REQUIRED
    return AnsesVerificationRecord.VerificationStatus.UNKNOWN


def _extract_anses_messages(stdout: str) -> dict[int, str]:
    messages_by_dni: dict[int, str] = {}
    for match in ANSES_RESULT_PATTERN.finditer(stdout or ""):
        dni = int(match.group("dni"))
        message = (match.group("message") or "").strip()
        if message:
            messages_by_dni[dni] = message
    return messages_by_dni


def _parse_candidate_birth_date(value):
    if not value:
        return None
    if hasattr(value, "date"):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return parse_date(value.strip())
    return None


def _normalize_candidate_age(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _save_anses_records(
    *,
    user,
    pairs: list[tuple[int, int]],
    stdout: str,
    candidates_map: dict[int, dict] | None = None,
) -> None:
    messages_by_dni = _extract_anses_messages(stdout)
    checked_at = timezone.now()
    candidates_map = candidates_map or {}
    for id_cliente, dni in pairs:
        candidate = candidates_map.get(id_cliente) or {}
        fecha_nacimiento = _parse_candidate_birth_date(candidate.get("fecha_nac"))
        message = messages_by_dni.get(dni, "").strip()
        AnsesVerificationRecord.objects.update_or_create(
            requested_by=user,
            id_cliente=id_cliente,
            defaults={
                "dni": dni,
                "verification_status": _map_anses_status(message),
                "verification_message": message,
                "last_checked_at": checked_at,
                "apellido": str(candidate.get("apellido") or "").strip(),
                "nombre": str(candidate.get("nombre") or "").strip(),
                "fecha_nacimiento": fecha_nacimiento,
                "edad": _normalize_candidate_age(candidate.get("edad")),
            },
        )


def _fetch_all_anses_candidates(*, min_age: int, max_age: int) -> list[dict]:
    service = AnsesVerificationService()
    items: list[dict] = []
    offset = 0
    batch_size = 500
    while True:
        payload = service.fetch_candidates(min_age=min_age, max_age=max_age, limit=batch_size, offset=offset)
        rows = payload.get("results", [])
        if not rows:
            break
        items.extend(rows)
        offset += len(rows)
        if len(rows) < batch_size:
            break
    return items


def _apply_candidate_filters(
    *,
    items: list[dict],
    records_map: dict[int, AnsesVerificationRecord],
    exclude_consulted: bool,
    verification_status: str,
) -> list[dict]:
    filtered: list[dict] = []
    for item in items:
        id_cliente = item.get("id_cliente")
        record = records_map.get(id_cliente) if id_cliente is not None else None
        consulted = record is not None
        if exclude_consulted and consulted:
            continue
        if verification_status and verification_status != "all":
            record_status = record.verification_status if record else ""
            if verification_status == "pending":
                if consulted:
                    continue
            elif record_status != verification_status:
                continue
        item["consulted"] = consulted
        item["verification_status"] = record.verification_status if record else ""
        item["verification_message"] = record.verification_message if record else ""
        filtered.append(item)
    return filtered


def _run_anses_filtered_job(job_id: str, user_id: int, min_age: int, max_age: int, exclude_consulted: bool, verification_status: str) -> None:
    User = get_user_model()
    user = User.objects.filter(id=user_id).first()
    if user is None:
        with ANSES_BACKGROUND_LOCK:
            ANSES_BACKGROUND_JOBS[job_id]["status"] = "failed"
            ANSES_BACKGROUND_JOBS[job_id]["error"] = "Usuario inválido."
            ANSES_BACKGROUND_JOBS[job_id]["finished_at"] = timezone.now().isoformat()
        return
    try:
        with ANSES_BACKGROUND_LOCK:
            ANSES_BACKGROUND_JOBS[job_id]["status"] = "running"
        all_items = _fetch_all_anses_candidates(min_age=min_age, max_age=max_age)
        records_qs = AnsesVerificationRecord.objects.filter(requested_by=user)
        records_map = {record.id_cliente: record for record in records_qs}
        clients = _apply_candidate_filters(
            items=all_items,
            records_map=records_map,
            exclude_consulted=exclude_consulted,
            verification_status=verification_status,
        )
        pairs = [
            (int(item["id_cliente"]), int(item["doc_nro"]))
            for item in clients
            if item.get("id_cliente") is not None and item.get("doc_nro") is not None
        ]
        candidates_map = {int(item["id_cliente"]): item for item in clients if item.get("id_cliente") is not None}
        with ANSES_BACKGROUND_LOCK:
            ANSES_BACKGROUND_JOBS[job_id]["total"] = len(pairs)
        if not pairs:
            with ANSES_BACKGROUND_LOCK:
                ANSES_BACKGROUND_JOBS[job_id]["status"] = "completed"
                ANSES_BACKGROUND_JOBS[job_id]["finished_at"] = timezone.now().isoformat()
            return
        service = AnsesVerificationService()
        for index, pair in enumerate(pairs):
            dnis = [pair[1]]
            try:
                result = service.run_verification(dnis, headless=True, no_download=True)
                stdout = result.get("stdout", "")
            except Exception as exc:
                stdout = f"ERROR DNI {pair[1]}: {exc}"
            _save_anses_records(user=user, pairs=[pair], stdout=stdout, candidates_map=candidates_map)
            with ANSES_BACKGROUND_LOCK:
                ANSES_BACKGROUND_JOBS[job_id]["processed"] += 1
            if index < len(pairs) - 1:
                time.sleep(1)
        with ANSES_BACKGROUND_LOCK:
            ANSES_BACKGROUND_JOBS[job_id]["status"] = "completed"
            ANSES_BACKGROUND_JOBS[job_id]["finished_at"] = timezone.now().isoformat()
    except Exception as exc:
        with ANSES_BACKGROUND_LOCK:
            ANSES_BACKGROUND_JOBS[job_id]["status"] = "failed"
            ANSES_BACKGROUND_JOBS[job_id]["error"] = str(exc)
            ANSES_BACKGROUND_JOBS[job_id]["finished_at"] = timezone.now().isoformat()



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


class BioStarDeviceUserdataAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, device_id: int):
        client = BioStar2Client.from_db_and_env()
        payload = client.discover_device_userdata(device_id)
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


class AnsesCandidatesAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        page_param = request.query_params.get("page", 1)
        page_size_param = request.query_params.get("page_size", 50)
        min_age_param = request.query_params.get("min_age", 90)
        max_age_param = request.query_params.get("max_age", 120)
        exclude_consulted_param = (request.query_params.get("exclude_consulted") or "").strip().lower()
        verification_status = (request.query_params.get("verification_status") or "all").strip().lower()
        exclude_consulted = exclude_consulted_param in {"1", "true", "yes", "si"}
        allowed_status_filters = {
            "all",
            "pending",
            AnsesVerificationRecord.VerificationStatus.GENERATED,
            AnsesVerificationRecord.VerificationStatus.OFFICE_REQUIRED,
            AnsesVerificationRecord.VerificationStatus.UNKNOWN,
        }
        if verification_status not in allowed_status_filters:
            return Response(
                {"detail": "El parámetro 'verification_status' es inválido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            page = int(page_param)
        except (TypeError, ValueError):
            return Response({"detail": "El parámetro 'page' debe ser numérico."}, status=status.HTTP_400_BAD_REQUEST)
        if page <= 0:
            return Response(
                {"detail": "El parámetro 'page' debe ser mayor a cero."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            page_size = int(page_size_param)
        except (TypeError, ValueError):
            return Response(
                {"detail": "El parámetro 'page_size' debe ser numérico."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if page_size != 50:
            return Response(
                {"detail": "El parámetro 'page_size' debe ser 50."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            min_age = int(min_age_param)
            max_age = int(max_age_param)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Los parámetros 'min_age' y 'max_age' deben ser numéricos."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if min_age < 0 or max_age < 0 or min_age > max_age:
            return Response(
                {"detail": "Rango de edades inválido."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            has_local_filters = exclude_consulted or verification_status != "all"
            if has_local_filters:
                all_items = _fetch_all_anses_candidates(min_age=min_age, max_age=max_age)
                records_qs = AnsesVerificationRecord.objects.filter(requested_by=request.user)
                records_map = {record.id_cliente: record for record in records_qs}
                filtered = _apply_candidate_filters(
                    items=all_items,
                    records_map=records_map,
                    exclude_consulted=exclude_consulted,
                    verification_status=verification_status,
                )
                total_count = len(filtered)
                offset = (page - 1) * page_size
                items = filtered[offset : offset + page_size]
            else:
                offset = (page - 1) * page_size
                payload = AnsesVerificationService().fetch_candidates(
                    min_age=min_age,
                    max_age=max_age,
                    limit=page_size,
                    offset=offset,
                )
                records_qs = AnsesVerificationRecord.objects.filter(requested_by=request.user)
                records_map = {record.id_cliente: record for record in records_qs}
                items = _apply_candidate_filters(
                    items=payload.get("results", []),
                    records_map=records_map,
                    exclude_consulted=False,
                    verification_status="all",
                )
                total_count = payload.get("count", len(items))
        except (AnsesVerificationError, ClientLookupError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response(
                {"detail": "Error inesperado al consultar candidatos para ANSES."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {
                "count": total_count,
                "page": page,
                "page_size": page_size,
                "results": items,
            },
            status=status.HTTP_200_OK,
        )


class AnsesVerifyAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        clients = request.data.get("clients") or []
        dni_list = request.data.get("dni_list")
        headless = bool(request.data.get("headless", True))
        no_download = bool(request.data.get("no_download", True))
        if clients and not isinstance(clients, list):
            return Response(
                {"detail": "El parámetro 'clients' debe ser una lista."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not clients and (not isinstance(dni_list, list) or not dni_list):
            return Response(
                {"detail": "Debe enviar 'clients' o 'dni_list' con al menos un DNI."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            if clients:
                pairs: list[tuple[int, int]] = []
                candidates_map: dict[int, dict] = {}
                for item in clients:
                    id_cliente = int(item["id_cliente"])
                    doc_nro = int(item["doc_nro"])
                    pairs.append((id_cliente, doc_nro))
                    candidates_map[id_cliente] = item
                dnis = [pair[1] for pair in pairs]
            else:
                pairs = []
                candidates_map = {}
                dnis = [int(item) for item in dni_list]
        except (TypeError, ValueError):
            return Response(
                {"detail": "Los clientes y DNIs deben ser numéricos."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = AnsesVerificationService().run_verification(
                dnis,
                headless=headless,
                no_download=no_download,
            )
            if pairs:
                _save_anses_records(
                    user=request.user,
                    pairs=pairs,
                    stdout=result.get("stdout", ""),
                    candidates_map=candidates_map,
                )
        except (AnsesVerificationError, ClientLookupError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response(
                {"detail": "Error inesperado al ejecutar verificación ANSES."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(result, status=status.HTTP_200_OK)


class AnsesVerifyFilteredAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            min_age = int(request.data.get("min_age", 90))
            max_age = int(request.data.get("max_age", 120))
        except (TypeError, ValueError):
            return Response({"detail": "Los parámetros de edad deben ser numéricos."}, status=status.HTTP_400_BAD_REQUEST)
        if min_age < 0 or max_age < 0 or min_age > max_age:
            return Response({"detail": "Rango de edades inválido."}, status=status.HTTP_400_BAD_REQUEST)
        exclude_consulted = bool(request.data.get("exclude_consulted", False))
        verification_status = (request.data.get("verification_status") or "all").strip().lower()
        allowed_status_filters = {
            "all",
            "pending",
            AnsesVerificationRecord.VerificationStatus.GENERATED,
            AnsesVerificationRecord.VerificationStatus.OFFICE_REQUIRED,
            AnsesVerificationRecord.VerificationStatus.UNKNOWN,
        }
        if verification_status not in allowed_status_filters:
            return Response(
                {"detail": "El parámetro 'verification_status' es inválido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        job_id = uuid.uuid4().hex
        with ANSES_BACKGROUND_LOCK:
            ANSES_BACKGROUND_JOBS[job_id] = {
                "status": "pending",
                "total": 0,
                "processed": 0,
                "error": "",
                "started_at": timezone.now().isoformat(),
                "finished_at": "",
            }
        thread = threading.Thread(
            target=_run_anses_filtered_job,
            kwargs={
                "job_id": job_id,
                "user_id": request.user.id,
                "min_age": min_age,
                "max_age": max_age,
                "exclude_consulted": exclude_consulted,
                "verification_status": verification_status,
            },
            daemon=True,
        )
        thread.start()
        return Response({"job_id": job_id, "status": "pending"}, status=status.HTTP_202_ACCEPTED)


class AnsesVerifyFilteredStatusAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, job_id: str):
        with ANSES_BACKGROUND_LOCK:
            job = ANSES_BACKGROUND_JOBS.get(job_id)
        if not job:
            return Response({"detail": "Proceso no encontrado."}, status=status.HTTP_404_NOT_FOUND)
        return Response(job, status=status.HTTP_200_OK)


def _calculate_age(fecha_nac) -> str:
    if not fecha_nac:
        return ""
    birth_date = fecha_nac.date() if hasattr(fecha_nac, "date") else fecha_nac
    today = timezone.localdate()
    years = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return str(years)


class AnsesProcessedExportAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _xlsx_header_row(cells: list[str]) -> str:
        values = []
        for index, value in enumerate(cells):
            column = chr(65 + index)
            safe_value = escape(str(value or ""))
            values.append(f'<c r="{column}1" t="inlineStr"><is><t>{safe_value}</t></is></c>')
        return f'<row r="1">{"".join(values)}</row>'

    @staticmethod
    def _xlsx_data_row(row_index: int, cells: list[str]) -> str:
        values = []
        for index, value in enumerate(cells):
            column = chr(65 + index)
            safe_value = escape(str(value or ""))
            values.append(f'<c r="{column}{row_index}" t="inlineStr"><is><t>{safe_value}</t></is></c>')
        return f'<row r="{row_index}">{"".join(values)}</row>'

    def _build_xlsx(self, headers: list[str], rows: list[list[str]]) -> bytes:
        workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Vitalicios procesados" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
        workbook_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
        root_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
        content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

        header_row = self._xlsx_header_row(headers)
        data_rows = [self._xlsx_data_row(index, row) for index, row in enumerate(rows, start=2)]
        worksheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>{header_row}{"".join(data_rows)}</sheetData>
</worksheet>"""

        output = BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types_xml)
            archive.writestr("_rels/.rels", root_rels_xml)
            archive.writestr("xl/workbook.xml", workbook_xml)
            archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
            archive.writestr("xl/worksheets/sheet1.xml", worksheet_xml)
        return output.getvalue()

    def get(self, request):
        records = (
            AnsesVerificationRecord.objects.filter(requested_by=request.user)
            .order_by("-last_checked_at", "-created_at")
        )
        timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d_%H%M%S")
        filename = f"vitalicios_procesados_{timestamp}.xlsx"

        record_ids = [record.id_cliente for record in records]
        clientes_map = Cliente.objects.in_bulk(record_ids, field_name="id_cliente")

        headers = [
            "Numero",
            "Apellido",
            "Nombre",
            "Fecha Nacimiento",
            "Edad",
            "Procesado",
            "Fecha de ultimo procesamiento",
            "Resultado",
        ]
        rows = []
        for record in records:
            cliente = clientes_map.get(record.id_cliente)
            apellido = (cliente.apellido if cliente and cliente.apellido else record.apellido) or ""
            nombre = (cliente.nombre if cliente and cliente.nombre else record.nombre) or ""
            fecha_nac = ""
            edad = ""
            if cliente and cliente.fecha_nac:
                fecha_nac = cliente.fecha_nac.date().isoformat()
                edad = _calculate_age(cliente.fecha_nac)
            elif record.fecha_nacimiento:
                fecha_nac = record.fecha_nacimiento.isoformat()
                edad = str(record.edad) if record.edad is not None else _calculate_age(record.fecha_nacimiento)
            rows.append(
                [
                    str(record.id_cliente),
                    apellido,
                    nombre,
                    fecha_nac,
                    edad,
                    "Si",
                    timezone.localtime(record.last_checked_at).strftime("%Y-%m-%d %H:%M:%S")
                    if record.last_checked_at
                    else "",
                    record.verification_message or record.get_verification_status_display(),
                ]
            )

        content = self._build_xlsx(headers=headers, rows=rows)
        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class Api3000CommandCatalogAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response({"commands": COMMAND_CATALOG})


class Api3000PingAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        try:
            base = validate_base_payload(payload)
            result = execute_command(command="lib_version", base=base, params={})
            return Response({"ok": True, **result})
        except ValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            return Response({"ok": False, "errors": detail}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response({"ok": False, "detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)


class Api3000ExecuteCommandAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        command = str(payload.get("command") or "").strip()
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

        try:
            base = validate_base_payload(payload, command=command)
            parsed_params = validate_command_params(command, params)
            result = execute_command(command=command, base=base, params=parsed_params)
            return Response({"ok": True, "result": result})
        except ValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            return Response({"ok": False, "errors": detail}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response({"ok": False, "detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

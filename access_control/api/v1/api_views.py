from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid
import time
import zipfile
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

import requests

from django.contrib.auth import get_user_model
from django.db import close_old_connections, transaction
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
    AccessCheckError,
    AnsesVerificationError,
    AnsesVerificationService,
    ClientLookupError,
    ExternalAccessLogError,
    ExternalAccessLogSynchronizer,
    MSSQLAccessCheckService,
)
from access_control.services.intelectron.api3000_console import (
    COMMAND_CATALOG,
    execute_command,
    validate_base_payload,
    validate_command_params,
)

from django.core.management import call_command

ANSES_ERROR_MESSAGE = "ACERCATE A UNA OFICINA DE ANSES CON DOCUMENTACIÓN QUE ACREDITE IDENTIDAD"
ANSES_SUCCESS_SNIPPET = "constancia generada"
ANSES_DECEASED_SNIPPET = "fallecido"
ANSES_RESULT_PATTERN = re.compile(r"^(?:OK|ERROR) DNI (?P<dni>\d+): (?P<message>.+)$", re.MULTILINE)
ANSES_FINAL_STATUS_PATTERN = re.compile(r"^ESTADO FINAL DNI (?P<dni>\d+): (?P<message>.+)$", re.MULTILINE)

ANSES_BACKGROUND_JOBS: dict[str, dict] = {}
ANSES_BACKGROUND_LOCK = threading.Lock()
ANSES_DB_WRITE_LOCK = threading.Lock()


def _map_anses_status(message: str) -> str:
    lowered = (message or "").strip().lower()
    if ANSES_SUCCESS_SNIPPET in lowered:
        return AnsesVerificationRecord.VerificationStatus.GENERATED
    if ANSES_ERROR_MESSAGE.lower() in lowered:
        return AnsesVerificationRecord.VerificationStatus.OFFICE_REQUIRED
    if ANSES_DECEASED_SNIPPET in lowered:
        return AnsesVerificationRecord.VerificationStatus.DECEASED
    return AnsesVerificationRecord.VerificationStatus.UNKNOWN


def _extract_anses_messages(stdout: str) -> dict[int, str]:
    messages_by_dni: dict[int, str] = {}
    output = stdout or ""

    for match in ANSES_RESULT_PATTERN.finditer(output):
        dni = int(match.group("dni"))
        message = (match.group("message") or "").strip()
        if message:
            messages_by_dni[dni] = message

    # Si existe "ESTADO FINAL", tiene prioridad por representar el resultado consolidado
    # tras la validación secundaria (busca-datos) cuando aplica.
    for match in ANSES_FINAL_STATUS_PATTERN.finditer(output):
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
        existing_record = AnsesVerificationRecord.objects.filter(requested_by=user, id_cliente=id_cliente).first()
        if existing_record and existing_record.verification_status in {
            AnsesVerificationRecord.VerificationStatus.GENERATED,
            AnsesVerificationRecord.VerificationStatus.DECEASED,
        }:
            continue
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
        if exclude_consulted and consulted and record.verification_status in {
            AnsesVerificationRecord.VerificationStatus.GENERATED,
            AnsesVerificationRecord.VerificationStatus.DECEASED,
        }:
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


def _run_anses_filtered_job(job_id: str, user_id: int, min_age: int, max_age: int, exclude_consulted: bool, verification_status: str, skip_anses: bool = False) -> None:
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
        def _process_pair(pair: tuple[int, int]) -> None:
            close_old_connections()
            service = AnsesVerificationService()
            dnis = [pair[1]]
            try:
                result = service.run_verification(dnis, headless=True, no_download=True, skip_anses=skip_anses)
                stdout = result.get("stdout", "")
            except Exception as exc:
                stdout = f"ERROR DNI {pair[1]}: {exc}"
            with ANSES_DB_WRITE_LOCK:
                _save_anses_records(user=user, pairs=[pair], stdout=stdout, candidates_map=candidates_map)
            with ANSES_BACKGROUND_LOCK:
                ANSES_BACKGROUND_JOBS[job_id]["processed"] += 1
            close_old_connections()

        for index, pair in enumerate(pairs):
            _process_pair(pair)
            if index < len(pairs) - 1:
                time.sleep(7)
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


class BioStarUserLookupAPI(views.APIView):
    """Busca un usuario en BioStar (en vivo) a partir de DNI, Id_Cliente o credencial de xSys.

    El user_id de BioStar coincide con el Id_Cliente de xSys, así que el DNI/credencial
    se resuelve primero contra xsys_geba (MSSQLAccessCheckService) y luego se consulta
    ese Id_Cliente directamente en BioStar.
    """

    permission_classes = [permissions.IsAuthenticated]

    IDENTIFIER_PARAMS = ("doc_nro", "id_cliente", "credencial")

    def get(self, request):
        params = request.query_params
        present = [name for name in self.IDENTIFIER_PARAMS if params.get(name)]
        if len(present) != 1:
            return Response(
                {
                    "detail": "Debe indicar exactamente uno de estos parámetros: "
                    + ", ".join(self.IDENTIFIER_PARAMS)
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        identifier_type = present[0]
        identifier_value = params[identifier_type]

        if identifier_type == "id_cliente":
            try:
                id_cliente = int(identifier_value)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "id_cliente debe ser numérico."}, status=status.HTTP_400_BAD_REQUEST
                )
        else:
            try:
                id_cliente = MSSQLAccessCheckService().resolve_id_cliente(
                    identifier_type=identifier_type, identifier_value=identifier_value
                )
            except AccessCheckError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            if not id_cliente:
                return Response(
                    {"found": False, "detail": "No se encontró un socio con ese dato en xSys."},
                    status=status.HTTP_200_OK,
                )

        # Se resuelve contra el espejo local BioStarUser (poblado por
        # biostar_sync_users), NO contra BioStar en vivo: así la UI no le agrega
        # carga al servidor de BioStar. El único consumidor regular de BioStar es
        # el biostar-poller. La frescura depende de correr biostar_sync_users.
        mirror = BioStarUser.objects.filter(user_id=id_cliente).first()
        if mirror is not None:
            biostar_user = mirror.raw_payload or {
                "user_id": str(mirror.user_id),
                "name": mirror.name,
            }
        else:
            biostar_user = None

        return Response(
            {
                "found": biostar_user is not None,
                "id_cliente": id_cliente,
                "biostar_user": biostar_user,
                "source": "espejo_local",
            },
            status=status.HTTP_200_OK,
        )


class BioStarUserSearchAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from django.db.models import Q

        search_text = (request.data.get("search_text") or "").strip()
        if not search_text:
            return Response(
                {"detail": "El parámetro 'search_text' es obligatorio."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            limit = max(1, int(request.data.get("limit") or 50))
            offset = max(0, int(request.data.get("offset") or 0))
        except (TypeError, ValueError):
            return Response(
                {"detail": "limit/offset deben ser numéricos."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Búsqueda contra el espejo local BioStarUser (poblado por
        # biostar_sync_users), NO contra BioStar en vivo: la UI no le agrega carga
        # al servidor de BioStar. Los campos de cada fila salen del raw_payload
        # guardado (mismo formato que consume la consola).
        q = Q(name__icontains=search_text) | Q(user_unique_id__icontains=search_text)
        if search_text.isdigit():
            q |= Q(user_id=int(search_text))
        qs = BioStarUser.objects.filter(is_active=True).filter(q).order_by("name", "user_id")
        total = qs.count()
        rows = []
        for u in qs[offset:offset + limit]:
            row = dict(u.raw_payload) if isinstance(u.raw_payload, dict) and u.raw_payload else {}
            row.setdefault("user_id", str(u.user_id))
            row.setdefault("name", u.name)
            rows.append(row)
        return Response(
            {"UserCollection": {"total": str(total), "rows": rows}, "source": "espejo_local"},
            status=status.HTTP_200_OK,
        )


class BioStarUserForceLoadAPI(views.APIView):
    """POST /api/biostar/users/<int:id_cliente>/force-load/

    Fuerza la carga (export) del socio a TODOS los equipos BioStar. Se dispara
    desde el diagnóstico facial para re-empujar el usuario a los lectores en el
    momento (útil cuando quedó borrado de un equipo). No crea el rostro: si el
    usuario no tiene rostro enrolado, hay que re-enrolarlo aparte.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id_cliente: int):
        client, cerr = _biostar_client_or_error()
        if cerr:
            return cerr
        try:
            payload = client.add_user_to_all_devices(id_cliente, overwrite=True)
        except requests.RequestException as exc:
            return Response(
                {"detail": "No se pudo forzar la carga en BioStar: " + str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as exc:  # p.ej. sin devices / config
            return Response(
                {"detail": "No se pudo forzar la carga: " + str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        biostar_logger.info("force_load user=%s by=%s", id_cliente, request.user)
        return Response(
            {"ok": True, "id_cliente": id_cliente, "biostar": payload},
            status=status.HTTP_200_OK,
        )


class SocioAvisoAPI(views.APIView):
    """GET/POST /api/socios/<id_cliente>/avisos/ — avisos/registros de un socio.

    Se usan desde el diagnóstico de facial para dejar un aviso: "Notificar que
    pase por Socios" (predefinido) o una nota libre. Registro local, no toca xSys.
    """

    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _dict(a) -> dict:
        return {
            "id": a.id, "tipo": a.tipo, "texto": a.texto,
            "creado_por": a.creado_por, "created_at": a.created_at.isoformat(),
            "resuelto": a.resuelto,
            "resuelto_por": a.resuelto_por,
            "resuelto_at": a.resuelto_at.isoformat() if a.resuelto_at else None,
        }

    def get(self, request, id_cliente: int):
        from access_control.models import SocioAviso

        avisos = SocioAviso.objects.filter(id_cliente=id_cliente).order_by("-created_at")[:50]
        return Response({"id_cliente": id_cliente, "avisos": [self._dict(a) for a in avisos]})

    def post(self, request, id_cliente: int):
        from access_control.models import SocioAviso

        tipo = (request.data.get("tipo") or SocioAviso.TIPO_LIBRE).strip()[:32]
        if tipo == SocioAviso.TIPO_PASE_POR_SOCIOS:
            texto = SocioAviso.TEXTO_PASE_POR_SOCIOS
        else:
            tipo = SocioAviso.TIPO_LIBRE
            texto = (request.data.get("texto") or "").strip()[:500]
        if not texto:
            return Response(
                {"detail": "El texto del aviso es obligatorio."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        aviso = SocioAviso.objects.create(
            id_cliente=id_cliente, tipo=tipo, texto=texto,
            creado_por=(getattr(request.user, "username", "") or "")[:150],
        )
        return Response(self._dict(aviso), status=status.HTTP_201_CREATED)


class AvisoResolverAPI(views.APIView):
    """POST /api/avisos/<aviso_id>/resolver/ — marca un aviso como notificado
    (o lo reabre con ``{"deshacer": true}``)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, aviso_id: int):
        from django.utils import timezone

        from access_control.models import SocioAviso

        a = SocioAviso.objects.filter(pk=aviso_id).first()
        if not a:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if request.data.get("deshacer"):
            a.resuelto = False
            a.resuelto_at = None
            a.resuelto_por = ""
        else:
            a.resuelto = True
            a.resuelto_at = timezone.now()
            a.resuelto_por = (getattr(request.user, "username", "") or "")[:150]
        a.save(update_fields=["resuelto", "resuelto_at", "resuelto_por"])
        return Response({"id": a.id, "resuelto": a.resuelto, "resuelto_por": a.resuelto_por})


biostar_logger = logging.getLogger("access_control.biostar")


def _biostar_client_or_error():
    """Instancia el cliente BioStar; devuelve (client, error_response)."""
    try:
        return BioStar2Client.from_db_and_env(), None
    except Exception as exc:  # config faltante / env incompleto
        return None, Response(
            {"detail": "No se pudo inicializar el cliente BioStar: " + str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def _resolve_id_cliente_from_request(request):
    """Resuelve un Id_Cliente de BioStar a partir de doc_nro / id_cliente / credencial.

    Reusa la lógica de BioStarUserLookupAPI. Devuelve (id_cliente, error_response).
    """
    identifiers = ("doc_nro", "id_cliente", "credencial")
    present = [name for name in identifiers if request.data.get(name)]
    if len(present) != 1:
        return None, Response(
            {"detail": "Debe indicar exactamente uno de: " + ", ".join(identifiers)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    identifier_type = present[0]
    identifier_value = str(request.data.get(identifier_type)).strip()

    if identifier_type == "id_cliente":
        try:
            return int(identifier_value), None
        except (TypeError, ValueError):
            return None, Response(
                {"detail": "id_cliente debe ser numérico."}, status=status.HTTP_400_BAD_REQUEST
            )

    try:
        id_cliente = MSSQLAccessCheckService().resolve_id_cliente(
            identifier_type=identifier_type, identifier_value=identifier_value
        )
    except AccessCheckError as exc:
        return None, Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    if not id_cliente:
        return None, Response(
            {"detail": "No se encontró un socio con ese dato en xSys."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return int(id_cliente), None


class BioStarDeviceDetailAPI(views.APIView):
    """Ficha completa (en vivo) de un dispositivo: red, firmware, tipo, grupo, capacidades."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, device_id: int):
        client, err = _biostar_client_or_error()
        if err:
            return err
        try:
            payload = client.get_device(device_id)
        except requests.RequestException as exc:
            return Response(
                {"detail": "No se pudo consultar BioStar: " + str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        device = payload.get("Device", payload) or {}
        version = device.get("version") or {}
        lan = device.get("lan") or {}
        summary = {
            "id": device.get("id"),
            "name": device.get("name"),
            "status": device.get("status"),
            "device_type": (device.get("device_type_id") or {}).get("name"),
            "device_group": (device.get("device_group_id") or {}).get("name"),
            "device_group_id": (device.get("device_group_id") or {}).get("id"),
            "product_name": version.get("product_name"),
            "firmware": version.get("firmware"),
            "hardware": version.get("hardware"),
            "kernel": version.get("kernel"),
            "ip": lan.get("ip"),
            "gateway": lan.get("gateway"),
            "subnet_mask": lan.get("subnet_mask"),
            "dns_addr": lan.get("dns_addr"),
            "server_ip": lan.get("server_ip"),
            "device_port": lan.get("device_port"),
            "enable_dhcp": lan.get("enable_dhcp"),
            "supports_face": bool(device.get("face")),
            "supports_fingerprint": bool(device.get("fingerprint")),
            "supports_card": bool(device.get("card")),
        }
        return Response({"summary": summary, "raw": device}, status=status.HTTP_200_OK)


class BioStarDeviceGroupListAPI(views.APIView):
    """Lista de grupos de dispositivos (para el selector de 'mover a grupo')."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        client, err = _biostar_client_or_error()
        if err:
            return err
        try:
            payload = client.list_device_groups()
        except requests.RequestException as exc:
            return Response(
                {"detail": "No se pudo consultar BioStar: " + str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        rows = (payload.get("DeviceGroupCollection") or {}).get("rows") or []
        groups = [{"id": r.get("id"), "name": r.get("name")} for r in rows]
        return Response({"groups": groups}, status=status.HTTP_200_OK)


class BioStarDeviceRenameAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, device_id: int):
        name = (request.data.get("name") or "").strip()
        if not name:
            return Response(
                {"detail": "El parámetro 'name' es obligatorio."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        client, err = _biostar_client_or_error()
        if err:
            return err
        try:
            payload = client.rename_device(device_id, name)
        except requests.RequestException as exc:
            return Response(
                {"detail": "No se pudo renombrar en BioStar: " + str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        biostar_logger.info("rename device=%s name=%r by=%s", device_id, name, request.user)
        return Response({"ok": True, "biostar": payload}, status=status.HTTP_200_OK)


class BioStarDeviceMoveGroupAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, device_id: int):
        group_id = request.data.get("group_id")
        try:
            group_id = int(group_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "El parámetro 'group_id' debe ser numérico."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        client, err = _biostar_client_or_error()
        if err:
            return err
        try:
            payload = client.move_device_group(device_id, group_id)
        except requests.RequestException as exc:
            return Response(
                {"detail": "No se pudo mover de grupo en BioStar: " + str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        biostar_logger.info(
            "move_group device=%s group=%s by=%s", device_id, group_id, request.user
        )
        return Response({"ok": True, "biostar": payload}, status=status.HTTP_200_OK)


class BioStarDeviceRebootAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, device_id: int):
        client, err = _biostar_client_or_error()
        if err:
            return err
        try:
            payload = client.reboot_device(device_id)
        except requests.RequestException as exc:
            return Response(
                {"detail": "No se pudo reiniciar el equipo en BioStar: " + str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        biostar_logger.warning("REBOOT device=%s by=%s", device_id, request.user)
        return Response({"ok": True, "biostar": payload}, status=status.HTTP_200_OK)


class BioStarDeviceLockAPI(views.APIView):
    """Bloquea o desbloquea la autenticación del equipo. ?action=lock|unlock (o path)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, device_id: int, action: str):
        if action not in ("lock", "unlock"):
            return Response(
                {"detail": "action debe ser 'lock' o 'unlock'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        client, err = _biostar_client_or_error()
        if err:
            return err
        method = client.lock_device if action == "lock" else client.unlock_device
        try:
            payload = method(device_id)
        except requests.RequestException as exc:
            return Response(
                {"detail": f"No se pudo {action} el equipo en BioStar: " + str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        biostar_logger.warning("%s device=%s by=%s", action.upper(), device_id, request.user)
        return Response({"ok": True, "biostar": payload}, status=status.HTTP_200_OK)


class BioStarDeviceDoorsAPI(views.APIView):
    """Puertas asociadas a un equipo (por entry_device_id) con su estado en vivo."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, device_id: int):
        client, err = _biostar_client_or_error()
        if err:
            return err
        try:
            doors_payload = client.list_doors()
            status_payload = client.door_status()
        except requests.RequestException as exc:
            return Response(
                {"detail": "No se pudo consultar BioStar: " + str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        rows = (doors_payload.get("DoorCollection") or {}).get("rows") or []
        status_rows = (status_payload.get("DoorStatusCollection") or {}).get("rows") or []

        def _door_status_id(row):
            did = row.get("door_id")
            if isinstance(did, dict):
                return str(did.get("id"))
            return str(did)

        status_by_id = {_door_status_id(r): r for r in status_rows}

        doors = []
        for row in rows:
            entry = row.get("entry_device_id") or {}
            if str(entry.get("id")) != str(device_id):
                continue
            door_id = str(row.get("id"))
            st = status_by_id.get(door_id, {})
            doors.append(
                {
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "open_duration": row.get("open_duration"),
                    "unconditional_lock": row.get("unconditional_lock"),
                    "opened": st.get("opened"),
                    "unlocked": st.get("unlocked"),
                    "alarm": st.get("alarm"),
                }
            )
        return Response({"device_id": device_id, "doors": doors}, status=status.HTTP_200_OK)


class BioStarDoorActionAPI(views.APIView):
    """Acción manual sobre una puerta: open / lock / unlock / release."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, door_id: int, action: str):
        if action not in BioStar2Client.DOOR_ACTIONS:
            return Response(
                {"detail": "action debe ser uno de: " + ", ".join(BioStar2Client.DOOR_ACTIONS)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        client, err = _biostar_client_or_error()
        if err:
            return err
        try:
            payload = client.door_action(door_id, action)
        except requests.RequestException as exc:
            return Response(
                {"detail": f"No se pudo {action} la puerta en BioStar: " + str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        biostar_logger.warning("DOOR %s door=%s by=%s", action.upper(), door_id, request.user)
        return Response({"ok": True, "biostar": payload}, status=status.HTTP_200_OK)


class BioStarDeviceUserAddAPI(views.APIView):
    """Agrega un socio (por DNI/credencial/id_cliente) a un equipo puntual."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, device_id: int):
        id_cliente, err = _resolve_id_cliente_from_request(request)
        if err:
            return err
        client, cerr = _biostar_client_or_error()
        if cerr:
            return cerr
        try:
            payload = client.add_user_to_device(id_cliente, device_id)
        except requests.RequestException as exc:
            return Response(
                {"detail": "No se pudo agregar el usuario en BioStar: " + str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        biostar_logger.info(
            "user_add device=%s user=%s by=%s", device_id, id_cliente, request.user
        )
        return Response(
            {"ok": True, "id_cliente": id_cliente, "biostar": payload},
            status=status.HTTP_200_OK,
        )


class BioStarDeviceUserRemoveAPI(views.APIView):
    """Quita un socio de un equipo puntual."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, device_id: int):
        id_cliente, err = _resolve_id_cliente_from_request(request)
        if err:
            return err
        client, cerr = _biostar_client_or_error()
        if cerr:
            return cerr
        try:
            payload = client.remove_user_from_device(device_id, id_cliente)
        except requests.RequestException as exc:
            return Response(
                {"detail": "No se pudo quitar el usuario en BioStar: " + str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        biostar_logger.info(
            "user_remove device=%s user=%s by=%s", device_id, id_cliente, request.user
        )
        return Response(
            {"ok": True, "id_cliente": id_cliente, "biostar": payload},
            status=status.HTTP_200_OK,
        )


class BioStarDeviceUsersClearAPI(views.APIView):
    """Vacía TODOS los usuarios de la caché local de un equipo (DELETE id=*)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, device_id: int):
        client, err = _biostar_client_or_error()
        if err:
            return err
        try:
            payload = client.remove_user_from_device(device_id, "*")
        except requests.RequestException as exc:
            return Response(
                {"detail": "No se pudo vaciar el equipo en BioStar: " + str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        biostar_logger.warning("users_clear device=%s by=%s", device_id, request.user)
        return Response({"ok": True, "biostar": payload}, status=status.HTTP_200_OK)


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


class AccessCheckAPI(views.APIView):
    """Verificación rápida (alternativa a CP_SCA_RegistrarAcceso) de si un socio puede ingresar por un acceso."""

    permission_classes = [permissions.IsAuthenticated]

    IDENTIFIER_PARAMS = ("doc_nro", "id_cliente", "credencial")
    DOOR_PARAMS = ("id_acceso", "id_controlador")

    def get(self, request):
        params = request.query_params

        present_identifiers = [name for name in self.IDENTIFIER_PARAMS if params.get(name)]
        if len(present_identifiers) != 1:
            return Response(
                {
                    "detail": "Debe indicar exactamente uno de estos parámetros: "
                    + ", ".join(self.IDENTIFIER_PARAMS)
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        identifier_type = present_identifiers[0]
        identifier_value = params[identifier_type]

        present_doors = [name for name in self.DOOR_PARAMS if params.get(name)]
        if len(present_doors) != 1:
            return Response(
                {
                    "detail": "Debe indicar exactamente uno de estos parámetros: "
                    + ", ".join(self.DOOR_PARAMS)
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        id_acceso = None
        id_controlador = None
        try:
            if present_doors[0] == "id_acceso":
                id_acceso = int(params["id_acceso"])
            else:
                id_controlador = int(params["id_controlador"])
        except (TypeError, ValueError):
            return Response(
                {"detail": "id_acceso / id_controlador deben ser numéricos."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = MSSQLAccessCheckService().check_access(
                identifier_type=identifier_type,
                identifier_value=identifier_value,
                id_acceso=id_acceso,
                id_controlador=id_controlador,
            )
        except AccessCheckError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK)


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
            AnsesVerificationRecord.VerificationStatus.DECEASED,
            AnsesVerificationRecord.VerificationStatus.UNKNOWN,
            AnsesVerificationRecord.VerificationStatus.DECEASED,
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
        skip_anses = bool(request.data.get("skip_anses", False))
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
                skip_anses=skip_anses,
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
        skip_anses = bool(request.data.get("skip_anses", False))
        allowed_status_filters = {
            "all",
            "pending",
            AnsesVerificationRecord.VerificationStatus.GENERATED,
            AnsesVerificationRecord.VerificationStatus.DECEASED,
            AnsesVerificationRecord.VerificationStatus.UNKNOWN,
            AnsesVerificationRecord.VerificationStatus.DECEASED,
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
                "skip_anses": skip_anses,
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
            "Cliente_id",
            "DNI",
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
                    str(record.dni or ""),
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


class IntelektronDeviceListAPI(views.APIView):
    """Lista de equipos Intelektron = controladores de xSys que tienen IP configurada.

    La presencia de IP (Intelek_IP / Ult_IP en CD_Controladores) marca que el
    controlador es hardware Intelektron API-3000. Puerto host TCP por defecto: 3001.
    """

    permission_classes = [permissions.IsAuthenticated]

    DEFAULT_PORT = 3001

    def get(self, request):
        from xsys.models.controlador import XsysControlador

        qs = (
            XsysControlador.objects.exclude(ip="")
            .exclude(ip__isnull=True)
            .order_by("id_acceso", "descripcion")
        )
        devices = [
            {
                "id_controlador": c.id_controlador,
                "descripcion": c.descripcion,
                "id_acceso": c.id_acceso,
                "tipo_cont": c.tipo_cont,
                "activo": c.activo,
                "ip": c.ip,
                "port": self.DEFAULT_PORT,
            }
            for c in qs
        ]
        return Response({"devices": devices}, status=status.HTTP_200_OK)


class IntelektronEventsAPI(views.APIView):
    """Últimos eventos (marcas) leídos por el listener de un molinete Intelektron.

    Lee del espejo local `IntelektronEvent` (lo puebla el comando
    `intelektron_listener`). No toca la placa.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from access_control.models import IntelektronEvent

        ip = (request.query_params.get("ip") or "").strip()
        try:
            limit = int(request.query_params.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))

        qs = IntelektronEvent.objects.all()
        if ip:
            qs = qs.filter(device_ip=ip)
        events = [
            {
                "id": e.id,
                "device_ip": e.device_ip,
                "access_id": e.access_id,
                "event_code": e.event_code,
                "event_name": e.event_name,
                "direction": e.direction,
                "direction_name": e.direction_name,
                "device_time": e.device_time.isoformat() if e.device_time else None,
                "created_at": e.created_at.isoformat(),
            }
            for e in qs[:limit]
        ]
        return Response({"events": events}, status=status.HTTP_200_OK)

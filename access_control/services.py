"""Servicios relacionados con la integración de registros externos de acceso."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Iterable

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

try:  # pragma: no cover - la importación depende del entorno
    import pyodbc  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - cubierto por prueba negativa
    pyodbc = None  # type: ignore

from .models import ExternalAccessLogEntry


logger = logging.getLogger(__name__)


class ExternalAccessLogError(RuntimeError):
    """Error genérico al consultar los registros externos de acceso."""


class ExternalAccessLogService:
    """Obtiene los últimos ingresos desde una base de datos externa MSSQL."""

    FIELDS: tuple[tuple[str, str], ...] = (
        ("Id_ES", "id_es"),
        ("Tipo", "tipo"),
        ("Origen", "origen"),
        ("Id_Tarjeta", "id_tarjeta"),
        ("Id_Cliente", "id_cliente"),
        ("Fecha", "fecha"),
        ("Resultado", "resultado"),
        ("Id_Controlador", "id_controlador"),
        ("Id_Acceso", "id_acceso"),
        ("Observacion", "observacion"),
        ("tipo_reg", "tipo_registro"),
        ("Id_CD_Motivo", "id_cd_motivo"),
        ("Flag_Permite_Paso", "flag_permite_paso"),
        ("Fecha_Paso_Permitido", "fecha_paso_permitido"),
        ("Id_Controlador_Paso_Permitido", "id_controlador_paso_permitido"),
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        if config is None:
            config = getattr(settings, "MSSQL_ACCESS_LOG", {})
        self.config = config
        self._validate_config()

    def _validate_config(self) -> None:
        if not self.config.get("ENABLED", False):
            raise ExternalAccessLogError(
                "La consulta de ingresos externos está deshabilitada en la configuración."
            )
        if pyodbc is None:
            raise ExternalAccessLogError(
                "El paquete pyodbc no está disponible. Instálelo para consultar MSSQL."
            )
        required = ["HOST", "DATABASE", "USER", "PASSWORD", "TABLE"]
        missing = [key for key in required if not self.config.get(key)]
        if missing:
            raise ExternalAccessLogError(
                "Faltan parámetros obligatorios para la conexión MSSQL: "
                + ", ".join(missing)
            )

    def _connection_string(self) -> str:
        driver = self.config.get("DRIVER") or "{ODBC Driver 18 for SQL Server}"
        host = self.config["HOST"]
        port = self.config.get("PORT")
        server = f"{host},{port}" if port else host
        params = {
            "DRIVER": driver,
            "SERVER": server,
            "DATABASE": self.config["DATABASE"],
            "UID": self.config["USER"],
            "PWD": self.config["PASSWORD"],
            "TrustServerCertificate": "yes",
        }
        return ";".join(f"{key}={value}" for key, value in params.items() if value) + ";"

    def _build_query(self, limit: int) -> str:
        column_list = ", ".join(original for original, _ in self.FIELDS)
        table = self.config["TABLE"]
        return (
            f"SELECT TOP {limit} {column_list} "
            f"FROM {table} ORDER BY Fecha DESC"
        )

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def _serialize_rows(self, rows: Iterable[Iterable[Any]]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for row in rows:
            entry: dict[str, Any] = {}
            for (original, alias), value in zip(self.FIELDS, row):
                entry[alias] = self._serialize_value(value)
            entries.append(entry)
        return entries

    def fetch_latest(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Recupera los últimos ingresos ordenados por fecha descendente."""

        if limit is None:
            limit_value = self.config.get("DEFAULT_LIMIT", 10)
        else:
            limit_value = limit
        if not isinstance(limit_value, int) or limit_value <= 0:
            raise ExternalAccessLogError("El parámetro limit debe ser un entero positivo.")

        query = self._build_query(limit_value)
        connection_string = self._connection_string()
        try:
            connection = pyodbc.connect(connection_string)  # type: ignore[union-attr]
        except Exception as exc:  # pragma: no cover - dependiente del controlador
            raise ExternalAccessLogError(
                "No se pudo establecer la conexión con MSSQL: " + str(exc)
            ) from exc

        try:
            cursor = connection.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
        except Exception as exc:  # pragma: no cover - dependiente del controlador
            raise ExternalAccessLogError(
                "Error al ejecutar la consulta en MSSQL: " + str(exc)
            ) from exc
        finally:
            try:
                connection.close()
            except Exception:  # pragma: no cover - cierre defensivo
                pass

        return self._serialize_rows(rows)


class ExternalAccessLogSynchronizer:
    """Sincroniza registros desde el servicio externo hacia la base local."""

    def __init__(
        self,
        service: ExternalAccessLogService | None = None,
        *,
        limit: int | None = None,
        poll_interval: float = 30.0,
    ) -> None:
        self.service = service or ExternalAccessLogService()
        self.limit = limit
        self.poll_interval = poll_interval

    async def sync_once(self) -> int:
        """Obtiene y persiste los últimos movimientos una sola vez."""

        entries = await asyncio.to_thread(self.service.fetch_latest, limit=self.limit)
        if not entries:
            return 0
        return await sync_to_async(self._persist_entries, thread_sensitive=True)(entries)

    async def run_forever(self) -> None:
        """Ejecuta la sincronización en bucle con la periodicidad configurada."""

        while True:
            try:
                synced = await self.sync_once()
                logger.debug("Sincronización externa completada: %s registros", synced)
            except ExternalAccessLogError as exc:
                logger.error("No se pudieron sincronizar los registros externos: %s", exc)
            except Exception:  # pragma: no cover - protección defensiva
                logger.exception("Error inesperado al sincronizar los registros externos")
            await asyncio.sleep(self.poll_interval)

    def _persist_entries(self, entries: list[dict[str, Any]]) -> int:
        now = timezone.now()
        objects: list[ExternalAccessLogEntry] = []
        for entry in entries:
            external_id = self._clean_int(entry.get("id_es"))
            if external_id is None:
                logger.debug("Registro externo descartado por no poseer identificador: %s", entry)
                continue
            objects.append(
                ExternalAccessLogEntry(
                    external_id=external_id,
                    tipo=self._clean_text(entry.get("tipo")),
                    origen=self._clean_text(entry.get("origen")),
                    id_tarjeta=self._clean_text(entry.get("id_tarjeta")),
                    id_cliente=self._clean_int(entry.get("id_cliente")),
                    fecha=self._parse_datetime(entry.get("fecha")) or now,
                    resultado=self._clean_text(entry.get("resultado")),
                    id_controlador=self._clean_int(entry.get("id_controlador")),
                    id_acceso=self._clean_int(entry.get("id_acceso")),
                    observacion=self._clean_text(entry.get("observacion")),
                    tipo_registro=self._clean_text(entry.get("tipo_registro")),
                    id_cd_motivo=self._clean_int(entry.get("id_cd_motivo")),
                    flag_permite_paso=self._clean_text(entry.get("flag_permite_paso")),
                    fecha_paso_permitido=self._parse_datetime(entry.get("fecha_paso_permitido")),
                    id_controlador_paso_permitido=self._clean_int(
                        entry.get("id_controlador_paso_permitido")
                    ),
                    synced_at=now,
                )
            )

        if not objects:
            return 0

        update_fields = [
            "tipo",
            "origen",
            "id_tarjeta",
            "id_cliente",
            "fecha",
            "resultado",
            "id_controlador",
            "id_acceso",
            "observacion",
            "tipo_registro",
            "id_cd_motivo",
            "flag_permite_paso",
            "fecha_paso_permitido",
            "id_controlador_paso_permitido",
            "synced_at",
        ]

        with transaction.atomic():
            ExternalAccessLogEntry.objects.bulk_create(
                objects,
                update_conflicts=True,
                update_fields=update_fields,
                unique_fields=["external_id"],
            )

        return len(objects)

    @staticmethod
    def _clean_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _clean_int(value: Any) -> int | None:
        if value in ("", None):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            dt = parse_datetime(str(value))
            if dt is None:
                try:
                    dt = datetime.fromisoformat(str(value))
                except ValueError:
                    return None
        if timezone.is_naive(dt):
            return timezone.make_aware(dt, timezone.get_current_timezone())
        return dt


__all__ = [
    "ExternalAccessLogService",
    "ExternalAccessLogError",
    "ExternalAccessLogSynchronizer",
]

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

from access_control.models.models import ExternalAccessLogEntry


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
            #"TrustServerCertificate": "yes",
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


class ClientLookupError(RuntimeError):
    """Error genérico al consultar clientes en MSSQL."""


class MSSQLClientLookupService:
    """Consulta clientes por DNI en MSSQL para completar datos faltantes en local."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        if config is None:
            config = getattr(settings, "MSSQL_CLIENT_LOOKUP", {})
        self.config = config
        self._validate_config()

    def _validate_config(self) -> None:
        if not self.config.get("ENABLED", False):
            raise ClientLookupError("La búsqueda de clientes en MSSQL está deshabilitada.")
        if pyodbc is None:
            raise ClientLookupError("El paquete pyodbc no está disponible para buscar clientes en MSSQL.")
        required = ["HOST", "DATABASE", "USER", "PASSWORD", "TABLE"]
        missing = [key for key in required if not self.config.get(key)]
        if missing:
            raise ClientLookupError(
                "Faltan parámetros obligatorios para la búsqueda de clientes en MSSQL: "
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
        }
        return ";".join(f"{key}={value}" for key, value in params.items() if value) + ";"

    def fetch_by_dni(self, dni: int) -> dict[str, Any] | None:
        query = f"SELECT TOP 1 Id_Cliente, Doc_Nro, Ult_Cuota_Paga FROM {self.config['TABLE']} WHERE Doc_Nro = ? ORDER BY Ult_Cuota_Paga DESC"
        try:
            connection = pyodbc.connect(self._connection_string())  # type: ignore[union-attr]
        except Exception as exc:  # pragma: no cover
            raise ClientLookupError("No se pudo conectar a MSSQL para buscar cliente: " + str(exc)) from exc

        try:
            cursor = connection.cursor()
            cursor.execute(query, dni)
            row = cursor.fetchone()
        except Exception as exc:  # pragma: no cover
            raise ClientLookupError("Error al consultar cliente en MSSQL: " + str(exc)) from exc
        finally:
            try:
                connection.close()
            except Exception:  # pragma: no cover
                pass

        if not row:
            return None

        ult_cuota_paga = row[2]
        if isinstance(ult_cuota_paga, datetime):
            ult_cuota_value = ult_cuota_paga
        elif ult_cuota_paga:
            ult_cuota_value = parse_datetime(str(ult_cuota_paga))
        else:
            ult_cuota_value = None

        if ult_cuota_value and timezone.is_naive(ult_cuota_value):
            ult_cuota_value = timezone.make_aware(ult_cuota_value, timezone.get_current_timezone())

        return {
            "id_cliente": int(row[0]) if row[0] is not None else None,
            "doc_nro": int(row[1]) if row[1] is not None else dni,
            "ult_cuota_paga": ult_cuota_value,
        }


class AccessCheckError(RuntimeError):
    """Error genérico al verificar si un socio puede ingresar por un acceso."""


class MSSQLAccessCheckService:
    """Verificación rápida de habilitación de acceso, alternativa a CP_SCA_RegistrarAcceso.

    Replica el orden real de habilitaciones del stored procedure (master, UCP,
    contrato, categoría, producto propio, producto por titular) más los 2
    rechazos más críticos (persona inactiva, vencimientos) y UCP obligatoria,
    reusando las mismas funciones escalares de xsys_geba para garantizar el
    mismo resultado que el SP original. NO evalúa antipassback, foto ni turno
    (dependen del estado físico del molinete/lector, no de datos del socio),
    ni las habilitaciones por evento, kit de productos o categoría/producto
    por horario — para esos casos hay que usar CP_SCA_RegistrarAcceso completo.
    """

    MOTIVOS = {
        "persona_inactiva": (104, "Persona inactiva"),
        "vencimiento": (105, "Rechazo por vencimiento"),
        "ucp_obligatoria": (309, "La persona no posee UCP al día"),
        "master": (202, "Acceso Master"),
        "ucp": (203, "La persona posee UCP al día"),
        "contrato": (204, "Habilitado por tipo de contrato"),
        "categoria": (205, "Habilitado por categoría de socio"),
        "producto": (206, "Habilitado por producto comprado"),
        "producto_titular": (207, "Habilitado por producto comprado por el titular"),
        "sin_habilitacion": (112, "No cumple ninguna condición habilitante"),
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        if config is None:
            config = getattr(settings, "MSSQL_ACCESS_CHECK", {})
        self.config = config
        self._validate_config()

    def _validate_config(self) -> None:
        if not self.config.get("ENABLED", False):
            raise AccessCheckError(
                "La verificación de acceso está deshabilitada en la configuración."
            )
        if pyodbc is None:
            raise AccessCheckError(
                "El paquete pyodbc no está disponible. Instálelo para consultar MSSQL."
            )
        required = ["HOST", "DATABASE", "USER", "PASSWORD"]
        missing = [key for key in required if not self.config.get(key)]
        if missing:
            raise AccessCheckError(
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
        }
        return ";".join(f"{key}={value}" for key, value in params.items() if value) + ";"

    def _connect(self):
        try:
            return pyodbc.connect(self._connection_string())  # type: ignore[union-attr]
        except Exception as exc:  # pragma: no cover - dependiente del controlador
            raise AccessCheckError(
                "No se pudo establecer la conexión con MSSQL: " + str(exc)
            ) from exc

    @staticmethod
    def _scalar(cursor, sql: str, params: tuple) -> Any:
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        except Exception as exc:  # pragma: no cover - dependiente del controlador
            raise AccessCheckError("Error al ejecutar la consulta en MSSQL: " + str(exc)) from exc
        return row[0] if row else None

    def resolve_id_cliente(self, *, identifier_type: str, identifier_value: str) -> int | None:
        """Resuelve doc_nro/id_cliente/credencial a Id_Cliente, sin evaluar ningún acceso."""

        connection = self._connect()
        try:
            cursor = connection.cursor()
            return self._resolve_id_cliente(cursor, identifier_type, identifier_value)
        finally:
            try:
                connection.close()
            except Exception:  # pragma: no cover - cierre defensivo
                pass

    def _resolve_id_cliente(self, cursor, identifier_type: str, identifier_value: str) -> int | None:
        if identifier_type == "id_cliente":
            try:
                id_cliente = int(identifier_value)
            except (TypeError, ValueError):
                raise AccessCheckError("id_cliente debe ser numérico.")
            return self._scalar(
                cursor, "SELECT Id_Cliente FROM Clientes WHERE Id_Cliente = ?", (id_cliente,)
            )
        if identifier_type == "doc_nro":
            try:
                doc_nro = int(identifier_value)
            except (TypeError, ValueError):
                raise AccessCheckError("doc_nro debe ser numérico.")
            return self._scalar(
                cursor,
                "SELECT TOP 1 Id_Cliente FROM Clientes WHERE Doc_Nro = ? "
                "ORDER BY Activo DESC, Ult_Cuota_Paga DESC",
                (doc_nro,),
            )
        if identifier_type == "credencial":
            return self._scalar(
                cursor,
                "SELECT TOP 1 Id_Cliente FROM Clientes "
                "WHERE UPPER(LTRIM(RTRIM(Credencial_Nro))) = UPPER(LTRIM(RTRIM(?)))",
                (identifier_value,),
            )
        raise AccessCheckError(
            "identifier_type debe ser 'doc_nro', 'id_cliente' o 'credencial'."
        )

    def _resolve_id_acceso(self, cursor, id_acceso: int | None, id_controlador: int | None) -> int | None:
        if id_acceso is not None:
            return id_acceso
        return self._scalar(cursor, "SELECT dbo.CF_SCA_IdAcceso(?)", (id_controlador,))

    def _producto_habilita(
        self, cursor, id_cliente_a_facturar: int, id_acceso: int, fecha, solo_titular: bool
    ) -> dict[str, Any] | None:
        valida_en_titular_clause = "= 1" if solo_titular else "IN (0,1)"
        sql = f"""
            SELECT TOP 1 CI.Id_Producto, ISNULL(PR.Descripcion_Resumida, CI.Id_Producto)
            FROM Cbtes_Items CI
            JOIN Cbtes CB ON CI.Id_Trans = CB.Id_Trans
            JOIN Cbtes_Tipos CT ON CT.Id_Tipo_Cbte = CB.Id_Tipo_Cbte
            JOIN CD_Accesos_Prod CA ON CA.Id_Producto = CI.Id_Producto AND CA.Id_Acceso = ?
            JOIN Productos PR ON PR.Id_Producto = CA.Id_Producto
            WHERE CI.Id_Cliente = ?
            AND ((CT.Compromete_Factura = 1 AND CB.Id_Estado_Cbte IN (4,2))
                 OR (CB.Id_Estado_Cbte IN (1) AND CI.Imp_Final = 0))
            AND dbo.CF_NC_A_FC(CB.Id_Trans) = 0
            AND ISNULL(CA.Valida_En_Titular,0) {valida_en_titular_clause}
            AND ISNULL(CA.Flag_Consumible,0) = 0
            AND (
                (ISNULL(PR.Flag_Mes,0) = 0 AND ISNULL(PR.Flag_Periodo,0) = 0)
                OR (ISNULL(PR.Flag_Mes,0) = 1 AND CONVERT(date, ?) <= DATEADD(DAY, ISNULL(CA.Dias_Gracia,0),
                    DATEADD(DAY, 1, EOMONTH(DATEADD(MONTH, ISNULL(CA.Meses_Gracia,0), CONVERT(date, CI.Fecha_QA)), -1))))
                OR (ISNULL(PR.Flag_Periodo,0) = 1 AND CI.Fecha_QA < DATEADD(DAY, 1, CONVERT(date, ?))
                    AND DATEADD(DAY, CA.Dias_Gracia, CI.Fecha_Venc) > DATEADD(DAY, -1, CONVERT(date, ?)))
            )
        """
        try:
            cursor.execute(sql, (id_acceso, id_cliente_a_facturar, fecha, fecha, fecha))
            row = cursor.fetchone()
        except Exception as exc:  # pragma: no cover - dependiente del controlador
            raise AccessCheckError("Error al validar producto comprado en MSSQL: " + str(exc)) from exc
        if not row:
            return None
        return {"id_producto": row[0], "descripcion": row[1]}

    def check_access(
        self,
        *,
        identifier_type: str,
        identifier_value: str,
        id_acceso: int | None = None,
        id_controlador: int | None = None,
        fecha: datetime | None = None,
    ) -> dict[str, Any]:
        """Determina si un socio puede ingresar por un acceso, sin escribir en MSSQL."""

        if id_acceso is None and id_controlador is None:
            raise AccessCheckError("Debe indicar id_acceso o id_controlador.")

        fecha = fecha or datetime.now()
        connection = self._connect()
        try:
            cursor = connection.cursor()

            resolved_id_acceso = self._resolve_id_acceso(cursor, id_acceso, id_controlador)
            if not resolved_id_acceso:
                raise AccessCheckError("No se pudo resolver el acceso indicado.")

            cursor.execute(
                "SELECT Id_Acceso, Descripcion, Activo, ISNULL(Flag_Ult_Cuota_Paga,0), ISNULL(Flag_Evento,0) "
                "FROM CD_Accesos WHERE Id_Acceso = ?",
                (resolved_id_acceso,),
            )
            acceso_row = cursor.fetchone()
            if not acceso_row or not acceso_row[2]:
                raise AccessCheckError(f"El acceso {resolved_id_acceso} no existe o está inactivo.")
            _, acceso_descripcion, _, flag_ucp, flag_evento = acceso_row

            id_cliente = self._resolve_id_cliente(cursor, identifier_type, identifier_value)
            if not id_cliente:
                return {
                    "found": False,
                    "id_acceso": resolved_id_acceso,
                    "acceso_descripcion": acceso_descripcion,
                }

            cursor.execute(
                "SELECT Razon_Social, ISNULL(Activo,0), Id_Tipo_Cli, ISNULL(Id_Cliente_Ref,0) "
                "FROM Clientes WHERE Id_Cliente = ?",
                (id_cliente,),
            )
            cliente_row = cursor.fetchone()
            razon_social, activo, _id_tipo_cli, id_cliente_ref = cliente_row

            result: dict[str, Any] = {
                "found": True,
                "id_cliente": id_cliente,
                "razon_social": (razon_social or "").strip(),
                "id_acceso": resolved_id_acceso,
                "acceso_descripcion": acceso_descripcion,
            }

            def resolved(can_enter: bool, motivo_key: str, detalle: str = "") -> dict[str, Any]:
                motivo_code, motivo_desc = self.MOTIVOS[motivo_key]
                result.update(
                    {
                        "can_enter": can_enter,
                        "motivo_code": motivo_code,
                        "motivo": motivo_desc,
                        "detalle": detalle,
                    }
                )
                return result

            if flag_evento:
                raise AccessCheckError(
                    "Este acceso es de tipo evento (Flag_Evento=1); esta verificación rápida "
                    "no cubre tickets/entradas de evento. Use CP_SCA_RegistrarAcceso completo."
                )

            # --- Rechazos críticos ---
            if not activo:
                return resolved(False, "persona_inactiva")

            vencimiento = self._scalar(
                cursor,
                "SELECT dbo.CF_SCA_ValidarVencimientosPersona(?, ?, ?)",
                (id_cliente, resolved_id_acceso, fecha),
            )
            if vencimiento:
                descripcion = self._scalar(
                    cursor,
                    "SELECT SUBSTRING(RTRIM(LTRIM(Descripcion)), 1, 16) FROM Clientes_Venc_Tipos WHERE Id_Tipo_Venc = ?",
                    (vencimiento,),
                )
                return resolved(False, "vencimiento", (descripcion or "").strip())

            if flag_ucp == 2:
                ucp = self._scalar(
                    cursor,
                    "SELECT dbo.CF_SCA_ValidarUltCuotaPaga(?, ?, ?)",
                    (id_cliente, resolved_id_acceso, fecha),
                )
                if not ucp:
                    return resolved(False, "ucp_obligatoria")

            # --- Habilitaciones ---
            master = self._scalar(cursor, "SELECT dbo.CF_SCA_ValidarMaster(?)", (id_cliente,))
            if master:
                return resolved(True, "master")

            if flag_ucp == 1:
                ucp = self._scalar(
                    cursor,
                    "SELECT dbo.CF_SCA_ValidarUltCuotaPaga(?, ?, ?)",
                    (id_cliente, resolved_id_acceso, fecha),
                )
                if ucp:
                    return resolved(True, "ucp")

            id_contrato = self._scalar(
                cursor,
                "SELECT dbo.CF_SCA_ValidarContratosTipos(?, ?, ?)",
                (id_cliente, resolved_id_acceso, fecha),
            )
            if id_contrato:
                descripcion = self._scalar(
                    cursor,
                    "SELECT RTRIM(LTRIM(CT.Descripcion)) FROM Contratos_Tipos CT "
                    "JOIN Contratos CO ON CT.Id_Tipo_Con = CO.Id_Tipo_Con WHERE CO.Id_Contrato = ?",
                    (id_contrato,),
                )
                return resolved(True, "contrato", (descripcion or "").strip())

            id_tipo_cli_habil = self._scalar(
                cursor,
                "SELECT dbo.CF_SCA_ValidarTipo(?, ?, ?)",
                (id_cliente, resolved_id_acceso, fecha),
            )
            if id_tipo_cli_habil:
                descripcion = self._scalar(
                    cursor,
                    "SELECT RTRIM(LTRIM(Descripcion)) FROM Clientes_Tipos WHERE Id_Tipo_Cli = ?",
                    (id_tipo_cli_habil,),
                )
                return resolved(True, "categoria", (descripcion or "").strip())

            producto = self._producto_habilita(cursor, id_cliente, resolved_id_acceso, fecha, solo_titular=False)
            if producto:
                return resolved(True, "producto", producto["descripcion"])

            if id_cliente_ref:
                producto_titular = self._producto_habilita(
                    cursor, id_cliente_ref, resolved_id_acceso, fecha, solo_titular=True
                )
                if producto_titular:
                    return resolved(True, "producto_titular", producto_titular["descripcion"])

            return resolved(False, "sin_habilitacion")
        finally:
            try:
                connection.close()
            except Exception:  # pragma: no cover - cierre defensivo
                pass


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
    "MSSQLClientLookupService",
    "ClientLookupError",
    "MSSQLAccessCheckService",
    "AccessCheckError",
]

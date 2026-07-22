"""Orquestación del espejo local de xSys (carga inicial + incremental).

Todo el acceso a xSys es de SOLO LECTURA. La cola ``CD_Clientes_Novedades`` se lee
por high-water-mark de ``Id_Novedad`` y jamás se le escribe ``Estado`` (la app
legacy la sigue consumiendo).
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import timedelta
from typing import Any, Iterable, Iterator, Sequence

from django.db import transaction
from django.utils import timezone

from access_control.models.models import ExternalAccessLogEntry

from xsys.models import (
    SyncState,
    XsysAcceso,
    XsysContrato,
    XsysControlador,
    XsysMotivo,
    XsysNovedad,
    XsysSocio,
    XsysSocioFoto,
    XsysWhitelist,
)

from .images import make_thumbnail
from .mssql import get_config, xsys_cursor
from .whitelist import XsysAccessCheckService, compute_habilitacion, persist_whitelist

logger = logging.getLogger(__name__)

# Columnas curadas de Clientes -> atributos de XsysSocio (mismo orden).
SOCIO_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Id_Cliente", "id_cliente"),
    ("Doc_Nro", "doc_nro"),
    ("Apellido", "apellido"),
    ("Nombre", "nombre"),
    ("Razon_Social", "razon_social"),
    ("Sexo", "sexo"),
    ("Fecha_Nac", "fecha_nac"),
    ("Email", "email"),
    ("Activo", "activo"),
    ("Tipo_Persona", "tipo_persona"),
    ("Credencial_Nro", "credencial_nro"),
    ("Ult_Cuota_Paga", "ult_cuota_paga"),
    ("Id_Estado_Cliente", "id_estado_cliente"),
    ("Id_Cliente_Externo", "id_cliente_externo"),
    ("Fecha_Alta", "fecha_alta"),
    ("Fecha_Baja", "fecha_baja"),
)
# SELECT con JOIN a Clientes_Tipos para traer la categoría (última columna).
_SOCIO_SELECT = ", ".join(f"C.{col}" for col, _ in SOCIO_COLUMNS) + ", CT.Descripcion"
_SOCIO_FROM = "Clientes C LEFT JOIN Clientes_Tipos CT ON CT.Id_Tipo_Cli = C.Id_Tipo_Cli"
_SOCIO_TEXT_FIELDS = {
    "apellido", "nombre", "razon_social", "sexo", "email",
    "tipo_persona", "credencial_nro", "id_cliente_externo",
}
_SOCIO_UPDATE_FIELDS = [attr for _, attr in SOCIO_COLUMNS if attr != "id_cliente"] + ["categoria", "synced_at"]

# Columnas de CD_ES -> atributos de ExternalAccessLogEntry (mismo orden).
CDES_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Id_ES", "external_id"),
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
_CDES_SELECT = ", ".join(col for col, _ in CDES_COLUMNS)
_CDES_TEXT = {"tipo", "origen", "id_tarjeta", "resultado", "observacion", "tipo_registro", "flag_permite_paso"}
_CDES_MAXLEN = {
    "tipo": 4, "origen": 8, "id_tarjeta": 64, "resultado": 4,
    "observacion": 255, "tipo_registro": 32, "flag_permite_paso": 4,
}
_CDES_UPDATE_FIELDS = [attr for _, attr in CDES_COLUMNS if attr != "external_id"] + ["synced_at"]

CHUNK = 1000


def _aware(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _chunked(seq: Sequence[Any], size: int = CHUNK) -> Iterator[Sequence[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


class XsysSyncService:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = get_config(config)
        self.batch_size = int(self.config.get("BATCH_SIZE", 1000))
        # Ventana de retención de CD_ES en días (0 = sin límite).
        self.retention_days = int(self.config.get("CD_ES_RETENTION_DAYS", 7) or 0)

    # ---------------------------------------------------------------- lecturas
    def _row_to_socio_kwargs(self, row: Sequence[Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        for (_, attr), value in zip(SOCIO_COLUMNS, row):
            if attr in _SOCIO_TEXT_FIELDS:
                value = (value or "").strip()
            elif attr in ("fecha_nac", "ult_cuota_paga", "fecha_alta", "fecha_baja"):
                value = _aware(value)
            kwargs[attr] = value
        # Última columna del SELECT = Clientes_Tipos.Descripcion (categoría).
        kwargs["categoria"] = (row[len(SOCIO_COLUMNS)] or "").strip()[:100] if len(row) > len(SOCIO_COLUMNS) else ""
        kwargs["synced_at"] = timezone.now()
        return kwargs

    def _max_scalar(self, cursor, sql: str) -> Any:
        cursor.execute(sql)
        row = cursor.fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------ persistencia
    def _upsert_socios(self, rows: Iterable[Sequence[Any]]) -> int:
        objs = [XsysSocio(**self._row_to_socio_kwargs(r)) for r in rows]
        if not objs:
            return 0
        XsysSocio.objects.bulk_create(
            objs,
            update_conflicts=True,
            unique_fields=["id_cliente"],
            update_fields=_SOCIO_UPDATE_FIELDS,
        )
        return len(objs)

    def _upsert_foto(self, id_cliente: int, nro: int, fecha, blob) -> bool:
        """Upsert de una foto. Devuelve True si escribió (cambió), False si no.

        Solo se persisten fotos de socios que efectivamente poseen imagen: si el
        blob viene vacío/NULL no se crea ninguna fila (evita fotos vacías).
        """
        data = bytes(blob) if blob is not None else None
        if not data:
            return False
        sha = hashlib.sha256(data).hexdigest()
        existing = XsysSocioFoto.objects.filter(id_cliente=id_cliente, nro=nro).only("sha256").first()
        if existing and existing.sha256 == sha:
            return False
        XsysSocioFoto.objects.update_or_create(
            id_cliente=id_cliente,
            nro=nro,
            defaults={
                "imagen": data,
                "thumbnail": make_thumbnail(data),
                "sha256": sha,
                "fecha": _aware(fecha),
                "synced_at": timezone.now(),
            },
        )
        return True

    # -------------------------------------------------------------- streams
    def sync_socios_all(self, cursor) -> int:
        # Solo socios activos (Activo=1); filtro server-side.
        cursor.execute(f"SELECT {_SOCIO_SELECT} FROM {_SOCIO_FROM} WHERE C.Activo = 1")
        total = 0
        while True:
            rows = cursor.fetchmany(self.batch_size)
            if not rows:
                break
            with transaction.atomic():
                total += self._upsert_socios(rows)
        return total

    def sync_socios_by_ids(self, cursor, ids: Sequence[int]) -> int:
        total = 0
        for chunk in _chunked(list(ids)):
            placeholders = ",".join("?" for _ in chunk)
            cursor.execute(
                f"SELECT {_SOCIO_SELECT} FROM {_SOCIO_FROM} "
                f"WHERE C.Activo = 1 AND C.Id_Cliente IN ({placeholders})",
                list(chunk),
            )
            rows = cursor.fetchall()
            with transaction.atomic():
                total += self._upsert_socios(rows)
        return total

    def sync_fotos_all(self, cursor) -> int:
        # Solo fotos de socios activos (Activo=1); filtro server-side vía JOIN.
        cursor.execute(
            "SELECT F.Id_Cliente, F.Nro, F.Fecha, F.Foto "
            "FROM Clientes_Fotos F "
            "JOIN Clientes C ON C.Id_Cliente = F.Id_Cliente "
            "WHERE C.Activo = 1"
        )
        written = 0
        while True:
            rows = cursor.fetchmany(self.batch_size)
            if not rows:
                break
            for id_cliente, nro, fecha, blob in rows:
                if self._upsert_foto(id_cliente, nro, fecha, blob):
                    written += 1
        return written

    def sync_fotos_by_ids(self, cursor, ids: Sequence[int]) -> int:
        written = 0
        for chunk in _chunked(list(ids)):
            placeholders = ",".join("?" for _ in chunk)
            cursor.execute(
                f"SELECT Id_Cliente, Nro, Fecha, Foto FROM Clientes_Fotos WHERE Id_Cliente IN ({placeholders})",
                list(chunk),
            )
            for id_cliente, nro, fecha, blob in cursor.fetchall():
                if self._upsert_foto(id_cliente, nro, fecha, blob):
                    written += 1
        return written

    def sync_accesos(self, cursor) -> int:
        """Espejo de CD_Accesos (puertas). Tabla chica: upsert completo."""
        cursor.execute(
            "SELECT Id_Acceso, Descripcion, Descripcion_Corta, Activo FROM CD_Accesos"
        )
        rows = cursor.fetchall()
        now = timezone.now()
        objs = [
            XsysAcceso(
                id_acceso=r[0],
                descripcion=(r[1] or "").strip(),
                descripcion_corta=(r[2] or "").strip(),
                activo=r[3],
                synced_at=now,
            )
            for r in rows
        ]
        if objs:
            XsysAcceso.objects.bulk_create(
                objs,
                update_conflicts=True,
                unique_fields=["id_acceso"],
                update_fields=["descripcion", "descripcion_corta", "activo", "synced_at"],
            )
        return len(objs)

    def sync_controladores(self, cursor) -> int:
        """Espejo de CD_Controladores (molinetes/lectores). Tabla chica: upsert.

        ``ip`` = Intelek_IP (IP configurada del controlador) o, si está vacía,
        Ult_IP (última IP vista). Vacío si xSys no tiene ninguna.
        """
        cursor.execute(
            "SELECT Id_Controlador, Id_Acceso, Descripcion, Tipo, Tipo_Cont, Activo, "
            "Intelek_IP, Ult_IP FROM CD_Controladores"
        )
        rows = cursor.fetchall()
        now = timezone.now()
        objs = [
            XsysControlador(
                id_controlador=r[0],
                id_acceso=r[1],
                descripcion=(r[2] or "").strip()[:100],
                tipo=(r[3] or "").strip(),
                tipo_cont=(r[4] or "").strip(),
                activo=r[5],
                ip=((r[6] or "").strip() or (r[7] or "").strip())[:40],
                synced_at=now,
            )
            for r in rows
        ]
        if objs:
            XsysControlador.objects.bulk_create(
                objs,
                update_conflicts=True,
                unique_fields=["id_controlador"],
                update_fields=["id_acceso", "descripcion", "tipo", "tipo_cont", "activo", "ip", "synced_at"],
            )
        return len(objs)

    def sync_motivos(self, cursor) -> int:
        """Espejo de CD_Motivos (mensajes de pantalla). Tabla chica: upsert completo."""
        cursor.execute(
            "SELECT Id_CD_Motivo, Tipo, Descripcion, Descripcion_Display, "
            "Descripcion_Pantalla, Activo FROM CD_Motivos"
        )
        rows = cursor.fetchall()
        now = timezone.now()
        objs = [
            XsysMotivo(
                id_cd_motivo=r[0],
                tipo=(r[1] or "").strip(),
                descripcion=(r[2] or "").strip()[:200],
                descripcion_display=(r[3] or "").strip(),
                descripcion_pantalla=(r[4] or "").strip(),
                activo=r[5],
                synced_at=now,
            )
            for r in rows
        ]
        if objs:
            XsysMotivo.objects.bulk_create(
                objs,
                update_conflicts=True,
                unique_fields=["id_cd_motivo"],
                update_fields=["tipo", "descripcion", "descripcion_display", "descripcion_pantalla", "activo", "synced_at"],
            )
        return len(objs)

    def _contrato_objs(self, rows):
        now = timezone.now()
        return [
            XsysContrato(
                id_contrato=r[0], id_cliente=r[1], id_tipo_con=r[2],
                descripcion=(r[3] or "").strip()[:50], fecha_alta=_aware(r[4]),
                fecha_hasta=_aware(r[5]), activo=r[6], synced_at=now,
            )
            for r in rows
        ]

    def sync_contratos_all(self, cursor) -> int:
        cursor.execute(
            "SELECT CO.Id_Contrato, CO.Id_Cliente, CO.Id_Tipo_Con, CT.Descripcion, "
            "CO.Fecha_Alta, CO.Fecha_Hasta, CO.Activo "
            "FROM Contratos CO JOIN Contratos_Tipos CT ON CT.Id_Tipo_Con = CO.Id_Tipo_Con "
            "WHERE CO.Activo = 1"
        )
        total = 0
        while True:
            rows = cursor.fetchmany(self.batch_size)
            if not rows:
                break
            objs = self._contrato_objs(rows)
            with transaction.atomic():
                XsysContrato.objects.bulk_create(
                    objs, update_conflicts=True, unique_fields=["id_contrato"],
                    update_fields=["id_cliente", "id_tipo_con", "descripcion", "fecha_alta", "fecha_hasta", "activo", "synced_at"],
                )
            total += len(objs)
        return total

    def sync_contratos_by_ids(self, cursor, ids) -> int:
        total = 0
        for chunk in _chunked(list(ids)):
            ph = ",".join("?" for _ in chunk)
            cursor.execute(
                "SELECT CO.Id_Contrato, CO.Id_Cliente, CO.Id_Tipo_Con, CT.Descripcion, "
                "CO.Fecha_Alta, CO.Fecha_Hasta, CO.Activo "
                "FROM Contratos CO JOIN Contratos_Tipos CT ON CT.Id_Tipo_Con = CO.Id_Tipo_Con "
                f"WHERE CO.Activo = 1 AND CO.Id_Cliente IN ({ph})",
                list(chunk),
            )
            rows = cursor.fetchall()
            with transaction.atomic():
                XsysContrato.objects.filter(id_cliente__in=list(chunk)).delete()
                XsysContrato.objects.bulk_create(self._contrato_objs(rows))
            total += len(rows)
        return total

    def read_novedades(self, cursor, last_id: int, limit: int | None = None) -> list[tuple]:
        top = f"TOP {int(limit)} " if limit else ""
        cursor.execute(
            f"SELECT {top}Id_Novedad, Id_Cliente, Fecha, Estado, Tipo, Nota "
            "FROM CD_Clientes_Novedades WHERE Id_Novedad > ? ORDER BY Id_Novedad",
            (last_id,),
        )
        return cursor.fetchall()

    def _record_novedades(self, rows: Iterable[Sequence[Any]]) -> None:
        objs = [
            XsysNovedad(
                id_novedad=r[0],
                id_cliente=r[1],
                fecha=_aware(r[2]),
                estado_origen=(r[3] or "").strip(),
                tipo=(r[4] or "").strip(),
                nota=(r[5] or "").strip(),
                processed_at=timezone.now(),
            )
            for r in rows
        ]
        if objs:
            XsysNovedad.objects.bulk_create(
                objs,
                update_conflicts=True,
                unique_fields=["id_novedad"],
                update_fields=["id_cliente", "fecha", "estado_origen", "tipo", "nota", "processed_at"],
            )

    # ------------------------------------------------------------- whitelist
    def seed_whitelist_from_suprema(self, cursor, grupo: int | None = None) -> int:
        """Siembra rápida: marca habilitados los Id_Cliente de CD_Lista_Blanca_Suprema.

        ``grupo`` es el Id_Grupo_Suprema (Cuota Social = 2), distinto del Id_Acceso.
        """
        grupo = grupo if grupo is not None else self.config.get("WHITELIST_SUPREMA_GRUPO", 2)
        cursor.execute(
            "SELECT DISTINCT Id_Cliente FROM CD_Lista_Blanca_Suprema "
            "WHERE Id_Grupo_Suprema = ? AND Id_Cliente IS NOT NULL",
            (grupo,),
        )
        ids = [r[0] for r in cursor.fetchall()]
        now = timezone.now()
        objs = [
            XsysWhitelist(
                id_cliente=i,
                habilitado=True,
                motivo="lista_blanca_suprema",
                fecha_calculo=now,
                synced_at=now,
            )
            for i in ids
        ]
        for chunk in _chunked(objs):
            with transaction.atomic():
                XsysWhitelist.objects.bulk_create(
                    chunk,
                    update_conflicts=True,
                    unique_fields=["id_cliente"],
                    update_fields=["habilitado", "motivo", "fecha_calculo", "synced_at"],
                )
        return len(objs)

    def recompute_whitelist(self, ids: Sequence[int], service=None, cursor=None) -> int:
        """Recalcula la habilitación de cada socio con la lógica de acceso.

        Reutiliza UNA sola conexión/cursor MSSQL para TODOS los socios: antes
        ``check_access`` abría (y cerraba) una conexión por socio, generando
        miles de handshakes TLS contra el SQL Server que saturaban la base y la
        red. Si el llamador ya tiene un cursor abierto lo pasa; si no, se abre
        una única conexión para toda la corrida.
        """
        ids = list(ids)
        if not ids:
            return 0
        service = service or XsysAccessCheckService()
        if cursor is not None:
            return self._recompute_whitelist(ids, service, cursor)
        with xsys_cursor(self.config) as own_cursor:
            return self._recompute_whitelist(ids, service, own_cursor)

    def _recompute_whitelist(self, ids: Sequence[int], service, cursor) -> int:
        # Lotes con pausa breve para no saturar la conexión MSSQL.
        batch = int(self.config.get("WHITELIST_BATCH_SIZE", 250) or 0)
        pause = float(self.config.get("WHITELIST_BATCH_PAUSE", 0.15) or 0)
        count = 0
        for i, id_cliente in enumerate(ids):
            try:
                res = compute_habilitacion(id_cliente, service=service, cursor=cursor)
            except Exception as exc:  # pragma: no cover - depende de datos/red
                logger.warning("whitelist recompute fallo cliente %s: %s", id_cliente, exc)
                continue
            persist_whitelist(id_cliente, res)
            count += 1
            if batch and pause and (i + 1) % batch == 0:
                time.sleep(pause)
        return count

    # --------------------------------------------------------------- comandos
    def initial_load(
        self,
        *,
        with_movements: bool = False,
        seed_whitelist: bool = False,
        recompute_whitelist: bool = True,
    ) -> dict[str, int]:
        stats: dict[str, int] = {}
        with xsys_cursor(self.config) as cursor:
            stats["accesos"] = self.sync_accesos(cursor)
            stats["controladores"] = self.sync_controladores(cursor)
            stats["motivos"] = self.sync_motivos(cursor)
            stats["socios"] = self.sync_socios_all(cursor)
            stats["contratos"] = self.sync_contratos_all(cursor)

            # La whitelist se resuelve ANTES de las fotos: solo se descargan las
            # fotos de los socios habilitados (lista blanca), no de todos los
            # activos. Reduce drásticamente el volumen (miles vs. decenas de miles).
            if seed_whitelist:
                stats["whitelist_seed"] = self.seed_whitelist_from_suprema(cursor)
            if recompute_whitelist:
                ids = list(
                    XsysSocio.objects.filter(activo=1).values_list("id_cliente", flat=True)
                )
                stats["whitelist"] = self.recompute_whitelist(ids)

            habilitados = list(
                XsysWhitelist.objects.filter(habilitado=True).values_list("id_cliente", flat=True)
            )
            stats["fotos"] = self.sync_fotos_by_ids(cursor, habilitados)

            # Novedades / fotos: fijar high-water sin backfill.
            max_nov = self._max_scalar(cursor, "SELECT MAX(Id_Novedad) FROM CD_Clientes_Novedades") or 0
            max_foto = self._max_scalar(cursor, "SELECT MAX(Fecha) FROM Clientes_Fotos")
            SyncState.advance("novedades", last_id=max_nov, rows=stats.get("socios", 0))
            SyncState.advance("fotos", last_datetime=_aware(max_foto))
            SyncState.advance("whitelist", last_datetime=timezone.now())

            if with_movements:
                # Backfill real de CD_ES (desde el mark actual, 0 = todo).
                stats["movimientos"] = self.sync_movements(cursor)
            else:
                max_es = self._max_scalar(cursor, "SELECT MAX(Id_ES) FROM CD_ES") or 0
                SyncState.advance("cd_es", last_id=max_es)
        return stats

    def _row_to_cdes_kwargs(self, row: Sequence[Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        for (_, attr), value in zip(CDES_COLUMNS, row):
            if attr in _CDES_TEXT:
                value = (str(value).strip() if value is not None else "")[: _CDES_MAXLEN[attr]]
            elif attr in ("fecha", "fecha_paso_permitido"):
                value = _aware(value)
            kwargs[attr] = value
        kwargs["synced_at"] = timezone.now()
        return kwargs

    def purge_old_movements(self) -> int:
        """Elimina del espejo local los movimientos fuera de la ventana de retención.

        Mantiene ``ExternalAccessLogEntry`` acotado a los últimos
        ``retention_days`` días (default 7). Devuelve la cantidad borrada.
        """
        if not self.retention_days:
            return 0
        cutoff = timezone.now() - timedelta(days=self.retention_days)
        deleted, _ = ExternalAccessLogEntry.objects.filter(fecha__lt=cutoff).delete()
        return deleted

    def sync_movements(self, cursor, *, limit: int | None = None) -> int:
        """Lee CD_ES por high-water Id_ES y persiste en ExternalAccessLogEntry.

        Solo se espejan los movimientos dentro de la ventana de retención
        (``retention_days`` días): la cláusula ``Fecha >=`` evita traer el
        histórico completo en el backfill inicial (Id_ES > 0) y en cada poll.
        """
        last = SyncState.get("cd_es").last_id or 0
        top = f"TOP {int(limit)} " if limit else ""
        fecha_filter = (
            f" AND Fecha >= DATEADD(DAY, -{int(self.retention_days)}, GETDATE())"
            if self.retention_days
            else ""
        )
        cursor.execute(
            f"SELECT {top}{_CDES_SELECT} FROM CD_ES "
            f"WHERE Id_ES > ?{fecha_filter} ORDER BY Id_ES",
            (last,),
        )
        total = 0
        max_id = last
        while True:
            rows = cursor.fetchmany(self.batch_size)
            if not rows:
                break
            objs = [ExternalAccessLogEntry(**self._row_to_cdes_kwargs(r)) for r in rows]
            with transaction.atomic():
                ExternalAccessLogEntry.objects.bulk_create(
                    objs,
                    update_conflicts=True,
                    unique_fields=["external_id"],
                    update_fields=_CDES_UPDATE_FIELDS,
                )
            total += len(objs)
            max_id = max(max_id, max(o.external_id for o in objs))
        if total:
            SyncState.advance("cd_es", last_id=max_id, rows=total)
        return total

    def incremental(self, *, limit: int | None = None, full_whitelist: bool = False) -> dict[str, int]:
        stats: dict[str, int] = {"novedades": 0, "socios": 0, "fotos": 0, "whitelist": 0, "movimientos": 0}
        state = SyncState.start_run("novedades")
        last_id = state.last_id or 0

        with xsys_cursor(self.config) as cursor:
            # Tablas de referencia (chicas) se refrescan en cada corrida.
            stats["accesos"] = self.sync_accesos(cursor)
            stats["controladores"] = self.sync_controladores(cursor)
            stats["motivos"] = self.sync_motivos(cursor)
            rows = self.read_novedades(cursor, last_id, limit=limit)
            stats["novedades"] = len(rows)
            if rows:
                self._record_novedades(rows)
                affected = sorted({r[1] for r in rows if r[1] is not None})
                stats["socios"] = self.sync_socios_by_ids(cursor, affected)
                stats["contratos"] = self.sync_contratos_by_ids(cursor, affected)
                stats["whitelist"] = self.recompute_whitelist(affected)
                # Fotos solo de los afectados que quedaron habilitados (whitelist).
                habilitados = list(
                    XsysWhitelist.objects.filter(
                        id_cliente__in=affected, habilitado=True
                    ).values_list("id_cliente", flat=True)
                )
                stats["fotos"] = self.sync_fotos_by_ids(cursor, habilitados)
                new_last = max(r[0] for r in rows)
            else:
                new_last = last_id

            if full_whitelist:
                ids = list(XsysSocio.objects.filter(activo=1).values_list("id_cliente", flat=True))
                stats["whitelist"] = self.recompute_whitelist(ids)

            # Avanzar high-water mark SOLO tras persistir todo lo anterior.
            SyncState.advance("novedades", last_id=new_last, rows=stats["novedades"])

            stats["movimientos"] = self.sync_movements(cursor)
            stats["movimientos_purgados"] = self.purge_old_movements()
        return stats

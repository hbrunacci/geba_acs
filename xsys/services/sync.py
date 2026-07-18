"""Orquestación del espejo local de xSys (carga inicial + incremental).

Todo el acceso a xSys es de SOLO LECTURA. La cola ``CD_Clientes_Novedades`` se lee
por high-water-mark de ``Id_Novedad`` y jamás se le escribe ``Estado`` (la app
legacy la sigue consumiendo).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Iterable, Iterator, Sequence

from django.db import transaction
from django.utils import timezone

from access_control.models.models import ExternalAccessLogEntry

from xsys.models import (
    SyncState,
    XsysAcceso,
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
_SOCIO_SELECT = ", ".join(col for col, _ in SOCIO_COLUMNS)
_SOCIO_TEXT_FIELDS = {
    "apellido", "nombre", "razon_social", "sexo", "email",
    "tipo_persona", "credencial_nro", "id_cliente_externo",
}
_SOCIO_UPDATE_FIELDS = [attr for _, attr in SOCIO_COLUMNS if attr != "id_cliente"] + ["synced_at"]

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

    # ---------------------------------------------------------------- lecturas
    def _row_to_socio_kwargs(self, row: Sequence[Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        for (_, attr), value in zip(SOCIO_COLUMNS, row):
            if attr in _SOCIO_TEXT_FIELDS:
                value = (value or "").strip()
            elif attr in ("fecha_nac", "ult_cuota_paga", "fecha_alta", "fecha_baja"):
                value = _aware(value)
            kwargs[attr] = value
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
        """Upsert de una foto. Devuelve True si escribió (cambió), False si no."""
        data = bytes(blob) if blob is not None else None
        sha = hashlib.sha256(data).hexdigest() if data else ""
        existing = XsysSocioFoto.objects.filter(id_cliente=id_cliente, nro=nro).only("sha256").first()
        if existing and existing.sha256 == sha:
            return False
        XsysSocioFoto.objects.update_or_create(
            id_cliente=id_cliente,
            nro=nro,
            defaults={
                "imagen": data,
                "thumbnail": make_thumbnail(data) if data else None,
                "sha256": sha,
                "fecha": _aware(fecha),
                "synced_at": timezone.now(),
            },
        )
        return True

    # -------------------------------------------------------------- streams
    def sync_socios_all(self, cursor) -> int:
        cursor.execute(f"SELECT {_SOCIO_SELECT} FROM Clientes")
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
                f"SELECT {_SOCIO_SELECT} FROM Clientes WHERE Id_Cliente IN ({placeholders})",
                list(chunk),
            )
            rows = cursor.fetchall()
            with transaction.atomic():
                total += self._upsert_socios(rows)
        return total

    def sync_fotos_all(self, cursor) -> int:
        cursor.execute("SELECT Id_Cliente, Nro, Fecha, Foto FROM Clientes_Fotos")
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

    def recompute_whitelist(self, ids: Sequence[int], service=None) -> int:
        """Recalcula la habilitación de cada socio con la lógica de acceso."""
        service = service or XsysAccessCheckService()
        count = 0
        for id_cliente in ids:
            try:
                res = compute_habilitacion(id_cliente, service=service)
            except Exception as exc:  # pragma: no cover - depende de datos/red
                logger.warning("whitelist recompute fallo cliente %s: %s", id_cliente, exc)
                continue
            persist_whitelist(id_cliente, res)
            count += 1
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
            stats["motivos"] = self.sync_motivos(cursor)
            stats["socios"] = self.sync_socios_all(cursor)
            stats["fotos"] = self.sync_fotos_all(cursor)

            if seed_whitelist:
                stats["whitelist_seed"] = self.seed_whitelist_from_suprema(cursor)
            if recompute_whitelist:
                ids = list(
                    XsysSocio.objects.filter(activo=1).values_list("id_cliente", flat=True)
                )
                stats["whitelist"] = self.recompute_whitelist(ids)

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

    def sync_movements(self, cursor, *, limit: int | None = None) -> int:
        """Lee CD_ES por high-water Id_ES y persiste en ExternalAccessLogEntry."""
        last = SyncState.get("cd_es").last_id or 0
        top = f"TOP {int(limit)} " if limit else ""
        cursor.execute(
            f"SELECT {top}{_CDES_SELECT} FROM CD_ES WHERE Id_ES > ? ORDER BY Id_ES",
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
            stats["motivos"] = self.sync_motivos(cursor)
            rows = self.read_novedades(cursor, last_id, limit=limit)
            stats["novedades"] = len(rows)
            if rows:
                self._record_novedades(rows)
                affected = sorted({r[1] for r in rows if r[1] is not None})
                stats["socios"] = self.sync_socios_by_ids(cursor, affected)
                stats["fotos"] = self.sync_fotos_by_ids(cursor, affected)
                stats["whitelist"] = self.recompute_whitelist(affected)
                new_last = max(r[0] for r in rows)
            else:
                new_last = last_id

            if full_whitelist:
                ids = list(XsysSocio.objects.filter(activo=1).values_list("id_cliente", flat=True))
                stats["whitelist"] = self.recompute_whitelist(ids)

            # Avanzar high-water mark SOLO tras persistir todo lo anterior.
            SyncState.advance("novedades", last_id=new_last, rows=stats["novedades"])

            stats["movimientos"] = self.sync_movements(cursor)
        return stats

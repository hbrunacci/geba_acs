from __future__ import annotations

from django.db import models
from django.utils import timezone


class SyncState(models.Model):
    """High-water marks por stream de sincronización.

    Streams: ``novedades`` (max Id_Novedad), ``cd_es`` (max Id_ES),
    ``fotos`` (max Fecha), ``whitelist`` (fecha del último recálculo).
    """

    stream = models.CharField(max_length=40, unique=True)
    last_id = models.BigIntegerField(null=True, blank=True)
    last_datetime = models.DateTimeField(null=True, blank=True)
    last_run_started_at = models.DateTimeField(null=True, blank=True)
    last_run_finished_at = models.DateTimeField(null=True, blank=True)
    last_run_ok = models.BooleanField(default=True)
    last_error = models.TextField(blank=True, default="")
    rows_last_run = models.IntegerField(default=0)

    class Meta:
        db_table = "xsys_sync_state"
        verbose_name = "Estado de sincronización xSys"
        verbose_name_plural = "Estados de sincronización xSys"

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"{self.stream} (last_id={self.last_id}, last_dt={self.last_datetime})"

    @classmethod
    def get(cls, stream: str) -> "SyncState":
        obj, _ = cls.objects.get_or_create(stream=stream)
        return obj

    @classmethod
    def start_run(cls, stream: str) -> "SyncState":
        obj = cls.get(stream)
        obj.last_run_started_at = timezone.now()
        obj.save(update_fields=["last_run_started_at"])
        return obj

    @classmethod
    def advance(
        cls,
        stream: str,
        *,
        last_id: int | None = None,
        last_datetime=None,
        rows: int | None = None,
        ok: bool = True,
        error: str = "",
    ) -> "SyncState":
        obj = cls.get(stream)
        if last_id is not None:
            obj.last_id = last_id
        if last_datetime is not None:
            obj.last_datetime = last_datetime
        if rows is not None:
            obj.rows_last_run = rows
        obj.last_run_finished_at = timezone.now()
        obj.last_run_ok = ok
        obj.last_error = error
        obj.save()
        return obj

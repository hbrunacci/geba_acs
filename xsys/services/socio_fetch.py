"""Fetch en segundo plano de socios faltantes en el espejo local desde xSys.

El espejo local (``XsysSocio``) solo trae socios ACTIVOS. Cuando el visor muestra
un paso de un socio que no está en el espejo (p.ej. inactivo, o dado de baja), no
puede resolver su nombre. Se encola su ``id_cliente`` y un worker lo trae de
``Clientes`` (MSSQL) **sin** el filtro de activo, para que el nombre aparezca en
el próximo refresco de la pantalla, sin bloquear la respuesta HTTP.

Mismo diseño que ``foto_fetch``: cola en memoria + 1 worker daemon por lotes, con
``_pending`` (evita encolar dos veces) y ``_attempted`` (negative-cache con TTL,
no reintenta el mismo id por un rato haya o no dato).
"""

from __future__ import annotations

import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

_RETRY_TTL = 3600.0  # 1 hora
_MAX_ATTEMPTED = 100_000
_BATCH = 200

_queue: "queue.Queue[int]" = queue.Queue(maxsize=10_000)
_pending: set[int] = set()
_attempted: dict[int, float] = {}
_lock = threading.Lock()
_worker_started = False
_worker_lock = threading.Lock()


def request_socio(id_cliente: int) -> None:
    """Encola (idempotente) la búsqueda async del socio faltante."""
    if not id_cliente:
        return
    now = time.monotonic()
    with _lock:
        last = _attempted.get(id_cliente)
        if last is not None and (now - last) < _RETRY_TTL:
            return
        if id_cliente in _pending:
            return
        _pending.add(id_cliente)
    _ensure_worker()
    try:
        _queue.put_nowait(id_cliente)
    except queue.Full:
        with _lock:
            _pending.discard(id_cliente)


def request_many(ids) -> None:
    for i in ids:
        request_socio(i)


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_worker_loop, name="xsys-socio-fetch", daemon=True).start()
        _worker_started = True


def _drain_batch(first: int) -> list[int]:
    batch = [first]
    while len(batch) < _BATCH:
        try:
            batch.append(_queue.get_nowait())
        except queue.Empty:
            break
    return batch


def _worker_loop() -> None:
    from django.db import connection as django_conn

    from xsys.services.mssql import XsysConnectionError, xsys_cursor
    from xsys.services.sync import XsysSyncService

    service = XsysSyncService()
    while True:
        first = _queue.get()
        batch = _drain_batch(first)
        try:
            with xsys_cursor(service.config) as cursor:
                traidos = service.sync_socios_by_ids(cursor, batch, only_active=False)
            if traidos:
                logger.info("socio_fetch: %s socio(s) traído(s) para %s id(s)", traidos, len(batch))
        except XsysConnectionError as exc:
            logger.warning("socio_fetch sin conexión a xSys: %s", exc)
        except Exception as exc:  # pragma: no cover - depende de red/datos
            logger.warning("socio_fetch falló (%s ids): %s", len(batch), exc)
        finally:
            now = time.monotonic()
            with _lock:
                for i in batch:
                    _pending.discard(i)
                    _attempted[i] = now
                _prune_attempted_locked(now)
            try:
                django_conn.close()
            except Exception:  # pragma: no cover
                pass
            for _ in batch:
                _queue.task_done()


def _prune_attempted_locked(now: float) -> None:
    if len(_attempted) <= _MAX_ATTEMPTED:
        return
    vencidas = [k for k, ts in _attempted.items() if (now - ts) >= _RETRY_TTL]
    for k in vencidas:
        _attempted.pop(k, None)

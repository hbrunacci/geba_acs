"""Fetch en segundo plano de fotos faltantes desde xSys.

Solo se descargan localmente las fotos de los socios habilitados (whitelist).
Cuando la pantalla/monitor muestra a un socio sin foto local, se encola su
``id_cliente`` y un worker en background lo trae de ``Clientes_Fotos`` (MSSQL)
sin bloquear la respuesta HTTP. La foto aparece en el próximo refresco de la
pantalla.

Diseño (modo nativo, un solo proceso waitress con varios threads):
- Cola en memoria + 1 worker daemon que procesa por lotes.
- ``_pending``: evita encolar dos veces el mismo id mientras está en vuelo.
- ``_attempted``: negative-cache con TTL; no reintenta el mismo id por un rato
  (haya o no foto en xSys), para no martillar por socios sin foto.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

# No reintentar el mismo id por este tiempo (haya o no foto en xSys).
_RETRY_TTL = 3600.0  # 1 hora
_MAX_ATTEMPTED = 100_000  # tope del negative-cache antes de podar
_BATCH = 200  # ids por lote MSSQL

_queue: "queue.Queue[int]" = queue.Queue(maxsize=10_000)
_pending: set[int] = set()
_attempted: dict[int, float] = {}
_lock = threading.Lock()
_worker_started = False
_worker_lock = threading.Lock()


def request_foto(id_cliente: int) -> None:
    """Encola (idempotente) la búsqueda async de la foto de un socio."""
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
        request_foto(i)


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_worker_loop, name="xsys-foto-fetch", daemon=True).start()
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
    # Import diferido: evita ciclos y trabajo en import-time.
    from django.db import connection as django_conn

    from xsys.services.mssql import XsysConnectionError, xsys_cursor
    from xsys.services.sync import XsysSyncService

    service = XsysSyncService()
    while True:
        first = _queue.get()
        batch = _drain_batch(first)
        try:
            with xsys_cursor(service.config) as cursor:
                written = service.sync_fotos_by_ids(cursor, batch)
            if written:
                logger.info("foto_fetch: %s foto(s) traídas para %s socio(s)", written, len(batch))
        except XsysConnectionError as exc:
            logger.warning("foto_fetch sin conexión a xSys: %s", exc)
        except Exception as exc:  # pragma: no cover - depende de red/datos
            logger.warning("foto_fetch falló (%s ids): %s", len(batch), exc)
        finally:
            now = time.monotonic()
            with _lock:
                for i in batch:
                    _pending.discard(i)
                    _attempted[i] = now
                _prune_attempted_locked(now)
            # Cerrar la conexión Django de este thread para no acumular handles.
            try:
                django_conn.close()
            except Exception:  # pragma: no cover
                pass
            for _ in batch:
                _queue.task_done()


def _prune_attempted_locked(now: float) -> None:
    """Poda entradas vencidas del negative-cache si creció demasiado."""
    if len(_attempted) <= _MAX_ATTEMPTED:
        return
    vencidas = [k for k, ts in _attempted.items() if (now - ts) >= _RETRY_TTL]
    for k in vencidas:
        _attempted.pop(k, None)

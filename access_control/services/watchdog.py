"""Watchdog para acotar operaciones que pueden colgarse (red / equipos).

Los pollers (BioStar, Intelektron) tienen loops con ``try/except`` que reintentan
ante errores, pero eso NO alcanza cuando la llamada de red se **cuelga dentro**
del socket/ctypes sin levantar excepción: el hilo queda bloqueado para siempre y
el ``except`` nunca se ejecuta (visto en prod: biostar_poll trabado 9 días,
intelektron_listener 11 días).

``run_with_deadline`` corre la operación en un hilo daemon y espera con
``join(timeout)``. Si no termina a tiempo, levanta ``WatchdogTimeout`` (que el
loop del poller ya captura y reintenta). El hilo colgado queda abandonado —no se
puede matar un hilo en Python—, pero el loop principal **sigue vivo**; como los
cuelgues son raros y transitorios, ese hilo termina liberándose solo o al
reiniciar el contenedor. Mismo espíritu que el fix de xsys_poll (ef5289a).
"""

from __future__ import annotations

import threading
from typing import Any, Callable


class WatchdogTimeout(Exception):
    """La operación superó el deadline; probable cuelgue de red o de un equipo."""


def run_with_deadline(fn: Callable[..., Any], timeout_seconds: float, /, *args, **kwargs) -> Any:
    """Ejecuta ``fn(*args, **kwargs)`` con un límite duro de ``timeout_seconds``.

    Devuelve lo que devuelva ``fn``. Re-lanza en el hilo principal cualquier
    excepción que ``fn`` haya levantado. Si ``fn`` no termina a tiempo, levanta
    ``WatchdogTimeout``.
    """
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - se re-lanza en el hilo principal
            box["error"] = exc
        finally:
            # Cerrar conexiones DB que el worker haya abierto, para no fugarlas
            # (no-op si no abrió ninguna).
            try:
                from django.db import connections

                connections.close_all()
            except Exception:  # pragma: no cover - defensivo
                pass

    worker = threading.Thread(target=_target, name="poller-watchdog", daemon=True)
    worker.start()
    worker.join(timeout_seconds)

    if worker.is_alive():
        raise WatchdogTimeout(
            f"la operación no terminó en {timeout_seconds:g}s; se abandona el ciclo y se reintenta"
        )
    if "error" in box:
        raise box["error"]
    return box.get("value")

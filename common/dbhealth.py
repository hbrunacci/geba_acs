"""Salud de las conexiones a la base en procesos de larga vida.

Por qué existe
--------------
Los servicios que corren en bucle (pollers, barridas, listeners) comparten un
modo de falla que ya nos costó caro tres veces: Postgres cierra la conexión por
inactividad —o queda inutilizable tras un error— y Django sigue devolviendo el
mismo objeto muerto. Cada vuelta del bucle falla con ``connection already
closed``, el ``except`` reconecta lo que cree que se cayó (MSSQL, BioStar) pero
NO la conexión de Django, y el proceso queda girando en falso: el contenedor
figura ``Up`` y no ingiere nada.

Pasó el 12-08-2026 con ``biostar_poll`` (37 h sin eventos faciales) y con
``xsys_poll`` (12 h sin movimientos de CD_ES); los visores se veían vacíos.

Regla: **todo bucle infinito llama a ``reset_db_connections()`` en su manejador
de errores.** Es barato —Django reabre de forma perezosa en el próximo uso— y
convierte un cuelgue permanente en un reintento que se recupera solo.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def reset_db_connections() -> None:
    """Descarta las conexiones a la base; Django las reabre al próximo uso.

    Se cierra a lo bruto en vez de usar sólo ``close_old_connections()`` porque
    esa función únicamente descarta las vencidas o las que Django ya sabe rotas,
    y el caso que nos interesa es justamente aquel en que no se dio cuenta.
    """
    try:
        from django.db import connections

        for conn in connections.all():
            try:
                conn.close()
            except Exception:  # pragma: no cover - defensivo
                pass
    except Exception as exc:  # pragma: no cover - defensivo
        logger.warning("no se pudieron resetear las conexiones a la base: %s", exc)

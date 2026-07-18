"""Conexión MSSQL dedicada del espejo xSys.

Consolida en un solo lugar la construcción de la cadena de conexión (que en el
resto del proyecto está duplicada) y agrega lo que allí falta y acá es
obligatorio: ``Encrypt=no;TrustServerCertificate=yes`` (si no, el handshake TLS
contra el puerto 49331 falla) más timeouts de login y de query.

Es estrictamente de solo lectura: quien lo use debe emitir únicamente
``SELECT`` / ``EXEC`` de funciones de lectura. La app legacy comparte la base.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from django.conf import settings

try:  # pragma: no cover - depende del entorno
    import pyodbc  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - cubierto por prueba negativa
    pyodbc = None  # type: ignore


class XsysConnectionError(RuntimeError):
    """Error al conectar/consultar la base MSSQL de xSys."""


def get_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    return config if config is not None else getattr(settings, "MSSQL_XSYS", {})


def xsys_connection_string(config: dict[str, Any] | None = None) -> str:
    cfg = get_config(config)
    driver = cfg.get("DRIVER") or "{ODBC Driver 18 for SQL Server}"
    host = cfg["HOST"]
    port = cfg.get("PORT")
    server = f"{host},{port}" if port else host
    params = {
        "DRIVER": driver,
        "SERVER": server,
        "DATABASE": cfg["DATABASE"],
        "UID": cfg["USER"],
        "PWD": cfg["PASSWORD"],
        "Encrypt": cfg.get("ENCRYPT", "no"),
        "TrustServerCertificate": cfg.get("TRUST_SERVER_CERTIFICATE", "yes"),
        "LoginTimeout": cfg.get("LOGIN_TIMEOUT", 15),
    }
    return ";".join(f"{key}={value}" for key, value in params.items() if value not in (None, "")) + ";"


def _validate(cfg: dict[str, Any]) -> None:
    if not cfg.get("ENABLED", False):
        raise XsysConnectionError("La integración xSys está deshabilitada (MSSQL_XSYS_ENABLED=0).")
    if pyodbc is None:
        raise XsysConnectionError("El paquete pyodbc no está disponible. Instálelo para consultar MSSQL.")
    missing = [k for k in ("HOST", "DATABASE", "USER", "PASSWORD") if not cfg.get(k)]
    if missing:
        raise XsysConnectionError("Faltan parámetros de conexión MSSQL_XSYS: " + ", ".join(missing))


def connect(config: dict[str, Any] | None = None):
    """Abre una conexión pyodbc a xSys. El llamador es responsable de cerrarla."""

    cfg = get_config(config)
    _validate(cfg)
    try:
        conn = pyodbc.connect(xsys_connection_string(cfg))
    except Exception as exc:  # pragma: no cover - depende de la red
        raise XsysConnectionError(f"No se pudo conectar a xSys: {exc}") from exc
    query_timeout = cfg.get("QUERY_TIMEOUT")
    if query_timeout:
        try:
            conn.timeout = int(query_timeout)
        except Exception:  # pragma: no cover - defensivo
            pass
    return conn


@contextmanager
def xsys_cursor(config: dict[str, Any] | None = None):
    """Context manager que entrega un cursor y cierra la conexión al salir."""

    conn = connect(config)
    try:
        cursor = conn.cursor()
        yield cursor
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - cierre defensivo
            pass

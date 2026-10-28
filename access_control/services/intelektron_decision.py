"""Decide un paso de molinete Intelektron preguntándole a Clever.

La decisión NO se reimplementa: se llama al mismo ``CP_SCA_RegistrarAcceso``
que usan los otros molinetes del club, con el tag tal cual lo guarda Clever
(hexadecimal de la credencial), el controlador asignado a la placa y la hora de
este host. El SP recorre la cascada completa (persona inactiva, cuota, deuda de
actividades, contratos, productos, invitaciones, antirrebote...) y, si
``graba`` es verdadero, deja la fila en ``CD_ES`` con su motivo, igual que la
app de Clever. Así el visor, los informes y las reglas son los mismos.

Requiere que el login de la aplicación tenga ``EXECUTE`` sobre el SP (al
28/10/2026 no lo tiene: ver ``scripts/xsys_controlador_intelektron_2026-10-28.sql``).
La inserción en ``CD_ES`` la hace el SP bajo encadenamiento de propiedad, no
hace falta permiso directo sobre la tabla.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from xsys.services import mssql

logger = logging.getLogger(__name__)

SQL_REGISTRAR = (
    "SET NOCOUNT ON; "
    "EXEC dbo.CP_SCA_RegistrarAcceso "
    "@pId_Controlador = ?, @pTipoAcceso = ?, @pFecha = ?, @pFechaEvento = '19000101', "
    "@pTag = ?, @pDoc = 0, @pHue = '', @pCba = '', @pDma = '', "
    "@pFlag_GrabaRegistro = ?, @pFlag_Permite_Paso = 0, @pFlag_Generacion_LB = 0"
)

COLUMNAS_RESPUESTA = ("flag_habilitado", "mensaje", "info", "foto", "foto_bin",
                      "id_cliente", "id_cliente_ref", "tiempo_mensaje")


@dataclass
class Decision:
    habilitado: bool
    mensaje: str
    info: str
    id_cliente: int
    id_cliente_ref: int
    tiempo_mensaje: int
    error: str = ""
    duracion_ms: float = 0.0

    @property
    def resumen(self) -> str:
        estado = "HABILITADO" if self.habilitado else "RECHAZADO"
        extra = f" ({self.error})" if self.error else ""
        return f"{estado} socio={self.id_cliente} '{self.mensaje}'{extra}"


def parse_respuesta(cursor: Any) -> dict[str, Any] | None:
    """Busca, entre los resultsets del SP, el que trae ``Flag_Habilitado``.

    Con ``SET NOCOUNT ON`` en el batch no aparecen los rowcounts, pero el SP
    puede emitir otros SELECT antes; se recorre con ``nextset`` hasta dar con la
    tabla de respuesta.
    """
    while True:
        desc = cursor.description
        if desc:
            cols = [c[0].lower() for c in desc]
            if "flag_habilitado" in cols:
                row = cursor.fetchone()
                if row is None:
                    return None
                return dict(zip(cols, row))
        try:
            if not cursor.nextset():
                return None
        except Exception:  # noqa: BLE001 - fin de resultsets o error del driver
            return None


class DecisorClever:
    """Conexión persistente a xSys para decidir pasos. Segura para un hilo a la vez."""

    def __init__(self, *, config: dict[str, Any] | None = None, query_timeout: int = 4) -> None:
        self._config = dict(config or mssql.get_config())
        self._config.setdefault("QUERY_TIMEOUT", query_timeout)
        self._conn = None
        self._lock = threading.Lock()

    def _connection(self):
        if self._conn is None:
            self._conn = mssql.connect(self._config)
            try:
                self._conn.autocommit = True
            except Exception:  # noqa: BLE001
                pass
        return self._conn

    def cerrar(self) -> None:
        with self._lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def decidir(self, *, id_controlador: int, tipo_acceso: str, tag: str,
                cuando: datetime | None = None, graba: bool = True) -> Decision:
        """Pregunta a Clever. Ante cualquier error devuelve RECHAZADO con el motivo."""
        inicio = datetime.now()
        cuando = cuando or inicio
        params = (int(id_controlador), tipo_acceso, cuando.replace(microsecond=0), tag, 1 if graba else 0)
        with self._lock:
            for intento in (1, 2):
                try:
                    cur = self._connection().cursor()
                    try:
                        cur.execute(SQL_REGISTRAR, params)
                        fila = parse_respuesta(cur)
                    finally:
                        cur.close()
                    break
                except Exception as exc:  # noqa: BLE001 - conexión caída, timeout, permiso
                    logger.warning("CP_SCA_RegistrarAcceso falló (intento %d): %s", intento, exc)
                    self._reset()
                    if intento == 2:
                        return Decision(False, "SIN CONEXION", "", 0, 0, 1, error=str(exc)[:200],
                                        duracion_ms=_ms(inicio))
        if not fila:
            return Decision(False, "SIN RESPUESTA", "", 0, 0, 1, error="el SP no devolvió la tabla de respuesta",
                            duracion_ms=_ms(inicio))
        return Decision(
            habilitado=int(fila.get("flag_habilitado") or 0) == 1,
            mensaje=str(fila.get("mensaje") or "").strip(),
            info=str(fila.get("info") or "").strip(),
            id_cliente=int(fila.get("id_cliente") or 0),
            id_cliente_ref=int(fila.get("id_cliente_ref") or 0),
            tiempo_mensaje=int(fila.get("tiempo_mensaje") or 1),
            duracion_ms=_ms(inicio),
        )

    def _reset(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _ms(inicio: datetime) -> float:
    return (datetime.now() - inicio).total_seconds() * 1000.0

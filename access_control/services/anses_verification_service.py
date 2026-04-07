from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings

from access_control.services.services import ClientLookupError, MSSQLClientLookupService, pyodbc


class AnsesVerificationError(RuntimeError):
    """Error en la integración de verificación ANSES."""


class AnsesVerificationService:
    """Listado de candidatos y ejecución del script ANSES desde frontend."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        if config is None:
            config = getattr(settings, "MSSQL_CLIENT_LOOKUP", {})
        self.config = config
        self.lookup_service = MSSQLClientLookupService(config=config)

    def _connection_string(self) -> str:
        return self.lookup_service._connection_string()

    def fetch_candidates(
        self,
        *,
        min_age: int = 90,
        max_age: int = 120,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if not isinstance(min_age, int) or not isinstance(max_age, int):
            raise AnsesVerificationError("Los parámetros de edad deben ser numéricos.")
        if min_age < 0 or max_age < 0 or min_age > max_age:
            raise AnsesVerificationError("Rango de edades inválido.")
        if not isinstance(limit, int) or limit <= 0:
            raise AnsesVerificationError("El parámetro limit debe ser un entero positivo.")
        if not isinstance(offset, int) or offset < 0:
            raise AnsesVerificationError("El parámetro offset debe ser un entero mayor o igual a cero.")
        if pyodbc is None:
            raise AnsesVerificationError("El paquete pyodbc no está disponible.")

        table = self.config["TABLE"]
        count_query = f"""
        SELECT COUNT(1)
        FROM {table} c
        INNER JOIN Clientes_Tipos ct
            ON ct.Id_Tipo_Cli = c.Id_Tipo_Cli
        WHERE c.Activo = 1
          AND c.Fecha_Nac IS NOT NULL
          AND c.Doc_Nro IS NOT NULL
          AND ct.Descripcion LIKE '%vitalicio%'
          AND DATEDIFF(YEAR, c.Fecha_Nac, CAST(GETDATE() AS date)) BETWEEN ? AND ?
        """
        query = f"""
        SELECT
            c.Id_Cliente,
            c.Doc_Nro,
            c.Nombre,
            c.Apellido,
            c.Sexo,
            c.Fecha_Nac,
            DATEDIFF(YEAR, c.Fecha_Nac, CAST(GETDATE() AS date)) AS Edad
        FROM {table} c
        INNER JOIN Clientes_Tipos ct
            ON ct.Id_Tipo_Cli = c.Id_Tipo_Cli
        WHERE c.Activo = 1
          AND c.Fecha_Nac IS NOT NULL
          AND c.Doc_Nro IS NOT NULL
          AND ct.Descripcion LIKE '%vitalicio%'
          AND DATEDIFF(YEAR, c.Fecha_Nac, CAST(GETDATE() AS date)) BETWEEN ? AND ?
        ORDER BY c.Fecha_Nac ASC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        """
        try:
            connection = pyodbc.connect(self._connection_string())  # type: ignore[union-attr]
            cursor = connection.cursor()
            cursor.execute(count_query, min_age, max_age)
            total_row = cursor.fetchone()
            total = int(total_row[0]) if total_row and total_row[0] is not None else 0
            cursor.execute(query, min_age, max_age, offset, limit)
            rows = cursor.fetchall()
        except Exception as exc:
            raise AnsesVerificationError(f"No se pudo consultar MSSQL: {exc}") from exc
        finally:
            try:
                connection.close()
            except Exception:
                pass

        items: list[dict[str, Any]] = []
        for row in rows:
            fecha_nac = row[5]
            if isinstance(fecha_nac, datetime):
                fecha_nac_value = fecha_nac.date().isoformat()
            elif fecha_nac:
                fecha_nac_value = str(fecha_nac)
            else:
                fecha_nac_value = ""
            items.append(
                {
                    "id_cliente": int(row[0]) if row[0] is not None else None,
                    "doc_nro": int(row[1]) if row[1] is not None else None,
                    "nombre": (row[2] or "").strip(),
                    "apellido": (row[3] or "").strip(),
                    "sexo": (row[4] or "").strip(),
                    "fecha_nac": fecha_nac_value,
                    "edad": int(row[6]) if row[6] is not None else None,
                }
            )
        return {"count": total, "results": items}

    def run_verification(self, dnis: list[int], *, headless: bool = True, no_download: bool = True) -> dict[str, Any]:
        if not dnis:
            raise AnsesVerificationError("Debe enviar al menos un DNI para verificar.")

        script_path = Path(settings.BASE_DIR) / "anses_cuil.py"
        if not script_path.exists():
            raise AnsesVerificationError("No se encontró el script anses_cuil.py.")

        unique_dnis = sorted({int(dni) for dni in dnis if int(dni) > 0})
        if not unique_dnis:
            raise AnsesVerificationError("No se recibieron DNIs válidos.")

        cmd = [sys.executable, str(script_path)]
        if headless:
            cmd.append("--headless")
        if no_download:
            cmd.append("--no-download")
        for dni in unique_dnis:
            cmd.extend(["--dni", str(dni)])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(settings.BASE_DIR),
                timeout=600,
            )
        except subprocess.TimeoutExpired as exc:
            raise AnsesVerificationError(f"Timeout al ejecutar verificación ANSES: {exc}") from exc
        except Exception as exc:
            raise AnsesVerificationError(f"No se pudo ejecutar anses_cuil.py: {exc}") from exc

        return {
            "command": " ".join(cmd),
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "dni_count": len(unique_dnis),
        }

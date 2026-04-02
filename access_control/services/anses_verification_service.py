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

    def fetch_candidates(self, limit: int = 50) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or limit <= 0:
            raise AnsesVerificationError("El parámetro limit debe ser un entero positivo.")
        if pyodbc is None:
            raise AnsesVerificationError("El paquete pyodbc no está disponible.")

        table = self.config["TABLE"]
        query = f"""
        SELECT TOP {limit}
            Id_Cliente,
            Doc_Nro,
            Nombre,
            Apellido,
            Sexo,
            Fecha_Nac,
            DATEDIFF(YEAR, Fecha_Nac, CAST(GETDATE() AS date)) AS Edad
        FROM {table}
        WHERE Activo = 1
          AND Fecha_Nac IS NOT NULL
          AND Doc_Nro IS NOT NULL
          AND DATEDIFF(YEAR, Fecha_Nac, CAST(GETDATE() AS date)) > 90
        ORDER BY Fecha_Nac ASC
        """
        try:
            connection = pyodbc.connect(self._connection_string())  # type: ignore[union-attr]
            cursor = connection.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
        except Exception as exc:
            raise AnsesVerificationError(f"No se pudo consultar MSSQL: {exc}") from exc
        finally:
            try:
                connection.close()
            except Exception:
                pass

        result: list[dict[str, Any]] = []
        for row in rows:
            fecha_nac = row[5]
            if isinstance(fecha_nac, datetime):
                fecha_nac_value = fecha_nac.date().isoformat()
            elif fecha_nac:
                fecha_nac_value = str(fecha_nac)
            else:
                fecha_nac_value = ""
            result.append(
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
        return result

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

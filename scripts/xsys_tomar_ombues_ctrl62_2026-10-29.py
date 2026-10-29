"""Pasa el controlador 62 (Ombúes "Molinete 1" del visor, placa 10.0.0.66) de Clever a geba_acs.

Lo ejecuta EL USUARIO, no el agente (la escritura en xSys está bloqueada para el agente):

    docker compose --project-directory C:/Users/User/desarrollo/geba_acs exec -T web python /app/scripts/xsys_tomar_ombues_ctrl62_2026-10-29.py
    docker compose --project-directory C:/Users/User/desarrollo/geba_acs exec -T web python /app/scripts/xsys_tomar_ombues_ctrl62_2026-10-29.py --volver

Qué hace:
  1. Respalda CD_Controladores en zCD_Controladores_20261029 (si no existe).
  2. UPDATE CD_Controladores SET Activo = 0 WHERE Id_Controlador = 62
     (con --volver: Activo = 1).
La aplicación de Clever (CleverIntelekReiIn) lee los controladores sólo al
arrancar, así que después hay que reiniciarla para que suelte la placa.
"""
import os
import re
import sys

import django

sys.path.insert(0, "/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "acs.settings")
django.setup()

from django.conf import settings  # noqa: E402
from xsys.services.mssql import xsys_cursor  # noqa: E402

ID_CONTROLADOR = 62
RESPALDO = "zCD_Controladores_20261029"
VOLVER = "--volver" in sys.argv
ACTIVO = 1 if VOLVER else 0

cfg = dict(settings.MSSQL_XSYS)
cfg["DATABASE"] = "xsys_geba"
txt = open("/app/.env.bak_20260907_sa", encoding="utf-8", errors="ignore").read()
cfg["USER"] = re.search(r"^MSSQL_XSYS_USER=(.*)$", txt, re.M).group(1).strip().strip("\"'")
cfg["PASSWORD"] = re.search(r"^MSSQL_XSYS_PASSWORD=(.*)$", txt, re.M).group(1).strip().strip("\"'")

with xsys_cursor(cfg) as cur:
    cur.execute("SELECT Id_Controlador, Descripcion, Intelek_IP, Id_Acceso, ISNULL(Activo,0) FROM CD_Controladores WHERE Id_Controlador=?", ID_CONTROLADOR)
    print("antes :", cur.fetchone())
    cur.execute("SELECT COUNT(*) FROM sys.tables WHERE name=?", RESPALDO)
    if cur.fetchone()[0] == 0:
        cur.execute(f"SELECT * INTO {RESPALDO} FROM CD_Controladores")
        print(f"respaldo {RESPALDO} creado")
    else:
        print(f"respaldo {RESPALDO} ya existía, no se toca")
    cur.execute("UPDATE CD_Controladores SET Activo=? WHERE Id_Controlador=?", ACTIVO, ID_CONTROLADOR)
    print("filas actualizadas:", cur.rowcount)
    cur.connection.commit()
    cur.execute("SELECT Id_Controlador, Descripcion, Intelek_IP, Id_Acceso, ISNULL(Activo,0) FROM CD_Controladores WHERE Id_Controlador=?", ID_CONTROLADOR)
    print("después:", cur.fetchone())

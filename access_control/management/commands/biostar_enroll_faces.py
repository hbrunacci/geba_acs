"""Enrolamiento facial masivo en BioStar 2 desde las fotos de xSys.

Toma los socios habilitados (lista blanca Suprema / Cuota Social) con foto que NO
tienen rostro en BioStar, y los enrola (creándolos si no existen), redimensionando la
imagen para esquivar el 500 'stack space' de BioStar con fotos grandes.

Reemplaza el enrolamiento de CleverSoft (detenido). Es reanudable: cada corrida
recalcula quién falta, así que los ya hechos se excluyen solos.

Uso:
    python manage.py biostar_enroll_faces --mode dryrun        # cuenta y muestra, sin escribir
    python manage.py biostar_enroll_faces --mode on            # enrola todo
    python manage.py biostar_enroll_faces --mode on --limit 20 # una tanda
    python manage.py biostar_enroll_faces --mode on --only 275686
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections


class Command(BaseCommand):
    help = "Enrola el rostro (visualFace) de los socios habilitados sin rostro en BioStar, con resize."

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["dryrun", "on"], default="dryrun",
                            help="dryrun (default): solo cuenta/muestra. on: enrola de verdad.")
        parser.add_argument("--limit", type=int, default=0, help="Máx. a procesar (0 = sin límite).")
        parser.add_argument("--delay", type=float, default=0.5, help="Segundos entre enrolamientos (default 0.5).")
        parser.add_argument("--only", type=int, default=None, help="Procesar un solo Id_Cliente.")
        parser.add_argument("--connect-wait", type=float, default=30.0,
                            help="Segundos entre reintentos de conexión a xSys si la VPN se cae (default 30).")
        parser.add_argument("--connect-max-wait", type=float, default=3600.0,
                            help="Máx. segundos esperando que vuelva la VPN antes de abortar (default 3600).")

    def handle(self, *args, **opts):
        from access_control.services.diag_facial import conectar, BIOSTAR_PREFIX_DEFAULT
        from access_control.services import biostar_face_sync as fs

        mode = opts["mode"]
        limit = opts["limit"]
        delay = opts["delay"]
        only = opts["only"]
        connect_wait = opts["connect_wait"]
        connect_max_wait = opts["connect_max_wait"]

        def conectar_espera():
            """conectar() reintentando mientras la VPN a xSys esté caída (no crashea)."""
            from access_control.services.diag_facial import DiagFacialError

            waited = 0.0
            while True:
                try:
                    return conectar()
                except (DiagFacialError, Exception) as exc:
                    if waited >= connect_max_wait:
                        raise
                    self.stdout.write(self.style.WARNING(
                        f"  xSys no accesible ({str(exc)[:80]}); reintento en {connect_wait:.0f}s "
                        f"(esperado {waited:.0f}/{connect_max_wait:.0f}s)"))
                    self.stdout.flush()
                    time.sleep(connect_wait)
                    waited += connect_wait

        self.stdout.write("Consultando candidatos en xSys/BioStar...")
        conn, driver = conectar_espera()
        cur = conn.cursor()
        cand = fs.build_candidates(cur, BIOSTAR_PREFIX_DEFAULT)

        to_enroll = cand["to_enroll"]
        to_create = cand["to_create"]
        if only is not None:
            to_enroll = [w for w in to_enroll if w["id_cliente"] == only]
            to_create = [w for w in to_create if w["id_cliente"] == only]

        # Existentes-sin-rostro primero (más rápido), después las altas.
        work = to_enroll + to_create
        if limit and limit > 0:
            work = work[:limit]

        self.stdout.write(self.style.SUCCESS(
            f"[{driver}] universo habilitados+foto={cand['universe']} | "
            f"a enrolar (existen sin rostro)={len(to_enroll)} | a crear (no existen)={len(to_create)} | "
            f"esta corrida={len(work)} (mode={mode})"
        ))

        if mode == "dryrun":
            muestra = work[:15]
            for w in muestra:
                self.stdout.write("  %-8s %-6s %s" % (
                    w["id_cliente"], "enrol" if w["exists"] else "crear", w["name"][:40]))
            if len(work) > len(muestra):
                self.stdout.write(f"  ... y {len(work) - len(muestra)} más")
            self.stdout.write("DRYRUN: no se escribió nada.")
            conn.close()
            return

        # mode == on
        from access_control.services.biostar2_client import BioStar2Client
        client = BioStar2Client.from_db_and_env()

        counts = {"enrolled": 0, "created": 0, "failed": 0, "no_foto": 0}
        fails = []
        total = len(work)

        def foto_resiliente(cid):
            """Trae la foto reconectando MSSQL si la conexión larga se cayó (VPN/timeout)."""
            nonlocal conn, cur
            for intento in (1, 2):
                try:
                    return fs.fetch_photo(cur, cid)
                except Exception as exc:
                    if intento == 2:
                        raise
                    self.stdout.write(self.style.WARNING(f"  reconectando MSSQL ({str(exc)[:80]})..."))
                    self.stdout.flush()
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn, _drv = conectar_espera()  # espera a que vuelva la VPN
                    cur = conn.cursor()

        for i, w in enumerate(work, 1):
            cid = w["id_cliente"]
            try:
                foto = foto_resiliente(cid)
            except Exception as exc:
                counts["failed"] += 1
                fails.append((cid, f"foto: {exc}"))
                self.stdout.write(self.style.WARNING("  [%d/%d] %s FALLÓ (foto): %s" % (i, total, cid, str(exc)[:80])))
                self.stdout.flush()
                continue
            if not foto:
                counts["no_foto"] += 1
                self.stdout.write("  [%d/%d] %s SIN FOTO" % (i, total, cid))
                continue
            try:
                res = fs.enroll_one(client, id_cliente=cid, jpeg_bytes=foto,
                                    name=w["name"], exists=w["exists"])
            except Exception as exc:  # red/BioStar caído: no cortar la corrida
                res = {"action": "failed", "reason": str(exc)[:150]}
            action = res.get("action", "failed")
            counts[action] = counts.get(action, 0) + 1
            if action == "failed":
                fails.append((cid, res.get("reason", "")))
                self.stdout.write(self.style.WARNING(
                    "  [%d/%d] %s FALLÓ: %s" % (i, total, cid, res.get("reason", ""))))
            else:
                self.stdout.write("  [%d/%d] %s %s (px %s)" % (
                    i, total, cid, action, res.get("maxside")))
            self.stdout.flush()
            if i % 50 == 0:
                close_old_connections()
            if delay:
                time.sleep(delay)

        conn.close()
        self.stdout.write(self.style.SUCCESS(
            "RESUMEN: enrolados=%s creados=%s fallidos=%s sin_foto=%s" % (
                counts["enrolled"], counts["created"], counts["failed"], counts["no_foto"])))
        if fails:
            self.stdout.write("Fallidos (revisar foto manualmente):")
            for cid, reason in fails[:50]:
                self.stdout.write("  %s: %s" % (cid, reason))

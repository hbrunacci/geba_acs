"""Auditoría de consistencia entre las tres fuentes del control de acceso.

    xSys (verdad)  →  whitelist local (espejo)  →  BioStar (lo que decide el facial)

Existe porque "los socios y las cuotas están sincronizados" no puede ser una
afirmación: tiene que ser algo que se mide. Este comando responde, con números,
si alguien puede pasar sin poder o no puede pasar pudiendo, y en qué eslabón se
rompió la cadena.

Devuelve **exit code 1** si encuentra divergencias, para poder colgarlo de una
alarma / tarea programada.

Uso:
    python manage.py acs_consistencia                  # informe completo
    python manage.py acs_consistencia --rapido         # sin recalcular contra xSys
    python manage.py acs_consistencia --max-edad 120   # umbral de frescura, en minutos
    python manage.py acs_consistencia --detalle 40     # cuántos ids listar por categoría
"""

from __future__ import annotations

import datetime
import sys

from django.core.management.base import BaseCommand
from django.utils import timezone


def _es_true(v) -> bool:
    """BioStar devuelve los booleanos como strings ('true'/'false')."""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() == "true"


class Command(BaseCommand):
    help = "Audita la consistencia xSys ↔ whitelist local ↔ BioStar. Exit 1 si hay divergencias."

    def add_arguments(self, parser):
        parser.add_argument("--rapido", action="store_true",
                            help="No recalcula contra xSys (sólo frescura y BioStar).")
        parser.add_argument("--max-edad", type=int, default=120,
                            help="Minutos de antigüedad tolerados en la whitelist (default 120).")
        parser.add_argument("--detalle", type=int, default=20,
                            help="Cuántos ids mostrar por categoría (default 20).")
        parser.add_argument("--muestra", type=int, default=0,
                            help="Comparar contra xSys sólo N socios al azar en vez de todos "
                                 "(0 = todos; la barrida completa tarda ~30 s).")

    def handle(self, *args, **opts):
        problemas = 0
        n = opts["detalle"]

        problemas += self._frescura(opts["max_edad"])
        if not opts["rapido"]:
            problemas += self._vs_xsys(n, opts["muestra"])
        problemas += self._vs_biostar(n)

        self.stdout.write("")
        if problemas:
            self.stdout.write(self.style.ERROR(f"RESULTADO: {problemas} problema(s) de consistencia."))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS("RESULTADO: las tres fuentes coinciden."))

    # ------------------------------------------------------------ 1. frescura
    def _frescura(self, max_edad_min: int) -> int:
        from xsys.models import XsysWhitelist

        self.stdout.write(self.style.MIGRATE_HEADING("1) Frescura de la whitelist local"))
        total = XsysWhitelist.objects.count()
        if not total:
            self.stdout.write(self.style.ERROR("   la whitelist está VACÍA"))
            return 1

        ahora = timezone.now()
        limite = ahora - datetime.timedelta(minutes=max_edad_min)
        viejas = XsysWhitelist.objects.filter(fecha_calculo__lt=limite).count()
        mas_vieja = XsysWhitelist.objects.order_by("fecha_calculo").first().fecha_calculo
        edad = (ahora - mas_vieja).total_seconds() / 60.0
        self.stdout.write(f"   filas: {total} | más antigua: {edad:.0f} min "
                          f"({mas_vieja:%Y-%m-%d %H:%M})")
        if viejas:
            self.stdout.write(self.style.ERROR(
                f"   {viejas} filas superan el umbral de {max_edad_min} min "
                f"→ la barrida completa no está corriendo"))
            return 1
        self.stdout.write(self.style.SUCCESS(f"   todas por debajo de {max_edad_min} min"))
        return 0

    # -------------------------------------------------- 2. whitelist vs. xSys
    def _vs_xsys(self, n: int, muestra: int) -> int:
        import random

        from xsys.models import XsysWhitelist
        from xsys.services.mssql import connect
        from xsys.services.whitelist import whitelist_params
        from xsys.services.whitelist_bulk import compute_habilitacion_bulk, get_acceso_flags

        self.stdout.write(self.style.MIGRATE_HEADING("2) Whitelist local vs. xSys en vivo"))
        local = dict(XsysWhitelist.objects.values_list("id_cliente", "habilitado"))
        ids = sorted(local)
        if muestra and muestra < len(ids):
            random.seed()
            ids = sorted(random.sample(ids, muestra))
            self.stdout.write(f"   (muestra de {len(ids)} socios)")

        id_acceso, _ = whitelist_params()
        conn = connect()
        try:
            cur = conn.cursor()
            flag_ucp, _fe, _d = get_acceso_flags(cur, id_acceso)
            vivo: dict[int, bool] = {}
            for i in range(0, len(ids), 2000):
                trozo = ids[i:i + 2000]
                res = compute_habilitacion_bulk(
                    cur, trozo, id_acceso=id_acceso, flag_ucp=flag_ucp, descripciones=False)
                vivo.update({c: bool(r["habilitado"]) for c, r in res.items()})
        finally:
            try:
                conn.close()
            except Exception:
                pass

        de_mas = sorted(c for c in ids if local.get(c) and not vivo.get(c, False))
        de_menos = sorted(c for c in ids if not local.get(c) and vivo.get(c, False))
        self.stdout.write(f"   evaluados: {len(ids)} | habilitados en vivo: {sum(vivo.values())}")
        if not de_mas and not de_menos:
            self.stdout.write(self.style.SUCCESS("   la whitelist coincide con xSys en el 100 %"))
            return 0
        if de_mas:
            self.stdout.write(self.style.ERROR(
                f"   {len(de_mas)} habilitados DE MÁS (pasarían sin poder): {de_mas[:n]}"))
        if de_menos:
            self.stdout.write(self.style.ERROR(
                f"   {len(de_menos)} habilitados DE MENOS (no pasarían pudiendo): {de_menos[:n]}"))
        return 1

    # ------------------------------------------------------- 3. BioStar
    def _vs_biostar(self, n: int) -> int:
        from access_control.models import BioStarUser
        from access_control.services.biostar_access_state import (
            is_operator_account,
            protected_user_ids,
        )
        from xsys.models import XsysWhitelist

        self.stdout.write(self.style.MIGRATE_HEADING("3) BioStar vs. whitelist (quién abre el facial)"))
        protegidos = protected_user_ids()
        enrolados, operadores = {}, []
        for u in BioStarUser.objects.filter(is_active=True).only("user_id", "raw_payload"):
            payload = u.raw_payload or {}
            if u.user_id in protegidos or is_operator_account(payload):
                operadores.append(u.user_id)
                continue
            enrolados[u.user_id] = payload
        if not enrolados:
            self.stdout.write(self.style.ERROR("   el espejo BioStarUser está vacío"))
            return 1
        if operadores:
            self.stdout.write(f"   cuentas de operador protegidas: {operadores}")

        # Se separa a los que no son socios (p. ej. la cuenta Administrator de
        # BioStar): no tienen habilitación que reflejar y no deben tocarse.
        conocidos = dict(
            XsysWhitelist.objects.filter(id_cliente__in=list(enrolados))
            .values_list("id_cliente", "habilitado")
        )
        ajenos = sorted(set(enrolados) - set(conocidos))
        enrolados = {uid: p for uid, p in enrolados.items() if uid in conocidos}
        hab = {c for c, h in conocidos.items() if h}

        # "Puede pasar según BioStar": ni deshabilitado ni con el acceso vencido.
        def biostar_permite(p: dict) -> bool:
            return not _es_true(p.get("disabled")) and not _es_true(p.get("expired"))

        abren_sin_poder = sorted(
            uid for uid, p in enrolados.items() if uid not in hab and biostar_permite(p))
        no_abren_pudiendo = sorted(
            uid for uid, p in enrolados.items() if uid in hab and not biostar_permite(p))
        sin_rostro = sorted(
            uid for uid, p in enrolados.items()
            if not int(p.get("visual_face_count") or 0) and not int(p.get("face_count") or 0))

        self.stdout.write(f"   enrolados socios: {len(enrolados)} | habilitados: {len(hab)}"
                          + (f" | ajenos al padrón (se ignoran): {len(ajenos)} {ajenos[:n]}" if ajenos else ""))
        problemas = 0
        if abren_sin_poder:
            self.stdout.write(self.style.ERROR(
                f"   {len(abren_sin_poder)} el facial les ABRE y NO deberían pasar: "
                f"{abren_sin_poder[:n]}"))
            problemas = 1
        if no_abren_pudiendo:
            self.stdout.write(self.style.ERROR(
                f"   {len(no_abren_pudiendo)} el facial les NIEGA y SÍ deberían pasar: "
                f"{no_abren_pudiendo[:n]}"))
            problemas = 1
        if sin_rostro:
            self.stdout.write(self.style.WARNING(
                f"   {len(sin_rostro)} enrolados sin rostro cargado (no los reconoce): "
                f"{sin_rostro[:n]}"))
        if not problemas:
            self.stdout.write(self.style.SUCCESS("   BioStar refleja exactamente la habilitación"))
        return problemas

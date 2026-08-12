"""Barrida COMPLETA de la lista blanca contra la lógica en vivo de xSys.

Problema que resuelve
---------------------
La lista blanca sólo se recalculaba para los socios tocados por novedades. Todo
socio cuyo estado cambiaba sin generar novedad quedaba congelado: al 11-08-2026
el 100 % de las 52.216 filas tenía más de un día y el 63 % más de quince, con la
más vieja del 22-07. Resultado medido ese día: 2.348 socios habilitados de más y
35 negados de más, entre ellos gente que xSys sí dejaba pasar.

Qué hace
--------
Recalcula TODOS los socios con la misma cascada de ``check_access`` pero de a
lotes (``whitelist_bulk``): ~4-5 minutos en vez de ~1 hora. Antes de escribir
nada, verifica una muestra contra el camino de a uno; si difiere en un solo
socio, aborta sin tocar la base.

Uso
---
    python manage.py xsys_whitelist_full --dry-run        # informe, no escribe
    python manage.py xsys_whitelist_full                  # una barrida
    python manage.py xsys_whitelist_full --loop --interval 1800
    python manage.py xsys_whitelist_full --push-biostar   # además sincroniza BioStar
"""

from __future__ import annotations

import logging
import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Recalcula la lista blanca COMPLETA contra xSys (set-based) y reporta las diferencias."

    def add_arguments(self, parser):
        parser.add_argument("--batch", type=int, default=2000,
                            help="Socios por query (default 2000).")
        parser.add_argument("--pause", type=float, default=0.2,
                            help="Pausa entre lotes, en segundos (default 0.2).")
        parser.add_argument("--verify", type=int, default=100,
                            help="Socios a verificar contra el camino de a uno antes de escribir "
                                 "(default 100; 0 desactiva la verificación).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Calcula y reporta, pero no escribe la whitelist.")
        parser.add_argument("--limit", type=int, default=None,
                            help="Procesar sólo los primeros N socios (pruebas).")
        parser.add_argument("--push-biostar", action="store_true",
                            help="Tras la barrida, sincroniza en BioStar el estado de los socios "
                                 "cuya habilitación cambió (respeta BIOSTAR_DISABLE_MODE).")
        parser.add_argument("--loop", action="store_true", help="Repetir indefinidamente.")
        parser.add_argument("--interval", type=float, default=1800,
                            help="Con --loop: segundos entre barridas (default 1800).")

    def handle(self, *args, **opts):
        if not opts["loop"]:
            self._run_once(opts)
            return
        while True:
            inicio = time.time()
            try:
                self._run_once(opts)
            except Exception as exc:  # pragma: no cover - servicio de larga vida
                logger.exception("xsys_whitelist_full: barrida falló: %s", exc)
                self.stderr.write(self.style.ERROR(f"barrida falló: {exc}"))
            espera = max(0.0, opts["interval"] - (time.time() - inicio))
            if espera:
                time.sleep(espera)

    # ------------------------------------------------------------------ core
    def _run_once(self, opts):
        import pyodbc  # noqa: F401  (import tardío: sólo existe con el driver)

        from xsys.models import XsysWhitelist
        from xsys.services.mssql import connect
        from xsys.services.whitelist import whitelist_params
        from xsys.services.whitelist_bulk import (
            compute_habilitacion_bulk,
            get_acceso_flags,
            server_now,
            verify_bulk_against_single,
        )

        id_acceso, _ctrl = whitelist_params()
        t0 = time.time()
        # Corriendo con --loop, entre barridas pasan minutos: Postgres cierra la
        # conexión por inactividad y Django reusa la muerta ("connection already
        # closed"). Se descartan las vencidas al empezar y otra vez antes de
        # escribir, porque la parte MSSQL de la barrida dura decenas de segundos.
        close_old_connections()
        # Sin pooling: tras un corte de red pyodbc devuelve conexiones muertas
        # del pool y la barrida entera falla (mismo problema ya visto en el poller).
        pyodbc.pooling = False
        conn = connect()
        try:
            cursor = conn.cursor()
            flag_ucp, _flag_evento, desc_acceso = get_acceso_flags(cursor, id_acceso)

            ids = self._target_ids(cursor, limit=opts["limit"])
            self.stdout.write(
                f"acceso {id_acceso} ({desc_acceso}, Flag_Ult_Cuota_Paga={flag_ucp}) — "
                f"{len(ids)} socios a evaluar"
            )
            if not ids:
                return

            # --- control de equivalencia ANTES de escribir ---
            if opts["verify"]:
                import random

                muestra = random.sample(ids, min(opts["verify"], len(ids)))
                v = verify_bulk_against_single(cursor, muestra, id_acceso=id_acceso)
                if v["difieren"]:
                    for d in v["detalle"]:
                        self.stderr.write(self.style.ERROR(f"  DISCREPANCIA {d}"))
                    raise RuntimeError(
                        f"El cálculo masivo difiere del de a uno en {v['difieren']}/{v['muestra']} "
                        f"socios. NO se escribió nada."
                    )
                self.stdout.write(self.style.SUCCESS(
                    f"verificación masivo vs de-a-uno: {v['coinciden']}/{v['muestra']} idénticas"))

            # --- estado previo, para saber qué cambió ---
            previo = dict(XsysWhitelist.objects.values_list("id_cliente", "habilitado"))

            # Un único instante para toda la barrida, tomado del reloj de xSys
            # (no del contenedor, que corre en UTC): si no, socios evaluados en
            # lotes distintos podrían caer a distinto lado de un corte de gracia.
            fecha = server_now(cursor)
            self.stdout.write(f"fecha de evaluación (reloj de xSys): {fecha:%Y-%m-%d %H:%M:%S}")
            batch = max(1, opts["batch"])
            resultados: dict[int, dict] = {}
            for i in range(0, len(ids), batch):
                trozo = ids[i:i + batch]
                resultados.update(compute_habilitacion_bulk(
                    cursor, trozo, id_acceso=id_acceso, fecha=fecha, flag_ucp=flag_ucp))
                hechos = min(i + batch, len(ids))
                if (i // batch) % 5 == 0 or hechos == len(ids):
                    tr = time.time() - t0
                    self.stdout.write(f"  {hechos}/{len(ids)} ({tr:.0f}s, {hechos/max(tr,1e-9):.0f}/s)")
                if opts["pause"]:
                    time.sleep(opts["pause"])
        finally:
            try:
                conn.close()
            except Exception:
                pass

        # --- diferencias ---
        nuevos_hab = {cid: bool(r["habilitado"]) for cid, r in resultados.items()}
        # Se separan las filas nuevas de los cambios de opinión: sólo estos
        # últimos son "la whitelist estaba mal", y son los que hay que empujar.
        sin_fila = sorted(c for c in nuevos_hab if c not in previo)
        flip_a_si = sorted(c for c, h in nuevos_hab.items() if h and previo.get(c) is False)
        flip_a_no = sorted(c for c, h in nuevos_hab.items() if not h and previo.get(c) is True)
        self.stdout.write(
            f"habilitados: {sum(nuevos_hab.values())} de {len(nuevos_hab)} | "
            f"CORRIGE False→True: {len(flip_a_si)} | CORRIGE True→False: {len(flip_a_no)} | "
            f"filas nuevas: {len(sin_fila)}"
        )
        if flip_a_si[:15]:
            self.stdout.write(f"  se les devuelve el acceso: {flip_a_si[:15]}")
        if flip_a_no[:15]:
            self.stdout.write(f"  se les quita el acceso   : {flip_a_no[:15]}")
        pasan_a_si, pasan_a_no = flip_a_si, flip_a_no

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("--dry-run: no se escribió la whitelist."))
            return

        close_old_connections()
        escritos = self._persist(resultados)
        self.stdout.write(self.style.SUCCESS(
            f"whitelist actualizada: {escritos} filas en {time.time() - t0:.0f}s"))

        if opts["push_biostar"]:
            self._push_biostar()

    # ------------------------------------------------------------- auxiliares
    def _target_ids(self, cursor, *, limit=None) -> list[int]:
        """Socios a evaluar: los activos de xSys MÁS los que ya tienen fila local.

        Los que ya tienen fila se incluyen aunque hoy estén inactivos: si no, un
        socio dado de baja conservaría para siempre su último ``habilitado=True``.
        """
        from xsys.models import XsysWhitelist

        cursor.execute("SELECT Id_Cliente FROM Clientes WHERE ISNULL(Activo,0) = 1")
        ids = {int(r[0]) for r in cursor.fetchall()}
        ids |= set(XsysWhitelist.objects.values_list("id_cliente", flat=True))
        out = sorted(ids)
        return out[:limit] if limit else out

    def _persist(self, resultados: dict[int, dict]) -> int:
        from xsys.models import XsysWhitelist

        now = timezone.now()
        objs = [
            XsysWhitelist(
                id_cliente=cid,
                habilitado=bool(r["habilitado"]),
                motivo_code=r.get("motivo_code"),
                motivo=(r.get("motivo") or "")[:120],
                detalle=(r.get("detalle") or "")[:120],
                id_acceso=r.get("id_acceso"),
                fecha_calculo=now,
                synced_at=now,
            )
            for cid, r in resultados.items()
        ]
        campos = ["habilitado", "motivo_code", "motivo", "detalle", "id_acceso",
                  "fecha_calculo", "synced_at"]
        for i in range(0, len(objs), 1000):
            with transaction.atomic():
                XsysWhitelist.objects.bulk_create(
                    objs[i:i + 1000],
                    update_conflicts=True,
                    unique_fields=["id_cliente"],
                    update_fields=campos,
                )
        return len(objs)

    def _push_biostar(self) -> None:
        """Reconcilia BioStar con la whitelist.

        Se recorren TODOS los enrolados, no sólo los que acaban de cambiar: con
        ``only_divergent`` la comparación es contra el estado real del espejo, así
        que sólo se emiten PUT para los que están mal. Empujar únicamente los
        cambios dejaba sin reintento a cualquiera cuyo PUT hubiera fallado una vez.
        """
        try:
            from access_control.services.biostar_access_state import push_access_state_affected

            res = push_access_state_affected(None, only_divergent=True, max_per_run=5000)
            res.pop("dryrun_disable_ids", None)
            res.pop("dryrun_enable_ids", None)
            self.stdout.write(self.style.SUCCESS(f"BioStar: {res}"))
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("xsys_whitelist_full: push a BioStar falló: %s", exc)
            self.stderr.write(self.style.ERROR(f"BioStar: falló el push: {exc}"))

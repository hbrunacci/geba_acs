"""Detector rápido de cambios de cuota/estado: espejo, lista blanca y facial al día.

Problema que resuelve
---------------------
Hasta ahora un socio que pagaba sólo se actualizaba si xSys generaba una novedad
para él, y si no, quedaba viejo hasta la barrida completa. Dos síntomas medidos
el 14-08-2026:

- El visor de molinetes mostraba una **cuota más vieja que la real** en 8.422 de
  21.858 socios (38,5 %). Todas las diferencias iban en esa dirección: el espejo
  atrasado, nunca adelantado.
- El facial tardaba hasta 15 minutos (el intervalo de la barrida) en volver a
  habilitar al que acababa de pagar.

Cómo
----
Resulta que barrer ``Clientes`` entero pidiendo sólo (Id_Cliente, Ult_Cuota_Paga,
Activo) cuesta **~0,5 s para 223.859 filas**. Siendo tan barato no hace falta
adivinar quién cambió a partir de las novedades: se compara el padrón completo
contra nuestro espejo en cada vuelta y se actúa sólo sobre los que difieren.

Se descartó mirar comprobantes recientes (``Cbtes``) como disparador: su campo
``Fecha`` es la fecha del documento, no la de la transacción — devuelve fechas
futuras y no sirve para detectar actividad.

Para cada socio que cambió: se refresca el espejo, se recalcula su habilitación
con la lógica de xSys y se empuja el estado a BioStar. Con el intervalo por
defecto, pagar y poder entrar por el facial queda por debajo del minuto.

Esto NO reemplaza a ``xsys_whitelist_full``: la barrida completa sigue haciendo
falta para lo que cambia sin tocar ``Ult_Cuota_Paga`` ni ``Activo`` (contratos,
productos, vencimientos) y para los cortes de gracia, que dependen del paso del
tiempo y no de un cambio de dato.

Uso:
    python manage.py xsys_cambios_poll --interval 20
    python manage.py xsys_cambios_poll --once --dry-run
"""

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from common.dbhealth import reset_db_connections

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Detecta cambios de cuota/estado en todo el padrón y actualiza espejo, lista blanca y BioStar."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=20.0,
                            help="Segundos entre barridos (default 20).")
        parser.add_argument("--once", action="store_true", help="Un solo barrido y salir.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Informa los cambios detectados sin escribir ni empujar.")
        parser.add_argument("--max-cambios", type=int, default=20000,
                            help="Si un barrido detecta más cambios que esto, no se aplica: "
                                 "señal de que xSys está a medio escribir (default 20000).")
        parser.add_argument("--reconnect-delay", type=float, default=10.0,
                            help="Espera tras un error antes de reintentar (default 10).")
        parser.add_argument("--no-biostar", action="store_true",
                            help="No empujar el estado a BioStar (sólo espejo y lista blanca).")

    def handle(self, *args, **opts):
        self.stdout.write(self.style.SUCCESS(
            f"Detector de cambios cada {opts['interval']}s. Ctrl-C para salir."))
        try:
            while True:
                try:
                    self._ciclo(opts)
                except Exception as exc:  # pragma: no cover - servicio de larga vida
                    logger.exception("xsys_cambios_poll: %s", exc)
                    self.stderr.write(self.style.ERROR(
                        f"error ({exc}); reintento en {opts['reconnect_delay']}s"))
                    # Si lo que se cayó fue Postgres, sin esto el proceso queda
                    # girando en falso con el contenedor en Up.
                    reset_db_connections()
                    time.sleep(opts["reconnect_delay"])
                    continue
                if opts["once"]:
                    return
                time.sleep(opts["interval"])
        except KeyboardInterrupt:
            self.stdout.write("\nDetector detenido.")

    # ------------------------------------------------------------------ ciclo
    def _ciclo(self, opts):
        import pyodbc

        from xsys.models import XsysSocio
        from xsys.services.mssql import connect
        from xsys.services.sync import XsysSyncService

        pyodbc.pooling = False
        t0 = time.time()
        service = XsysSyncService()
        conn = connect()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT Id_Cliente, Ult_Cuota_Paga, ISNULL(Activo,0) FROM Clientes")
            remoto = {int(r[0]): (r[1], int(r[2])) for r in cursor.fetchall()}

            local = {
                cid: (ucp, act)
                for cid, ucp, act in XsysSocio.objects.values_list(
                    "id_cliente", "ult_cuota_paga", "activo")
            }

            cambiados = self._diferencias(remoto, local)
            if not cambiados:
                return

            if len(cambiados) > opts["max_cambios"]:
                # Un salto así no es tráfico normal: es xSys a medio escribir (o
                # nuestro espejo recién inicializado). Aplicarlo empujaría ruido a
                # los lectores. Se informa y se espera al siguiente barrido.
                self.stderr.write(self.style.ERROR(
                    f"{len(cambiados)} cambios detectados, por encima de --max-cambios "
                    f"({opts['max_cambios']}): no se aplica nada este barrido."))
                return

            self.stdout.write(f"{len(cambiados)} socios con cambio de cuota/estado "
                              f"(detección {time.time() - t0:.1f}s)")
            if opts["dry_run"]:
                self.stdout.write(f"  --dry-run, no se aplica. Ejemplos: {cambiados[:10]}")
                return

            n = service.sync_socios_by_ids(cursor, cambiados, only_active=False)
            self.stdout.write(f"  espejo actualizado: {n}")
            self._recalcular(cursor, cambiados)
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if not opts["no_biostar"]:
            self._push_biostar(cambiados)

    def _diferencias(self, remoto: dict, local: dict) -> list[int]:
        """Ids cuya cuota o estado activo difieren entre xSys y el espejo.

        Se comparan las fechas por día: ``Ult_Cuota_Paga`` es el mes pagado hasta
        (día 1), y el espejo la guarda como datetime con zona.
        """
        from django.utils import timezone

        cambiados: list[int] = []
        for cid, (ucp_r, act_r) in remoto.items():
            en_local = local.get(cid)
            if en_local is None:
                # Sólo interesan los activos que todavía no espejamos; los
                # inactivos que nunca tuvimos no aportan nada al control de acceso.
                if act_r == 1:
                    cambiados.append(cid)
                continue
            ucp_l, act_l = en_local
            if (act_l or 0) != act_r:
                cambiados.append(cid)
                continue
            d_r = ucp_r.date() if ucp_r else None
            d_l = timezone.localtime(ucp_l).date() if ucp_l else None
            if d_r != d_l:
                cambiados.append(cid)
        return cambiados

    def _recalcular(self, cursor, ids: list[int]) -> None:
        from xsys.models import XsysWhitelist
        from xsys.services.whitelist import persist_whitelist, whitelist_params
        from xsys.services.whitelist_bulk import (
            compute_habilitacion_bulk,
            get_acceso_flags,
            server_now,
        )

        id_acceso, _ = whitelist_params()
        flag_ucp, _fe, _d = get_acceso_flags(cursor, id_acceso)
        fecha = server_now(cursor)
        previo = dict(
            XsysWhitelist.objects.filter(id_cliente__in=ids).values_list("id_cliente", "habilitado")
        )
        cambios_hab = 0
        for i in range(0, len(ids), 2000):
            res = compute_habilitacion_bulk(
                cursor, ids[i:i + 2000], id_acceso=id_acceso, fecha=fecha, flag_ucp=flag_ucp)
            for cid, r in res.items():
                persist_whitelist(cid, r)
                if bool(r["habilitado"]) != previo.get(cid):
                    cambios_hab += 1
        self.stdout.write(f"  habilitación recalculada; cambió en {cambios_hab}")

    def _push_biostar(self, ids: list[int]) -> None:
        try:
            from access_control.services.biostar_access_state import push_access_state_affected

            res = push_access_state_affected(ids, only_divergent=True, max_per_run=len(ids) or 1)
            res.pop("dryrun_disable_ids", None)
            res.pop("dryrun_enable_ids", None)
            if res.get("deshabilitados") or res.get("rehabilitados") or res.get("errores"):
                self.stdout.write(self.style.SUCCESS(f"  BioStar: {res}"))
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("xsys_cambios_poll: push a BioStar falló: %s", exc)
            self.stderr.write(self.style.ERROR(f"  BioStar: falló el push: {exc}"))

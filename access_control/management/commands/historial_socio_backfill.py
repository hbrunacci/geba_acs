"""Carga el historial de accesos por socio (``SocioAcceso``) con lo ya ocurrido.

De acá en adelante el historial se escribe solo, en la ingesta. Este comando es
para el pasado, y tiene dos fuentes:

  --local            los espejos que ya están en la base de geba_acs
                     (``ExternalAccessLogEntry`` + ``BiostarAccessEvent``).
                     Instantáneo, pero sólo alcanza la ventana de retención de
                     esos espejos: 7 días de xSys, 2 de los faciales.

  --desde AAAA-MM-DD  lee ``CD_ES`` directo del SQL del club. Es la única forma
                     de tener años de historial. Solo lectura, paginado por
                     ``Id_ES``; se puede cortar y volver a correr sin duplicar.

Los faciales viejos no se pueden recuperar: BioStar es la única fuente con la
identidad del equipo y su log también rota. Lo que sí queda de esos cruces es el
registro que xSys hace por el controlador puente, que entra por ``--desde``.

Ejemplos:
    manage.py historial_socio_backfill --local
    manage.py historial_socio_backfill --desde 2026-01-01
    manage.py historial_socio_backfill --desde 2026-01-01 --hasta 2026-06-30
"""

from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from access_control.services import historial_socio

# Columnas de CD_ES que necesita el historial. No se traen las demás: son 8,6
# millones de filas y cada columna de más se paga en la red.
_COLS = ("Id_ES", "Id_Cliente", "Fecha", "Resultado", "Id_Controlador",
         "Id_Acceso", "Observacion", "Id_CD_Motivo")
_LOTE = 5000


class _Movimiento:
    """Lo mínimo que ``historial_socio.fila_de_movimiento`` necesita de una fila.

    Se evita instanciar ``ExternalAccessLogEntry``: son objetos de un modelo con
    tabla propia y acá no se los quiere guardar ni por accidente.
    """

    __slots__ = ("external_id", "id_cliente", "fecha", "resultado",
                 "id_controlador", "id_acceso", "observacion", "id_cd_motivo",
                 "conflicto_molinete")

    def __init__(self, row):
        (self.external_id, self.id_cliente, fecha, resultado, self.id_controlador,
         self.id_acceso, observacion, self.id_cd_motivo) = row
        if fecha is not None and timezone.is_naive(fecha):
            fecha = timezone.make_aware(fecha, timezone.get_current_timezone())
        self.fecha = fecha
        self.resultado = (resultado or "").strip()
        self.observacion = (observacion or "").strip()
        # El conflicto de paso pendiente se calcula al ingerir en vivo, contra el
        # reloj. Para el pasado no se puede reconstruir sin mentir, así que queda
        # vacío: ausencia de dato, no ausencia de conflicto.
        self.conflicto_molinete = ""


class Command(BaseCommand):
    help = "Puebla el historial de accesos por socio con los movimientos ya ocurridos."

    def add_arguments(self, parser):
        parser.add_argument("--local", action="store_true",
                            help="Cargar desde los espejos locales (rápido, ventana corta).")
        parser.add_argument("--desde", help="Leer CD_ES de xSys desde esta fecha (AAAA-MM-DD).")
        parser.add_argument("--hasta", help="Hasta esta fecha inclusive (AAAA-MM-DD).")

    def handle(self, *args, **opts):
        if not opts["local"] and not opts["desde"]:
            raise CommandError("Indicá --local o --desde AAAA-MM-DD.")
        historial_socio.invalidar_cache()
        if opts["local"]:
            self._desde_espejos()
        if opts["desde"]:
            self._desde_xsys(self._fecha(opts["desde"]), self._fecha(opts["hasta"]))

    @staticmethod
    def _fecha(txt):
        if not txt:
            return None
        try:
            return datetime.date.fromisoformat(txt)
        except ValueError:
            raise CommandError(f"Fecha inválida: {txt!r}. Usá AAAA-MM-DD.")

    # ------------------------------------------------------------- espejos
    def _desde_espejos(self):
        from access_control.models import BiostarAccessEvent
        from access_control.models.models import ExternalAccessLogEntry

        ctx = historial_socio.contexto()
        n = 0
        qs = ExternalAccessLogEntry.objects.filter(id_cliente__gt=0).order_by("external_id")
        for lote in self._por_lotes(qs.iterator(chunk_size=_LOTE)):
            n += historial_socio.registrar_movimientos(lote, ctx)
            self.stdout.write(f"  CD_ES (espejo): {n}")
        f = 0
        fqs = BiostarAccessEvent.objects.filter(id_cliente__gt=0).order_by("id")
        for lote in self._por_lotes(fqs.iterator(chunk_size=_LOTE)):
            f += historial_socio.registrar_faciales(lote, ctx)
            self.stdout.write(f"  faciales (espejo): {f}")
        self.stdout.write(self.style.SUCCESS(
            f"Espejos locales: {n} movimientos y {f} faciales procesados."))

    @staticmethod
    def _por_lotes(it):
        lote = []
        for x in it:
            lote.append(x)
            if len(lote) >= _LOTE:
                yield lote
                lote = []
        if lote:
            yield lote

    # ---------------------------------------------------------------- xSys
    def _desde_xsys(self, desde, hasta):
        from django.conf import settings

        from xsys.services.mssql import connect

        ctx = historial_socio.contexto()
        conn = connect(settings.MSSQL_XSYS)
        conn.timeout = 0
        cur = conn.cursor()

        filtro = "Fecha >= ? AND Id_Cliente > 0"
        params = [desde]
        if hasta:
            filtro += " AND Fecha < ?"
            params.append(hasta + datetime.timedelta(days=1))

        cur.execute(f"SELECT COUNT(*) FROM CD_ES WHERE {filtro}", params)
        total = cur.fetchone()[0]
        self.stdout.write(f"CD_ES desde {desde}{f' hasta {hasta}' if hasta else ''}: {total} filas.")

        # Paginado por Id_ES y no por OFFSET: sobre 8,6 millones de filas el
        # OFFSET vuelve a recorrer todo lo saltado en cada página.
        ultimo = 0
        hechos = 0
        while True:
            cur.execute(
                f"SELECT TOP {_LOTE} {', '.join(_COLS)} FROM CD_ES "
                f"WHERE {filtro} AND Id_ES > ? ORDER BY Id_ES",
                params + [ultimo],
            )
            filas = cur.fetchall()
            if not filas:
                break
            movs = [_Movimiento(r) for r in filas]
            historial_socio.registrar_movimientos(movs, ctx)
            ultimo = movs[-1].external_id
            hechos += len(movs)
            self.stdout.write(f"  {hechos}/{total} (Id_ES {ultimo})")
        self.stdout.write(self.style.SUCCESS(f"xSys: {hechos} movimientos procesados."))

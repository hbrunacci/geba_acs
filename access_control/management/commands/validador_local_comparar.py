"""Contrasta el validador local contra lo que Clever decidió de verdad.

Toma los pasos que el SP de Clever grabó en ``CD_ES`` para un acceso (filas
con ``Id_CD_Motivo``, origen 'A') y vuelve a evaluar cada lectura con la
réplica local, a la misma fecha y hora, por el mismo controlador. Informa
cuántas coinciden en resultado (habilitado / rechazado) y en motivo, y lista
las diferencias.

Las diferencias esperables, que NO son errores de la réplica:
  * Los datos cambiaron desde el paso (pagó la cuota, se le cargó un contrato,
    se lo dio de baja): la réplica mira la base de HOY.
  * QR dinámicos: Clever los resuelve con "token generado hace menos de 5
    minutos" contado desde AHORA, no desde la fecha del paso, así que todo QR
    viejo cae a "La Persona es inválida" (103) o "QR vencido" (115).
  * 211 / 212 / 310: Clever consumió la habilitación manual, el link o la
    unidad del kit al pasar; hoy ya no están.

Sólo lee. Ejemplo:
  python manage.py validador_local_comparar --acceso 14 --horas 24
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand

from access_control.services.validador_local import ValidadorLocal, ahora_local, es_numerico
from xsys.services import mssql

SQL_PASOS = (
    "SELECT TOP (?) Id_ES, Tipo, LTRIM(RTRIM(Id_Tarjeta)), ISNULL(Id_Cliente,0), Fecha, Resultado, "
    "Id_Controlador, Id_CD_Motivo, LTRIM(RTRIM(ISNULL(CONVERT(VARCHAR(200), Observacion),''))) "
    "FROM CD_ES WHERE Id_Acceso = ? AND Fecha >= ? AND Fecha < ? AND Id_CD_Motivo IS NOT NULL "
    "AND Origen = 'A' {ctrl} ORDER BY Fecha DESC"
)


class Command(BaseCommand):
    help = "Re-evalúa con el validador local los pasos que Clever grabó en CD_ES y compara."

    def add_arguments(self, parser):
        parser.add_argument("--acceso", type=int, required=True, help="Id_Acceso a comparar (p.ej. 14 SM-Alcorta).")
        parser.add_argument("--controlador", type=int, default=None, help="Limitar a un Id_Controlador.")
        parser.add_argument("--horas", type=float, default=24, help="Ventana hacia atrás desde ahora (default 24).")
        parser.add_argument("--limite", type=int, default=2000, help="Máximo de pasos a evaluar (default 2000).")
        parser.add_argument("--mostrar", type=int, default=40, help="Cuántas diferencias listar (default 40).")
        parser.add_argument("--sin-qr", action="store_true", help="Excluir lecturas de QR (10 dígitos).")

    def handle(self, *args, **opts):
        hasta = ahora_local()
        desde = hasta - timedelta(hours=opts["horas"])
        sql = SQL_PASOS.format(ctrl="AND Id_Controlador = ?" if opts["controlador"] else "")
        params = [opts["limite"], opts["acceso"], desde, hasta]
        if opts["controlador"]:
            params.append(opts["controlador"])
        with mssql.xsys_cursor() as cur:
            cur.execute(sql, params)
            filas = [tuple(r) for r in cur.fetchall()]
        if opts["sin_qr"]:
            filas = [f for f in filas if not (len(f[2]) == 10 and es_numerico(f[2]))]
        self.stdout.write(f"Acceso {opts['acceso']} · {len(filas)} pasos de Clever entre {desde:%d/%m %H:%M} y {hasta:%d/%m %H:%M}")

        val = ValidadorLocal(cache_seg=3600)
        iguales = resultado_igual = 0
        pares: Counter = Counter()
        diferencias: list[str] = []
        errores = 0
        try:
            for id_es, tipo, tag, id_cliente, fecha, resultado, id_ctrl, motivo, obs in filas:
                d = val.decidir(tipo_acceso=(tipo or "E").strip(), tag=tag, id_controlador=id_ctrl, cuando=fecha)
                if d.error:
                    errores += 1
                clever_ok = (resultado or "").strip() == "S"
                mismo_res = clever_ok == d.habilitado
                mismo_mot = int(motivo) == int(d.id_motivo)
                resultado_igual += mismo_res
                iguales += mismo_res and mismo_mot
                pares[(int(motivo), int(d.id_motivo))] += 1
                if not (mismo_res and mismo_mot) and len(diferencias) < opts["mostrar"]:
                    diferencias.append(
                        f"  {fecha:%d/%m %H:%M:%S} ctrl {id_ctrl} {tipo} tag={tag} socio={id_cliente} | "
                        f"Clever {resultado} {motivo} '{obs[:45]}' | local {d.resultado} {d.id_motivo} "
                        f"'{d.mensaje[:45]}' socio={d.id_cliente}{' ERR ' + d.error[:60] if d.error else ''}")
        finally:
            val.cerrar()

        n = len(filas) or 1
        self.stdout.write(self.style.SUCCESS(
            f"Mismo resultado: {resultado_igual}/{len(filas)} ({100 * resultado_igual / n:.1f} %) · "
            f"mismo resultado y motivo: {iguales}/{len(filas)} ({100 * iguales / n:.1f} %) · errores de consulta: {errores}"))
        self.stdout.write("Motivo Clever -> motivo local (cantidad):")
        for (mc, ml), cant in sorted(pares.items(), key=lambda kv: -kv[1]):
            marca = "" if mc == ml else "   <-- distinto"
            self.stdout.write(f"  {mc:>4} -> {ml:<4} {cant:>6}{marca}")
        if diferencias:
            self.stdout.write("Diferencias:")
            for linea in diferencias:
                self.stdout.write(linea)

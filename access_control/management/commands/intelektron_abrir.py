"""Prueba de conexion + apertura de puerta contra una placa Intelektron API-3000.

Conecta a IP:puerto (por defecto 3002), valida con get_time y opcionalmente
envia un comando de apertura de rele (itk_rele_control).

Valores (del SDK oficial, frmReles):
  rele_id: 0=Desactivado, 1..8=rele N, 254=Multi ON, 255=Multi OFF
  time_mode (funcion): 0=Pulse, N=Time (N segundos), 253=ON, 254=OFF, 255=Inv

VALIDADO EN VIVO (2026-08-11, molinete 10.0.0.115): la apertura que hace girar
el aspa es --rele 2 --time 3 (rele 2 = relé de paso, pulso de 3 segundos).
Con rele 1/3 o Pulse(0) no acciona; relés 4-8 no existen (error 147).

Ejemplos:
  # solo validar conexion (get_time), no abre nada:
  python manage.py intelektron_abrir --ip 10.0.0.67 --check
  # ABRIR (validado): rele 2, pulso 3 segundos:
  python manage.py intelektron_abrir --ip 10.0.0.115 --rele 2 --time 3
"""

from __future__ import annotations

import sys

from django.core.management.base import BaseCommand

WRAPPER = "/app/access_control/services/intelectron/api3000_wrapper"


class Command(BaseCommand):
    help = "Conecta a una placa Intelektron (puerto 3002) y envia un comando de apertura de rele."

    def add_arguments(self, parser):
        parser.add_argument("--ip", required=True, help="IP de la placa.")
        parser.add_argument("--port", type=int, default=3002, help="Puerto TCP (default 3002).")
        parser.add_argument("--source-node", type=int, default=0, help="Nodo origen (default 0).")
        parser.add_argument("--dest-node", type=int, default=1, help="Nodo destino (default 1).")
        parser.add_argument("--rele", type=int, default=None, help="rele_id a accionar (1..8, 254/255 multi).")
        parser.add_argument("--time", type=int, default=0,
                            help="time_mode: 0=Pulse, N=segundos, 253=ON, 254=OFF, 255=Inv (default 0).")
        parser.add_argument("--check", action="store_true", help="Solo validar conexion (get_time), sin abrir.")

    def handle(self, *args, **opts):
        if WRAPPER not in sys.path:
            sys.path.insert(0, WRAPPER)
        from api3000.client import Api3000Client

        ip, port = opts["ip"], opts["port"]
        sn, dn = opts["source_node"], opts["dest_node"]
        conn = "%s:%d" % (ip, port)

        try:
            with Api3000Client(source_node=sn, conn_string=conn, timeout=8000,
                               log_path="/tmp/itk_abrir.log") as cli:
                # 1) validar comunicacion
                try:
                    hora = cli.get_time_as_datetime(dest_node=dn)
                    self.stdout.write(self.style.SUCCESS(
                        "CONECTADO a %s (source_node=%s dest_node=%s) — reloj del equipo: %s" % (conn, sn, dn, hora)))
                except Exception as exc:
                    self.stdout.write(self.style.ERROR("No respondio get_time en %s: %s" % (conn, str(exc)[:150])))
                    return

                if opts["check"] or opts["rele"] is None:
                    self.stdout.write("Modo --check (o sin --rele): no se envia apertura.")
                    return

                # 2) enviar apertura
                rele, tmode = opts["rele"], opts["time"]
                self.stdout.write("Enviando apertura: rele_id=%s time_mode=%s ..." % (rele, tmode))
                try:
                    cli.rele_control(dest_node=dn, rele=rele, action=tmode)
                    self.stdout.write(self.style.SUCCESS(
                        "OK: comando de apertura ACEPTADO (rele=%s, time_mode=%s). Verificar el molinete." % (rele, tmode)))
                except Exception as exc:
                    self.stdout.write(self.style.ERROR("La apertura FALLO: %s" % str(exc)[:180]))
        except Exception as exc:
            self.stdout.write(self.style.ERROR("No se pudo conectar a %s: %s" % (conn, str(exc)[:180])))

"""Modo LISTEN: abre un puerto y espera que la placa Intelektron se conecte
a NOSOTROS, para recibir sus eventos en vivo (lectores y sensores).

Es la via correcta para tiempo real: el itk_open cliente con callbacks NO recibe
eventos (verificado en vivo 2026-08-11). Aca invertimos el sentido: nosotros
somos el servidor (itk_listen + itk_accept) y el equipo el que conecta.

Uso:
    python manage.py intelektron_listen --port 3003 --wait 120
"""

from __future__ import annotations

import sys
import time

from django.core.management.base import BaseCommand

WRAPPER = "/app/access_control/services/intelectron/api3000_wrapper"


class Command(BaseCommand):
    help = "Escucha (itk_listen/itk_accept) a la espera de que una placa Intelektron se conecte."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=3003, help="Puerto TCP donde escuchar (default 3003).")
        parser.add_argument("--wait", type=int, default=120, help="Segundos a esperar una conexion (default 120).")
        parser.add_argument("--source-node", type=int, default=0, help="Nodo origen (default 0).")
        parser.add_argument("--listen-timeout", type=int, default=10000, help="Timeout del listen en ms.")
        parser.add_argument("--rcv-timeout", type=int, default=20000, help="Timeout de recepcion en ms.")
        parser.add_argument("--escuchar", type=int, default=60,
                            help="Tras aceptar, segundos escuchando eventos (default 60).")

    def handle(self, *args, **opts):
        if WRAPPER not in sys.path:
            sys.path.insert(0, WRAPPER)
        from api3000.client import Api3000Client

        port, wait = opts["port"], opts["wait"]
        cli = Api3000Client(source_node=opts["source_node"], log_path="/tmp/itk_listen.log")
        cli.init_library()

        try:
            h_listen = cli.listen(port, timeout=opts["listen_timeout"])
        except Exception as exc:
            self.stdout.write(self.style.ERROR("No se pudo abrir el listen en el puerto %s: %s" % (port, exc)))
            return

        self.stdout.write(self.style.SUCCESS(
            "ESCUCHANDO en 0.0.0.0:%s (h_listen=%s). Esperando que una placa se conecte (%ss)..."
            % (port, h_listen, wait)))
        self.stdout.write("   Si el equipo no esta configurado para reportar a este host, no va a conectar.")

        try:
            info = cli.accept(accept_timeout=wait * 1000, rcv_timeout=opts["rcv_timeout"])
            self.stdout.write(self.style.SUCCESS(
                "CONECTO UNA PLACA: h_link=%s dest_node=%s port=%s"
                % (info["h_link"], info["dest_node"], info["port"])))

            # Con la sesion establecida, probar comunicacion y quedarse escuchando.
            try:
                hora = cli.get_time_as_datetime(dest_node=info["dest_node"])
                self.stdout.write("   reloj del equipo: %s" % hora)
            except Exception as exc:
                self.stdout.write("   (no respondio get_time: %s)" % str(exc)[:80])

            secs = opts["escuchar"]
            self.stdout.write("   escuchando %ss por eventos... PASA UNA CREDENCIAL" % secs)
            time.sleep(secs)
            self.stdout.write("   fin de la ventana de escucha.")
        except Exception as exc:
            self.stdout.write(self.style.WARNING(
                "Nadie se conecto / accept fallo: %s" % str(exc)[:160]))
        finally:
            try:
                cli.close_listen(h_listen)
            except Exception:
                pass
            try:
                cli.uninit_library()
            except Exception:
                pass
            self.stdout.write("listen cerrado.")

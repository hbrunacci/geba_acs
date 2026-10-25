"""PDF de los socios acreditados en la elección, por hora y por mesa.

    python manage.py elecciones_reporte
        Elección más nueva, datos reales de xSys. Guarda el PDF en /tmp.

    python manage.py elecciones_reporte --demo
        Mismo reporte con datos ficticios, para mostrarlo antes de la jornada.

    python manage.py elecciones_reporte --enviar --para hbrunacci@gmail.com
        Además lo manda por correo (sólo a destinatarios permitidos).

Ver ``xsys.services.elecciones_reporte``.
"""

from __future__ import annotations

from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from common.correo import permitidos
from xsys.services import elecciones as E
from xsys.services import elecciones_reporte as R


class Command(BaseCommand):
    help = "Genera el PDF de acreditados de la elección y opcionalmente lo manda por correo."

    def add_arguments(self, parser):
        parser.add_argument("--eleccion", type=int, help="Id_Lista_Precio (default: la más nueva).")
        parser.add_argument("--demo", action="store_true", help="Datos ficticios.")
        parser.add_argument("--salida", help="Ruta del PDF (default: /tmp/...).")
        parser.add_argument("--enviar", action="store_true", help="Mandarlo por correo.")
        parser.add_argument("--para", action="append", help="Destinatario (se puede repetir).")

    def handle(self, *args, **o):
        try:
            todas = E.listar()
            eleccion = (next((e for e in todas if e["id"] == o["eleccion"]), None)
                        if o["eleccion"] else (todas[0] if todas else None))
        except E.EleccionesError as exc:
            raise CommandError(str(exc)) from exc
        if not eleccion:
            raise CommandError("No se encontró la elección.")

        if o["demo"]:
            fecha = timezone.localdate()
            if eleccion.get("fecha"):
                fecha = timezone.datetime.fromisoformat(eleccion["fecha"]).date()
            filas = R.demo(fecha)
        else:
            filas = R.acreditados(eleccion["id"])
        contenido = R.pdf(eleccion, filas, demo=o["demo"])

        ahora = timezone.localtime()
        nombre = f"acreditados_{'demo_' if o['demo'] else ''}{ahora:%Y%m%d_%H%M}.pdf"
        salida = o["salida"] or f"/tmp/{nombre}"
        with open(salida, "wb") as fh:
            fh.write(contenido)
        self.stdout.write(f"{eleccion['nombre']}: {len(filas)} acreditados -> {salida} "
                          f"({len(contenido) // 1024} KB)")

        if o["enviar"]:
            para = o["para"] or sorted(permitidos())
            demo = " (DEMO, datos ficticios)" if o["demo"] else ""
            msg = EmailMessage(
                f"{eleccion['nombre']}: acreditados al {ahora:%d/%m %H:%M}{demo}",
                f"Adjunto el reporte de socios acreditados por hora y por mesa{demo}.\n\n"
                f"Corte: {ahora:%d/%m/%Y %H:%M} · {len(filas)} acreditados.\n\nGEBA ACS",
                None, para)
            msg.attach(nombre, contenido, "application/pdf")
            if not msg.send():
                raise CommandError("No se envió: ningún destinatario está permitido.")
            self.stdout.write(self.style.SUCCESS("Enviado a: " + ", ".join(para)))

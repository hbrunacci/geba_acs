"""Manda un correo de prueba para verificar la configuración.

    python manage.py probar_correo
        A todos los destinatarios permitidos.

    python manage.py probar_correo --para hbrunacci@gmail.com
        A uno solo (tiene que estar en la lista permitida).
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from common.correo import permitidos


class Command(BaseCommand):
    help = "Manda un correo de prueba a los destinatarios permitidos."

    def add_arguments(self, parser):
        parser.add_argument("--para", action="append", help="Destinatario (se puede repetir).")

    def handle(self, *args, **opciones):
        para = opciones["para"] or sorted(permitidos())
        if not para:
            raise CommandError("No hay destinatarios permitidos configurados.")
        enviados = send_mail(
            "GEBA ACS: correo de prueba",
            "Correo de prueba enviado desde GEBA ACS el %s.\n\n"
            "Servidor: %s:%s como %s."
            % (timezone.localtime().strftime("%d/%m/%Y %H:%M"), settings.EMAIL_HOST,
               settings.EMAIL_PORT, settings.EMAIL_HOST_USER),
            None, para, fail_silently=False,
        )
        if not enviados:
            raise CommandError("No se envió: ningún destinatario está en la lista permitida.")
        self.stdout.write(self.style.SUCCESS("Enviado a: " + ", ".join(para)))

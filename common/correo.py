"""Envío de correo con lista cerrada de destinatarios.

Todo mail que salga de la app pasa por ``BackendRestringido``: saca de To, Cc y
Bcc cualquier dirección que no esté en ``EMAIL_DESTINATARIOS_PERMITIDOS`` y, si
no queda ninguna, el mensaje no se manda. Está en el backend y no en cada
llamada a propósito: así no depende de que quien escriba un envío nuevo se
acuerde de filtrar.

Sin contraseña configurada no hay SMTP: se usa el backend de consola, que deja
el mensaje en el log del contenedor.
"""

from __future__ import annotations

import logging
from email.utils import parseaddr

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.console import EmailBackend as ConsolaBackend
from django.core.mail.backends.smtp import EmailBackend as SmtpBackend

logger = logging.getLogger(__name__)


def permitidos() -> set[str]:
    return {d.lower() for d in getattr(settings, "EMAIL_DESTINATARIOS_PERMITIDOS", ())}


def _filtrar(direcciones, ok: set[str], descartadas: list[str]) -> list[str]:
    quedan = []
    for d in direcciones or []:
        if parseaddr(d)[1].lower() in ok:
            quedan.append(d)
        else:
            descartadas.append(d)
    return quedan


class BackendRestringido(BaseEmailBackend):
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        real = SmtpBackend if settings.EMAIL_HOST_PASSWORD else ConsolaBackend
        self.real = real(fail_silently=fail_silently, **kwargs)

    def open(self):
        return self.real.open()

    def close(self):
        return self.real.close()

    def send_messages(self, email_messages):
        ok = permitidos()
        salen = []
        for m in email_messages:
            descartadas: list[str] = []
            m.to = _filtrar(m.to, ok, descartadas)
            m.cc = _filtrar(m.cc, ok, descartadas)
            m.bcc = _filtrar(m.bcc, ok, descartadas)
            if descartadas:
                logger.warning("correo %r: descartados destinatarios no permitidos: %s",
                               m.subject, ", ".join(descartadas))
            if m.recipients():
                salen.append(m)
            else:
                logger.warning("correo %r: sin destinatarios permitidos, no se envía", m.subject)
        if not salen:
            return 0
        return self.real.send_messages(salen)

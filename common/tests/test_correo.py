"""La app sólo puede escribirles a los destinatarios permitidos."""

from unittest import mock

from django.core.mail import EmailMessage
from django.test import SimpleTestCase, override_settings

from common import correo

PERMITIDOS = ("hbrunacci@gmail.com", "leonardomendez@geba.org.ar")


@override_settings(EMAIL_DESTINATARIOS_PERMITIDOS=PERMITIDOS, EMAIL_HOST_PASSWORD="")
class BackendRestringidoTests(SimpleTestCase):
    def _enviar(self, **kw):
        b = correo.BackendRestringido()
        with mock.patch.object(b.real, "send_messages", return_value=1) as real:
            n = b.send_messages([EmailMessage("asunto", "cuerpo", "a@b.c", **kw)])
        return n, real

    def test_saca_a_los_no_permitidos(self):
        n, real = self._enviar(to=["Leonardo Mendez <LeonardoMendez@geba.org.ar>", "socio@gmail.com"],
                               bcc=["otro@x.com", "hbrunacci@gmail.com"])
        m = real.call_args[0][0][0]
        self.assertEqual(m.to, ["Leonardo Mendez <LeonardoMendez@geba.org.ar>"])
        self.assertEqual(m.bcc, ["hbrunacci@gmail.com"])
        self.assertEqual(n, 1)

    def test_sin_ninguno_permitido_no_sale(self):
        n, real = self._enviar(to=["socio@gmail.com"], cc=["otro@x.com"])
        self.assertEqual(n, 0)
        real.assert_not_called()

    def test_sin_contrasena_no_usa_smtp(self):
        self.assertNotIsInstance(correo.BackendRestringido().real, correo.SmtpBackend)

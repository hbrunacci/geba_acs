import json

from django.test import TestCase

from access_control.models import SocioAviso
from institutions.models import AccessDoor
from xsys.models import PantallaPuerta

TOKEN = "tok-aviso-abcdef"
URL = "/api/xsys/socios/914988/aviso/"


class PantallaAvisoAPITests(TestCase):
    """Avisos de un toque desde el monitor, que es un kiosco sin login."""

    @classmethod
    def setUpTestData(cls):
        door = AccessDoor.objects.create(name="Ombues", is_active=True, xsys_id_acceso=15)
        PantallaPuerta.objects.create(token=TOKEN, door=door, ip="10.0.0.9", nombre="Molinete 3")

    def _post(self, tipo, token=TOKEN):
        kw = {"HTTP_X_PANTALLA_TOKEN": token} if token else {}
        return self.client.post(URL, json.dumps({"tipo": tipo}), content_type="application/json", **kw)

    def test_crea_el_aviso_con_el_texto_del_servidor(self):
        r = self._post("tomar_foto")
        self.assertEqual(r.status_code, 201)
        aviso = SocioAviso.objects.get()
        self.assertEqual(aviso.id_cliente, 914988)
        self.assertEqual(aviso.tipo, "tomar_foto")
        self.assertEqual(aviso.texto, "Se indica tomar foto")

    def test_los_tres_tipos_del_visor(self):
        esperados = {
            "tomar_foto": "Se indica tomar foto",
            "deuda": "Se notifica deuda",
            "pase_por_socios": "Se indica pasar por oficina de socios",
        }
        for tipo, texto in esperados.items():
            self.assertEqual(self._post(tipo).status_code, 201)
            self.assertTrue(SocioAviso.objects.filter(tipo=tipo, texto=texto).exists())

    def test_registra_de_que_pantalla_salio(self):
        """En un kiosco sin login es lo único que se sabe del origen."""
        self._post("deuda")
        self.assertEqual(SocioAviso.objects.get().creado_por, "monitor: Molinete 3")

    def test_rechaza_un_tipo_desconocido(self):
        r = self._post("borrar_socio")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(SocioAviso.objects.exists())

    def test_no_acepta_texto_libre(self):
        """El molinete no tiene teclado: el texto lo fija el servidor."""
        r = self.client.post(
            URL, json.dumps({"tipo": "libre", "texto": "lo que sea"}),
            content_type="application/json", HTTP_X_PANTALLA_TOKEN=TOKEN,
        )
        self.assertEqual(r.status_code, 400)
        self.assertFalse(SocioAviso.objects.exists())

    def test_exige_token_de_pantalla(self):
        r = self._post("deuda", token=None)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(SocioAviso.objects.exists())

    def test_el_aviso_creado_aparece_en_el_visor(self):
        """Lo que se deja acá tiene que salir con 📢 cuando el socio vuelve a pasar."""
        self._post("pase_por_socios")
        avisos = list(
            SocioAviso.objects.filter(id_cliente=914988).values_list("texto", flat=True)
        )
        self.assertEqual(avisos, ["Se indica pasar por oficina de socios"])

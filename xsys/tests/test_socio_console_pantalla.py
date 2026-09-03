"""La pantalla de socios, mirando el HTML que sale, no el código que la arma.

Varias veces pasó que la pantalla quedaba rota con las pruebas en verde: probaban
el código de estado y no lo renderizado. Acá se mira la salida: que los ``id`` que
el script busca existan de verdad, que la URL a la que pega esté ruteada, y que
no se cuele un comentario de Django impreso en pantalla.
"""

import re

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from common.roles import GRUPO_ADMIN

URL = "/xsys/socios/"


class SocioConsolePantallaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        u = User.objects.create_user("admin", password="x")
        u.groups.add(Group.objects.get_or_create(name=GRUPO_ADMIN)[0])

    def setUp(self):
        self.client.login(username="admin", password="x")
        self.html = self.client.get(URL).content.decode()

    def test_la_pantalla_abre(self):
        self.assertEqual(self.client.get(URL).status_code, 200)

    def test_tiene_la_seccion_de_historial(self):
        self.assertIn("Historial de accesos", self.html)

    def test_los_ids_que_busca_el_script_existen_en_el_html(self):
        """Un getElementById que no matchea deja la pantalla muda, sin error visible."""
        buscados = set(re.findall(r'getElementById\("([^"]+)"\)', self.html))
        presentes = set(re.findall(r'id="([^"]+)"', self.html))
        self.assertTrue(buscados, "el script no busca ningún id: algo se rompió")
        self.assertEqual(buscados - presentes, set())

    def test_pega_a_una_url_ruteada(self):
        self.assertIn('"/api/xsys/socios/" + encodeURIComponent(idCliente) + "/accesos/?"', self.html)
        self.assertEqual(reverse("xsys_socio_accesos_api", args=[1]),
                         "/api/xsys/socios/1/accesos/")

    def test_no_se_imprime_un_comentario_de_django(self):
        """``{# … #}`` es de UNA línea: en varias, Django lo escupe en pantalla."""
        self.assertNotIn("{#", self.html)

    def test_los_filtros_del_historial_estan_cableados(self):
        for campo in ("h-origen", "h-resultado", "h-desde", "h-hasta"):
            self.assertIn('id="%s"' % campo, self.html)
        self.assertIn("hFiltros.forEach", self.html)

    def test_el_boton_de_ver_mas_no_reinicia_la_lista(self):
        """Pasarle ``true`` al segundo argumento borraría lo ya cargado."""
        self.assertIn('hMas.addEventListener("click", function () '
                      '{ cargarHistorial(hSocio, false); });', self.html)

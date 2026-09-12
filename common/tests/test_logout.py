"""Pruebas del cierre de sesión.

Contexto: el sidebar salía con ``<a href="{% url 'common:logout' %}">``. Desde
Django 5, ``LogoutView`` sólo acepta POST —GET quedó deprecado en 4.1 y se
removió en 5.0—, así que el enlace devolvía 405 y la sesión seguía abierta. El
usuario clickeaba "Cerrar sesión", no pasaba nada visible, y quedaba logueado.

Es la clase de rotura que no se nota al actualizar Django porque no falla
ninguna pantalla: falla un botón. Estas pruebas la fijan.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

H = {"HTTP_HOST": "localhost"}


class LogoutTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("saliente", password="x")
        self.client.force_login(self.user)

    def _logueado(self) -> bool:
        return "_auth_user_id" in self.client.session

    def test_el_post_cierra_la_sesion(self):
        self.assertTrue(self._logueado())
        r = self.client.post(reverse("common:logout"), **H)
        self.assertEqual(r.status_code, 302)
        self.assertFalse(self._logueado())

    def test_despues_de_salir_manda_al_login(self):
        r = self.client.post(reverse("common:logout"), **H)
        self.assertEqual(r.url, reverse("common:login"))

    def test_el_get_no_cierra_la_sesion(self):
        # No es un bug a arreglar: es el comportamiento de Django 5 y la razón
        # por la que el botón tiene que ser un POST. Si algún día Django volviera
        # a aceptar GET, esta prueba avisa que el motivo del formulario cambió.
        r = self.client.get(reverse("common:logout"), **H)
        self.assertEqual(r.status_code, 405)
        self.assertTrue(self._logueado())

    def _html_del_sidebar(self) -> str:
        """Una pantalla renderizada de verdad, para mirar el sidebar.

        Con un usuario sin roles el dashboard redirige y el cuerpo viene vacío,
        así que para esto hace falta alguien que llegue a ver la página.
        """
        User = get_user_model()
        self.client.force_login(User.objects.create_superuser("root_out", password="x"))
        r = self.client.get(reverse("common:dashboard"), **H)
        self.assertEqual(r.status_code, 200, "El dashboard no renderizó")
        return r.content.decode()

    def test_el_boton_del_sidebar_es_un_form_por_post_con_csrf(self):
        html = self._html_del_sidebar()
        self.assertIn('action="/logout/"', html)
        self.assertIn('method="post"', html)
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertIn('type="submit"', html)

    def test_el_sidebar_ya_no_tiene_el_enlace_viejo(self):
        # El <a href> a /logout/ es exactamente lo que estaba roto.
        self.assertNotIn('<a class="sidebar__logout" href="/logout/"',
                         self._html_del_sidebar())

    def test_salir_de_verdad_deja_afuera_de_las_pantallas(self):
        # Cierra el círculo: no alcanza con que la sesión se borre, tiene que
        # dejar de entrar a una pantalla con login.
        self.client.post(reverse("common:logout"), **H)
        r = self.client.get(reverse("common:dashboard"), **H)
        self.assertIn(r.status_code, (302, 403))

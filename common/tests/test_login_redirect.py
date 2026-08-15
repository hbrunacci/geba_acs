from django.contrib.auth.models import Group, User
from django.test import TestCase

from common.roles import GRUPO_PUERTAS

PASS = "clave-de-prueba-123"


class LoginRedirectTests(TestCase):
    """El login tiene que devolver a la página protegida de la que se venía.

    Es lo que necesita el monitor: el operador toca "Avisos dejados a socios",
    se loguea y tiene que caer en /avisos/, no en el dashboard.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="operador", password=PASS)
        cls.user.groups.add(Group.objects.get_or_create(name=GRUPO_PUERTAS)[0])

    def test_una_pagina_protegida_manda_al_login_con_next(self):
        r = self.client.get("/avisos/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/login/?next=/avisos/")

    def test_despues_de_loguearse_vuelve_a_la_pagina_pedida(self):
        r = self.client.post("/login/", {"username": "operador", "password": PASS, "next": "/avisos/"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/avisos/")

    def test_sin_next_va_al_dashboard(self):
        r = self.client.post("/login/", {"username": "operador", "password": PASS})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/")

    def test_no_redirige_a_un_sitio_externo(self):
        """El next llega por la URL: no puede servir para sacar gente del sitio."""
        r = self.client.post(
            "/login/",
            {"username": "operador", "password": PASS, "next": "https://sitio-ajeno.example/x"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/")

    def test_el_flujo_completo_termina_en_avisos(self):
        self.client.get("/avisos/")
        self.client.post("/login/", {"username": "operador", "password": PASS, "next": "/avisos/"})
        r = self.client.get("/avisos/")
        self.assertEqual(r.status_code, 200)

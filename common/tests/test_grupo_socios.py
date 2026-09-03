"""El grupo ``socios`` y la pantalla de avisos a socios."""

from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, Group, User
from django.test import TestCase

from access_control.models import SocioAviso
from common.roles import GRUPO_PUERTAS, GRUPO_SOCIOS, puede_socios

URL = "/avisos/"


class GrupoSociosTests(TestCase):
    def test_la_migracion_deja_el_grupo(self):
        self.assertTrue(Group.objects.filter(name=GRUPO_SOCIOS).exists())

    def test_el_nombre_es_el_que_pidio_el_club(self):
        self.assertEqual(GRUPO_SOCIOS, "socios")

    def test_no_quedo_una_variante_con_mayuscula(self):
        self.assertFalse(Group.objects.filter(name="Socios").exists())


class PuedeSociosTests(TestCase):
    def test_con_el_grupo(self):
        u = User.objects.create_user("s", password="x")
        u.groups.add(Group.objects.get(name=GRUPO_SOCIOS))
        self.assertTrue(puede_socios(u))

    def test_el_rol_de_puertas_lo_sigue_teniendo(self):
        """La pantalla nació ahí: el grupo nuevo suma, no reemplaza."""
        u = User.objects.create_user("p", password="x")
        u.groups.add(Group.objects.get_or_create(name=GRUPO_PUERTAS)[0])
        self.assertTrue(puede_socios(u))

    def test_superusuario(self):
        self.assertTrue(puede_socios(User.objects.create_superuser("su", "s@x.com", "x")))

    def test_un_usuario_suelto_no(self):
        self.assertFalse(puede_socios(User.objects.create_user("x", password="x")))

    def test_anonimo_no(self):
        self.assertFalse(puede_socios(AnonymousUser()))

    def test_inactivo_con_grupo_no_entra(self):
        """Un usuario dado de baja no puede seguir entrando por el grupo."""
        u = User.objects.create_user("b", password="x", is_active=False)
        u.groups.add(Group.objects.get(name=GRUPO_SOCIOS))
        self.assertEqual(self.client.login(username="b", password="x"), False)


class PantallaAvisosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        SocioAviso.objects.create(id_cliente=929935, texto="Pasar por Socios")
        cls.socios = User.objects.create_user("oficina", password="x")
        cls.socios.groups.add(Group.objects.get(name=GRUPO_SOCIOS))
        cls.puertas = User.objects.create_user("puerta", password="x")
        cls.puertas.groups.add(Group.objects.get_or_create(name=GRUPO_PUERTAS)[0])
        cls.suelto = User.objects.create_user("nadie", password="x")

    def setUp(self):
        # La pantalla encola en un hilo la búsqueda en xSys de los socios que le
        # faltan al espejo. Desde el contenedor el SQL del club RESPONDE, y ese
        # hilo escribe fuera de la transacción de la prueba: el socio queda en la
        # base de test y le rompe el conteo a la prueba que corra después.
        parche = patch("xsys.services.socio_fetch.request_many")
        parche.start()
        self.addCleanup(parche.stop)

    def _abrir(self, username):
        self.client.login(username=username, password="x")
        return self.client.get(URL)

    def test_el_grupo_socios_entra(self):
        self.assertEqual(self._abrir("oficina").status_code, 200)

    def test_el_grupo_socios_ve_los_avisos(self):
        self.assertContains(self._abrir("oficina"), "Pasar por Socios")

    def test_el_rol_de_puertas_sigue_entrando(self):
        self.assertEqual(self._abrir("puerta").status_code, 200)

    def test_un_usuario_sin_grupo_no(self):
        self.assertEqual(self._abrir("nadie").status_code, 403)

    def test_sin_login_no(self):
        self.assertEqual(self.client.get(URL).status_code, 302)

    def test_al_grupo_socios_no_se_le_ofrece_el_diagnostico(self):
        """El diagnóstico consulta en vivo contra el SQL del club: sigue siendo
        del rol de puertas. Mostrarle el botón sería mandarlo a un 403."""
        self.assertNotContains(self._abrir("oficina"), "/diag-facial/")

    def test_al_de_puertas_si(self):
        self.assertContains(self._abrir("puerta"), "/diag-facial/")

    def test_el_menu_les_muestra_la_pantalla(self):
        """Sin ítem en el sidebar, el grupo socios tendría que saberse la URL."""
        self.assertContains(self._abrir("oficina"), "Avisos a socios")

    def test_el_sidebar_pregunta_por_el_rol(self):
        """El ítem del menú se dibuja con ``nav_can_socios``; si el context
        processor no lo expusiera, el bloque quedaría siempre apagado."""
        from django.test import RequestFactory

        from common.context_processors import nav_roles

        req = RequestFactory().get("/")
        req.user = self.socios
        self.assertTrue(nav_roles(req)["nav_can_socios"])
        req.user = self.suelto
        self.assertFalse(nav_roles(req)["nav_can_socios"])

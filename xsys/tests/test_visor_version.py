from unittest import mock

from django.test import TestCase

from xsys import api_views


class VersionVisorTests(TestCase):
    """La versión del visor hace que las pantallas kiosco se auto-recarguen."""

    def setUp(self):
        # El valor se cachea entre llamadas: cada test arranca en limpio.
        api_views._VERSION_CACHE = ("", "")

    def _con_mtime(self, mtime_ns):
        st = mock.Mock(st_mtime_ns=mtime_ns)
        return mock.patch("pathlib.Path.stat", return_value=st)

    def test_es_estable_si_el_template_no_cambia(self):
        with self._con_mtime(1_000):
            self.assertEqual(api_views._version_visor(), api_views._version_visor())

    def test_cambia_cuando_se_edita_el_template(self):
        with self._con_mtime(1_000):
            antes = api_views._version_visor()
        with self._con_mtime(2_000):
            despues = api_views._version_visor()
        self.assertNotEqual(antes, despues)

    def test_no_rompe_si_el_template_no_se_puede_leer(self):
        with mock.patch("pathlib.Path.stat", side_effect=OSError):
            self.assertEqual(api_views._version_visor(), "")

    def test_el_endpoint_publica_la_version(self):
        from institutions.models import AccessDoor
        from xsys.models import PantallaPuerta

        door = AccessDoor.objects.create(name="Prueba", is_active=True, xsys_id_acceso=14)
        p = PantallaPuerta.objects.create(token="tok-version-1234", door=door, ip="1.2.3.4")
        r = self.client.get("/api/xsys/puerta/estado/", HTTP_X_PANTALLA_TOKEN=p.token)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["visor_version"])

    def test_la_pagina_del_monitor_no_se_cachea(self):
        """Sin esto, el kiosco se queda con el JS viejo tras cada deploy."""
        r = self.client.get("/xsys/puerta/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-store", r["Cache-Control"])

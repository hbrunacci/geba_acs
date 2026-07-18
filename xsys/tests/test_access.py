from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from xsys.models import XsysSocio, XsysWhitelist
from xsys.services.access import resolver_acceso
from xsys.services.mssql import XsysConnectionError


def _socio(id_cliente=944426, doc=31850936, cred="BCB30514", activo=1):
    return XsysSocio.objects.create(
        id_cliente=id_cliente, doc_nro=doc, apellido="SIMOUR", nombre="GERMAN",
        activo=activo, tipo_persona="F", credencial_nro=cred, ult_cuota_paga=timezone.now(),
    )


class ResolverAccesoTests(TestCase):
    def test_local_positivo_no_verifica_online(self):
        _socio()
        XsysWhitelist.objects.create(id_cliente=944426, habilitado=True, motivo="CUOTA SOCIAL")
        with patch("xsys.services.access.compute_habilitacion") as m:
            r = resolver_acceso(doc=31850936)
        self.assertTrue(r["puede_ingresar"])
        self.assertEqual(r["origen"], "local")
        self.assertNotIn("reverificacion", r)
        m.assert_not_called()

    def test_local_negativo_reverifica_y_flipea(self):
        _socio()
        XsysWhitelist.objects.create(id_cliente=944426, habilitado=False, motivo="Rechazado por Vencimiento")
        nuevo = {"habilitado": True, "motivo_code": 200, "motivo": "Habilit. por Ult. Cuota Paga", "detalle": "", "id_acceso": 22}
        with patch("xsys.services.access.compute_habilitacion", return_value=nuevo) as m:
            r = resolver_acceso(credencial="bcb30514")
        m.assert_called_once_with(944426)
        self.assertTrue(r["puede_ingresar"])
        self.assertEqual(r["origen"], "xsys_reverificado")
        self.assertTrue(r["reverificacion"]["cambio"])
        self.assertEqual(r["reverificacion"]["motivo_previo"], "Rechazado por Vencimiento")
        # write-through: el espejo local quedó actualizado
        self.assertTrue(XsysWhitelist.objects.get(id_cliente=944426).habilitado)

    def test_local_negativo_online_sin_cambio(self):
        _socio()
        XsysWhitelist.objects.create(id_cliente=944426, habilitado=False, motivo="Rechazado por Vencimiento")
        igual = {"habilitado": False, "motivo_code": 309, "motivo": "Rechazado por Vencimiento", "detalle": "", "id_acceso": 22}
        with patch("xsys.services.access.compute_habilitacion", return_value=igual):
            r = resolver_acceso(id_cliente=944426)
        self.assertFalse(r["puede_ingresar"])
        self.assertTrue(r["reverificacion"]["disponible"])
        self.assertFalse(r["reverificacion"]["cambio"])

    def test_local_negativo_xsys_no_disponible(self):
        _socio()
        XsysWhitelist.objects.create(id_cliente=944426, habilitado=False, motivo="Rechazado")
        with patch("xsys.services.access.compute_habilitacion", side_effect=XsysConnectionError("sin ruta")):
            r = resolver_acceso(id_cliente=944426)
        self.assertFalse(r["puede_ingresar"])
        self.assertFalse(r["reverificacion"]["disponible"])
        self.assertIn("sin ruta", r["reverificacion"]["error"])

    def test_verificar_online_false_no_llama(self):
        _socio()
        XsysWhitelist.objects.create(id_cliente=944426, habilitado=False, motivo="Rechazado")
        with patch("xsys.services.access.compute_habilitacion") as m:
            r = resolver_acceso(id_cliente=944426, verificar_online=False)
        self.assertFalse(r["puede_ingresar"])
        self.assertEqual(r["origen"], "local")
        m.assert_not_called()

    def test_socio_no_encontrado(self):
        r = resolver_acceso(doc=1)
        self.assertFalse(r["found"])
        self.assertFalse(r["puede_ingresar"])
        self.assertEqual(r["motivo"], "socio_no_encontrado")

    def test_sin_parametros(self):
        with self.assertRaises(ValueError):
            resolver_acceso()


class AccesoApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("op", password="pw")
        self.client.force_login(self.user)
        _socio()
        XsysWhitelist.objects.create(id_cliente=944426, habilitado=True, motivo="CUOTA SOCIAL")

    def test_endpoint_local_positivo(self):
        r = self.client.get("/api/xsys/acceso/", {"doc": 31850936})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["puede_ingresar"])
        self.assertEqual(d["origen"], "local")

    def test_endpoint_no_encontrado_404(self):
        self.assertEqual(self.client.get("/api/xsys/acceso/", {"doc": 1}).status_code, 404)

    def test_endpoint_sin_parametros_400(self):
        self.assertEqual(self.client.get("/api/xsys/acceso/").status_code, 400)

    def test_endpoint_online_false(self):
        XsysWhitelist.objects.filter(id_cliente=944426).update(habilitado=False)
        with patch("xsys.services.access.compute_habilitacion") as m:
            r = self.client.get("/api/xsys/acceso/", {"doc": 31850936, "online": "0"})
        self.assertFalse(r.json()["puede_ingresar"])
        m.assert_not_called()

    def test_endpoint_requiere_auth(self):
        self.client.logout()
        self.assertIn(self.client.get("/api/xsys/acceso/", {"doc": 31850936}).status_code, (401, 403))

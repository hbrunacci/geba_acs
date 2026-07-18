from django.test import TestCase
from django.utils import timezone

from access_control.models.models import ExternalAccessLogEntry
from xsys.models import (
    PantallaPuerta,
    XsysAcceso,
    XsysMotivo,
    XsysSocio,
    XsysSocioFoto,
)

TOKEN = "pantalla-token-abc123"


def _get(client, url):
    return client.get(url, HTTP_X_PANTALLA_TOKEN=TOKEN)


def _post(client, url, data):
    return client.post(url, data, content_type="application/json", HTTP_X_PANTALLA_TOKEN=TOKEN)


class PuertaMonitorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        XsysAcceso.objects.create(id_acceso=22, descripcion="Acceso CS", activo=1)
        XsysAcceso.objects.create(id_acceso=24, descripcion="Pileta", activo=1)
        XsysAcceso.objects.create(id_acceso=99, descripcion="Inactiva", activo=0)
        XsysMotivo.objects.create(id_cd_motivo=305, descripcion="Habilitado cuota", descripcion_pantalla="ADELANTE, CUOTA OK")
        cls.socio = XsysSocio.objects.create(id_cliente=944426, apellido="SIMOUR", nombre="GERMAN", activo=1)
        XsysSocioFoto.objects.create(id_cliente=944426, nro=1, imagen=b"\xff\xd8\xff\xe0x", sha256="x")

    def _evento(self, id_es, id_acceso=22, tipo="E", resultado="S", id_cliente=944426, motivo=305):
        return ExternalAccessLogEntry.objects.create(
            external_id=id_es, tipo=tipo, id_cliente=id_cliente, fecha=timezone.now(),
            resultado=resultado, id_acceso=id_acceso, id_cd_motivo=motivo, observacion="obs",
        )

    def test_puertas_lista_solo_activas(self):
        r = _get(self.client, "/api/xsys/puertas/")
        self.assertEqual(r.status_code, 200)
        ids = [p["id_acceso"] for p in r.json()["puertas"]]
        self.assertIn(22, ids); self.assertIn(24, ids); self.assertNotIn(99, ids)

    def test_sin_token_400(self):
        self.assertEqual(self.client.get("/api/xsys/puerta/ultimo/").status_code, 400)
        self.assertEqual(
            self.client.post("/api/xsys/puerta/seleccionar/", {"id_acceso": 22},
                             content_type="application/json").status_code,
            400,
        )

    def test_sin_configurar_devuelve_puertas(self):
        r = _get(self.client, "/api/xsys/puerta/ultimo/")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertFalse(d["configurada"])
        self.assertTrue(any(p["id_acceso"] == 22 for p in d["puertas"]))
        self.assertTrue(PantallaPuerta.objects.filter(token=TOKEN).exists())

    def test_seleccionar_puerta(self):
        r = _post(self.client, "/api/xsys/puerta/seleccionar/", {"id_acceso": 22})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(PantallaPuerta.objects.get(token=TOKEN).id_acceso, 22)

    def test_seleccionar_puerta_inexistente_404(self):
        r = _post(self.client, "/api/xsys/puerta/seleccionar/", {"id_acceso": 12345})
        self.assertEqual(r.status_code, 404)

    def test_ultimo_ingreso_con_evento(self):
        PantallaPuerta.objects.create(token=TOKEN, id_acceso=22)
        self._evento(8000, id_acceso=22)
        self._evento(8001, id_acceso=22)          # último ingreso
        self._evento(7999, id_acceso=24)          # otra puerta
        self._evento(8002, id_acceso=22, tipo="S")  # egreso, se ignora
        r = _get(self.client, "/api/xsys/puerta/ultimo/")
        d = r.json()
        self.assertTrue(d["configurada"])
        self.assertEqual(d["puerta"]["id_acceso"], 22)
        ev = d["evento"]
        self.assertEqual(ev["id_es"], 8001)
        self.assertTrue(ev["permitido"])
        self.assertEqual(ev["mensaje"], "ADELANTE, CUOTA OK")
        self.assertEqual(ev["nombre"], "SIMOUR, GERMAN")
        self.assertEqual(ev["foto_url"], "/api/xsys/socios/944426/foto/")

    def test_ultimo_sin_eventos(self):
        PantallaPuerta.objects.create(token=TOKEN, id_acceso=24)
        r = _get(self.client, "/api/xsys/puerta/ultimo/")
        d = r.json()
        self.assertTrue(d["configurada"])
        self.assertIsNone(d["evento"])

    def test_evento_denegado_usa_observacion_si_no_hay_motivo(self):
        PantallaPuerta.objects.create(token=TOKEN, id_acceso=22)
        ExternalAccessLogEntry.objects.create(
            external_id=9000, tipo="E", id_cliente=944426, fecha=timezone.now(),
            resultado="N", id_acceso=22, id_cd_motivo=None, observacion="Rechazado por vencimiento",
        )
        r = _get(self.client, "/api/xsys/puerta/ultimo/")
        ev = r.json()["evento"]
        self.assertFalse(ev["permitido"])
        self.assertEqual(ev["mensaje"], "Rechazado por vencimiento")

    def test_registra_ip_como_dato(self):
        PantallaPuerta.objects.create(token=TOKEN, id_acceso=22)
        self.client.get("/api/xsys/puerta/ultimo/", HTTP_X_PANTALLA_TOKEN=TOKEN, REMOTE_ADDR="10.1.2.3")
        self.assertEqual(PantallaPuerta.objects.get(token=TOKEN).ip, "10.1.2.3")

    def test_monitor_page_publico(self):
        r = self.client.get("/xsys/puerta/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Monitor de puerta")

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from access_control.models.models import ExternalAccessLogEntry
from xsys.models import (
    PantallaPuerta,
    PuertaMolinete,
    XsysAcceso,
    XsysControlador,
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
        XsysAcceso.objects.create(id_acceso=14, descripcion="SM-Alcorta", activo=1)
        XsysAcceso.objects.create(id_acceso=16, descripcion="SM-Noble", activo=1)
        # molinetes (controladores) de la puerta 14
        XsysControlador.objects.create(id_controlador=59, id_acceso=14, descripcion="Alcorta Mol1", tipo_cont="K", activo=1)
        XsysControlador.objects.create(id_controlador=60, id_acceso=14, descripcion="Alcorta Mol2", tipo_cont="K", activo=1)
        XsysControlador.objects.create(id_controlador=90, id_acceso=14, descripcion="Alcorta Facial", tipo_cont="F", activo=1)
        XsysMotivo.objects.create(id_cd_motivo=305, descripcion="ok", descripcion_pantalla="ADELANTE")
        XsysSocio.objects.create(id_cliente=944426, apellido="SIMOUR", nombre="GERMAN", activo=1)
        XsysSocioFoto.objects.create(id_cliente=944426, nro=1, imagen=b"\xff\xd8\xff\xe0x", sha256="x")

    def _ev(self, id_es, id_controlador, id_acceso=14, tipo="E", resultado="S", motivo=305):
        return ExternalAccessLogEntry.objects.create(
            external_id=id_es, tipo=tipo, id_cliente=944426, fecha=timezone.now(),
            resultado=resultado, id_acceso=id_acceso, id_controlador=id_controlador,
            id_cd_motivo=motivo, observacion="obs",
        )

    def test_sin_token_400(self):
        self.assertEqual(self.client.get("/api/xsys/puerta/estado/").status_code, 400)

    def test_seleccionar_una_puerta(self):
        r = _post(self.client, "/api/xsys/puerta/seleccionar/", {"id_acceso": 14, "nombre": "Alcorta"})
        self.assertEqual(r.status_code, 200)
        p = PantallaPuerta.objects.get(token=TOKEN)
        self.assertEqual(p.id_acceso, 14)
        self.assertEqual(p.nombre, "Alcorta")

    def test_estado_auto_una_columna_por_controlador(self):
        PantallaPuerta.objects.create(token=TOKEN, id_acceso=14)
        self._ev(8000, 59); self._ev(8001, 60); self._ev(8002, 90)
        d = _get(self.client, "/api/xsys/puerta/estado/").json()
        self.assertTrue(d["configurada"])
        # sin molinetes definidos -> auto: una columna por cada controlador activo (3)
        self.assertEqual(len(d["columnas"]), 3)
        by_nombre = {c["nombre"]: c for c in d["columnas"]}
        self.assertEqual(by_nombre["Alcorta Mol1"]["ultimo"]["id_es"], 8000)
        self.assertEqual(by_nombre["Alcorta Mol2"]["ultimo"]["id_es"], 8001)

    def test_estado_molinete_agrupa_controladores(self):
        # Columna "Molinete 1" agrupa el molinete 59 + su facial 90
        PuertaMolinete.objects.create(id_acceso=14, nombre="Molinete 1", id_controladores=[59, 90], orden=0)
        PuertaMolinete.objects.create(id_acceso=14, nombre="Molinete 2", id_controladores=[60], orden=1)
        PantallaPuerta.objects.create(token=TOKEN, id_acceso=14)
        self._ev(8000, 59)   # molinete 1 (por 59)
        self._ev(8001, 90)   # molinete 1 (por facial 90) -> mismo lugar
        self._ev(8002, 60)   # molinete 2
        d = _get(self.client, "/api/xsys/puerta/estado/").json()
        cols = d["columnas"]
        self.assertEqual([c["nombre"] for c in cols], ["Molinete 1", "Molinete 2"])
        self.assertEqual(cols[0]["ultimo"]["id_es"], 8001)          # última lectura del molinete 1 (facial)
        hist_ids = [e["id_es"] for e in cols[0]["historial"]]
        self.assertIn(8000, hist_ids)                                # la del molinete convive en la misma columna
        self.assertEqual(cols[1]["ultimo"]["id_es"], 8002)

    def test_config_crud_molinetes(self):
        user = User.objects.create_user("admin", password="pw")
        self.client.force_login(user)
        # crear
        r = self.client.post("/api/xsys/config/molinetes/",
                             {"id_acceso": 14, "nombre": "Mol A", "id_controladores": [59, 90]},
                             content_type="application/json")
        self.assertEqual(r.status_code, 201)
        mid = r.json()["id"]
        # listar
        r = self.client.get("/api/xsys/config/molinetes/?id_acceso=14")
        self.assertEqual(len(r.json()["molinetes"]), 1)
        # auto (crea por los controladores que faltan: 60)
        r = self.client.post("/api/xsys/config/molinetes/auto/", {"id_acceso": 14}, content_type="application/json")
        self.assertGreaterEqual(r.json()["creados"], 1)
        # eliminar
        self.assertEqual(self.client.delete("/api/xsys/config/molinetes/%d/" % mid).status_code, 204)

    def test_config_requiere_login(self):
        self.assertIn(self.client.get("/api/xsys/config/molinetes/?id_acceso=14").status_code, (401, 403))

    def test_monitor_page_publico(self):
        self.assertEqual(self.client.get("/xsys/puerta/").status_code, 200)

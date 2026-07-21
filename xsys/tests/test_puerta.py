from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from access_control.models.models import ExternalAccessLogEntry
from institutions.models import AccessDoor, DoorController, DoorTurnstileGroup
from xsys.models import (
    PantallaPuerta,
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
        # Espejo xSys: accesos y controladores (los controladores son de xSys).
        XsysAcceso.objects.create(id_acceso=14, descripcion="SM-Alcorta", activo=1)
        XsysAcceso.objects.create(id_acceso=16, descripcion="SM-Noble", activo=1)
        XsysControlador.objects.create(id_controlador=59, id_acceso=14, descripcion="Alcorta Mol1", tipo_cont="K", activo=1)
        XsysControlador.objects.create(id_controlador=60, id_acceso=14, descripcion="Alcorta Mol2", tipo_cont="K", activo=1)
        XsysControlador.objects.create(id_controlador=90, id_acceso=14, descripcion="Alcorta Facial", tipo_cont="F", activo=1)
        XsysMotivo.objects.create(id_cd_motivo=305, descripcion="ok", descripcion_pantalla="ADELANTE")
        XsysSocio.objects.create(
            id_cliente=944426, apellido="SIMOUR", nombre="GERMAN", activo=1,
            categoria="SOCIO ACTIVO", ult_cuota_paga=timezone.now(),
        )
        XsysSocioFoto.objects.create(id_cliente=944426, nro=1, imagen=b"\xff\xd8\xff\xe0x", sha256="x")

        # Puerta local (independiente de xSys) con sus controladores asignados.
        cls.door = AccessDoor.objects.create(name="SM-Alcorta", xsys_id_acceso=14)
        for orden, cid in enumerate((59, 60, 90)):
            DoorController.objects.create(door=cls.door, id_controlador=cid, orden=orden)

    def _ev(self, id_es, id_controlador, id_acceso=14, tipo="E", resultado="S", motivo=305):
        return ExternalAccessLogEntry.objects.create(
            external_id=id_es, tipo=tipo, id_cliente=944426, fecha=timezone.now(),
            resultado=resultado, id_acceso=id_acceso, id_controlador=id_controlador,
            id_cd_motivo=motivo, observacion="obs",
        )

    def test_sin_token_400(self):
        self.assertEqual(self.client.get("/api/xsys/puerta/estado/").status_code, 400)

    def test_seleccionar_una_puerta(self):
        r = _post(self.client, "/api/xsys/puerta/seleccionar/", {"puerta_id": self.door.id, "nombre": "Alcorta"})
        self.assertEqual(r.status_code, 200)
        p = PantallaPuerta.objects.get(token=TOKEN)
        self.assertEqual(p.door_id, self.door.id)
        self.assertEqual(p.nombre, "Alcorta")

    def test_estado_auto_una_columna_por_controlador(self):
        PantallaPuerta.objects.create(token=TOKEN, door=self.door)
        self._ev(8000, 59); self._ev(8001, 60); self._ev(8002, 90)
        d = _get(self.client, "/api/xsys/puerta/estado/").json()
        self.assertTrue(d["configurada"])
        # sin grupos definidos -> auto: una columna por cada controlador asignado (3)
        self.assertEqual(len(d["columnas"]), 3)
        by_nombre = {c["nombre"]: c for c in d["columnas"]}
        self.assertEqual(by_nombre["Alcorta Mol1"]["ultimo"]["id_es"], 8000)
        self.assertEqual(by_nombre["Alcorta Mol2"]["ultimo"]["id_es"], 8001)
        # el mensaje ahora trae categoría + última cuota paga
        ev = by_nombre["Alcorta Mol1"]["ultimo"]
        self.assertEqual(ev["categoria"], "SOCIO ACTIVO")
        self.assertIsNotNone(ev["ult_cuota_paga"])
        # permitido + cuota al día -> "Acceso Concedido"
        self.assertTrue(ev["cuota_al_dia"])
        self.assertEqual(ev["mensaje"], "Acceso Concedido")
        self.assertEqual(ev["estado"], "ok")
        # la puerta viaja con su id local
        self.assertEqual(d["puerta"]["id"], self.door.id)

    def test_lectura_facial_vs_credencial(self):
        PantallaPuerta.objects.create(token=TOKEN, door=self.door)
        self._ev(8000, 59)   # ctrl 59 = molinete (Tipo_Cont K) -> credencial
        self._ev(8001, 90)   # ctrl 90 = facial (Tipo_Cont F) -> facial
        d = _get(self.client, "/api/xsys/puerta/estado/").json()
        by = {c["nombre"]: c for c in d["columnas"]}
        self.assertEqual(by["Alcorta Mol1"]["ultimo"]["lectura"], "credencial")
        self.assertEqual(by["Alcorta Facial"]["ultimo"]["lectura"], "facial")

    def test_mensaje_deducido_anomalia_amarillo(self):
        from datetime import datetime as _dt
        from django.utils import timezone as _tz
        # permitido pero cuota vencida -> anomalía (amarillo)
        XsysSocio.objects.filter(pk=944426).update(ult_cuota_paga=_tz.make_aware(_dt(2025, 1, 1)))
        PantallaPuerta.objects.create(token=TOKEN, door=self.door)
        self._ev(9200, 59, resultado="S")   # xSys dejó pasar, pero cuota vencida
        d = _get(self.client, "/api/xsys/puerta/estado/").json()
        col = next(c for c in d["columnas"] if 59 in c["controladores"])
        ev = col["ultimo"]
        self.assertTrue(ev["permitido"])
        self.assertFalse(ev["cuota_al_dia"])
        self.assertEqual(ev["estado"], "anomalia")
        self.assertEqual(ev["mensaje"], "Acceso Concedido · Cuota Vencida")

    def test_mensaje_deducido_cuota_vencida(self):
        from datetime import datetime as _dt
        from django.utils import timezone as _tz
        # socio con cuota muy vieja -> vencida
        XsysSocio.objects.filter(pk=944426).update(
            ult_cuota_paga=_tz.make_aware(_dt(2025, 1, 1)))
        PantallaPuerta.objects.create(token=TOKEN, door=self.door)
        self._ev(9000, 59, resultado="N")
        d = _get(self.client, "/api/xsys/puerta/estado/").json()
        col = next(c for c in d["columnas"] if 59 in c["controladores"])
        ev = col["ultimo"]
        self.assertFalse(ev["cuota_al_dia"])
        self.assertEqual(ev["mensaje"], "Cuota Vencida")

    def test_mensaje_deducido_chequear_oficina(self):
        # cuota al día pero acceso denegado por otra regla -> Chequear Oficina
        PantallaPuerta.objects.create(token=TOKEN, door=self.door)
        self._ev(9100, 59, resultado="N")   # socio 944426 tiene ult_cuota_paga=now (al día)
        d = _get(self.client, "/api/xsys/puerta/estado/").json()
        col = next(c for c in d["columnas"] if 59 in c["controladores"])
        ev = col["ultimo"]
        self.assertTrue(ev["cuota_al_dia"])
        self.assertEqual(ev["mensaje"], "Chequear Oficina de Socios")

    def test_estado_molinete_agrupa_controladores(self):
        # Columna "Molinete 1" agrupa el molinete 59 + su facial 90
        DoorTurnstileGroup.objects.create(door=self.door, nombre="Molinete 1", id_controladores=[59, 90], orden=0)
        DoorTurnstileGroup.objects.create(door=self.door, nombre="Molinete 2", id_controladores=[60], orden=1)
        PantallaPuerta.objects.create(token=TOKEN, door=self.door)
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

    def test_armado_puerta_flujo_3_pasos(self):
        """Alta de puerta -> asignar controladores -> definir grupo, todo por API."""
        user = User.objects.create_user("admin", password="pw")
        self.client.force_login(user)
        # 1) alta de la puerta
        r = self.client.post("/api/xsys/config/puertas/",
                             {"nombre": "Puerta Nueva", "xsys_id_acceso": 14},
                             content_type="application/json")
        self.assertEqual(r.status_code, 201)
        pid = r.json()["id"]
        # catálogo de controladores de xSys filtrado por acceso
        r = self.client.get("/api/xsys/config/controladores-xsys/?id_acceso=14")
        self.assertEqual(len(r.json()["controladores"]), 3)
        # 2) asignar controladores (pool)
        r = self.client.put("/api/xsys/config/puertas/%d/controladores/" % pid,
                            {"id_controladores": [59, 90]}, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["id_controladores"], [59, 90])
        r = self.client.get("/api/xsys/config/puertas/%d/controladores/" % pid)
        self.assertEqual(len(r.json()["controladores"]), 2)
        # 3) crear un grupo; solo acepta controladores del pool (60 se descarta)
        r = self.client.post("/api/xsys/config/molinetes/",
                             {"puerta_id": pid, "nombre": "Mol A", "id_controladores": [59, 90, 60]},
                             content_type="application/json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(sorted(r.json()["id_controladores"]), [59, 90])

    def test_config_crud_molinetes(self):
        user = User.objects.create_user("admin", password="pw")
        self.client.force_login(user)
        # crear
        r = self.client.post("/api/xsys/config/molinetes/",
                             {"puerta_id": self.door.id, "nombre": "Mol A", "id_controladores": [59, 90]},
                             content_type="application/json")
        self.assertEqual(r.status_code, 201)
        mid = r.json()["id"]
        # listar
        r = self.client.get("/api/xsys/config/molinetes/?puerta_id=%d" % self.door.id)
        self.assertEqual(len(r.json()["molinetes"]), 1)
        # auto (crea por los controladores asignados que faltan: 60)
        r = self.client.post("/api/xsys/config/molinetes/auto/", {"puerta_id": self.door.id}, content_type="application/json")
        self.assertGreaterEqual(r.json()["creados"], 1)
        # eliminar
        self.assertEqual(self.client.delete("/api/xsys/config/molinetes/%d/" % mid).status_code, 204)

    def test_config_requiere_login(self):
        self.assertIn(self.client.get("/api/xsys/config/molinetes/?puerta_id=%d" % self.door.id).status_code, (401, 403))
        self.assertIn(self.client.get("/api/xsys/config/puertas/").status_code, (401, 403))

    def test_socio_detalle_con_contratos(self):
        from xsys.models import XsysContrato
        XsysContrato.objects.create(id_contrato=1, id_cliente=944426, descripcion="CUOTA SOCIAL", activo=1)
        XsysContrato.objects.create(id_contrato=2, id_cliente=944426, descripcion="ED NATACION", activo=1)
        r = self.client.get("/api/xsys/socios/944426/detalle/")   # AllowAny
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["nombre"], "SIMOUR, GERMAN")
        self.assertEqual(d["categoria"], "SOCIO ACTIVO")
        self.assertIsNotNone(d["ult_cuota_paga"])
        descs = [c["descripcion"] for c in d["contratos"]]
        self.assertIn("ED NATACION", descs)
        self.assertIn("CUOTA SOCIAL", descs)

    def test_monitor_page_publico(self):
        self.assertEqual(self.client.get("/xsys/puerta/").status_code, 200)

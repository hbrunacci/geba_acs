"""El buscador del visor de molinetes.

Tenía dos agujeros: no miraba los pasos por FACIAL —que en Ombúes y Noble son
la mayoría de los ingresos del día, así que buscar a alguien que entró por la
cara no devolvía nada— y devolvía un dict reducido, con el que la fila no podía
abrir el modal de detalle.
"""

import datetime

from django.test import TestCase
from django.utils import timezone

from access_control.models import BiostarAccessEvent
from access_control.models.models import ExternalAccessLogEntry
from institutions.models import AccessDoor, DoorController, DoorTurnstileGroup
from xsys.models import PantallaPuerta, XsysAcceso, XsysControlador, XsysSocio

TOKEN = "token-buscador-abc"
CID = 944426
DEVICE = 777


class BuscadorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        XsysAcceso.objects.create(id_acceso=14, descripcion="SM-Alcorta", activo=1)
        XsysControlador.objects.create(id_controlador=59, id_acceso=14,
                                       descripcion="Alcorta Mol1", tipo_cont="K", activo=1)
        XsysSocio.objects.create(id_cliente=CID, apellido="SIMOUR", nombre="GERMAN",
                                 doc_nro=30111222, activo=1, categoria="SOCIO ACTIVO",
                                 ult_cuota_paga=timezone.now())
        XsysSocio.objects.create(id_cliente=999001, apellido="OTRO", nombre="PEPE",
                                 doc_nro=1, activo=1, ult_cuota_paga=timezone.now())
        cls.door = AccessDoor.objects.create(name="SM-Alcorta", xsys_id_acceso=14)
        DoorController.objects.create(door=cls.door, id_controlador=59, orden=0)
        DoorTurnstileGroup.objects.create(
            door=cls.door, nombre="Molinete 1", orden=0,
            id_controladores=[59], biostar_device_ids=[DEVICE])

    def setUp(self):
        PantallaPuerta.objects.create(token=TOKEN, door=self.door)

    def _buscar(self, q):
        return self.client.get("/api/xsys/accesos/buscar/?q=" + q,
                               HTTP_X_PANTALLA_TOKEN=TOKEN).json()

    def _molinete(self, id_es=5001, cid=CID):
        return ExternalAccessLogEntry.objects.create(
            external_id=id_es, tipo="E", id_cliente=cid, fecha=timezone.now(),
            resultado="S", id_acceso=14, id_controlador=59, observacion="ok")

    def _facial(self, bid="1", cid=CID):
        return BiostarAccessEvent.objects.create(
            biostar_id=bid, device_id=DEVICE, device_name="Facial", id_cliente=cid,
            fecha=timezone.now(), event_code=4867, event_name="VERIFY_SUCCESS",
            permitido=True)

    # -- el agujero del facial --------------------------------------------
    def test_encuentra_al_que_entro_por_facial(self):
        self._facial()
        res = self._buscar("SIMOUR")["resultados"]
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["lectura"], "facial")

    def test_mezcla_molinete_y_facial(self):
        self._molinete()
        self._facial()
        res = self._buscar("SIMOUR")["resultados"]
        self.assertEqual(len(res), 2)
        self.assertEqual({r["lectura"] for r in res}, {"credencial", "facial"})

    def test_los_dos_canales_dicen_por_que_molinete_paso(self):
        self._molinete()
        self._facial()
        for r in self._buscar("SIMOUR")["resultados"]:
            self.assertEqual(r["molinete"], "Molinete 1")

    def test_del_mas_nuevo_al_mas_viejo(self):
        self._molinete(5001)
        self._molinete(5002)
        res = self._buscar("SIMOUR")["resultados"]
        self.assertEqual([r["id_es"] for r in res], [5002, 5001])

    # -- lo que necesita el modal -----------------------------------------
    def test_el_resultado_trae_el_payload_completo(self):
        """Con el dict reducido de antes, la fila no podía abrir el detalle."""
        self._molinete()
        r = self._buscar("SIMOUR")["resultados"][0]
        for campo in ("id_es", "nombre", "mensaje", "estado", "categoria", "permitido",
                      "foto_url", "foto_thumb_url", "avisos", "contratos", "molinete"):
            self.assertIn(campo, r, campo)

    def test_el_payload_es_el_mismo_que_muestra_la_columna(self):
        self._molinete()
        col = self.client.get("/api/xsys/puerta/estado/",
                              HTTP_X_PANTALLA_TOKEN=TOKEN).json()["columnas"][0]
        r = dict(self._buscar("SIMOUR")["resultados"][0])
        r.pop("molinete")
        self.assertEqual(r, col["ultimo"])

    # -- filtros ----------------------------------------------------------
    def test_busca_por_dni(self):
        self._molinete()
        self.assertEqual(len(self._buscar("30111222")["resultados"]), 1)

    def test_busca_por_numero_de_socio(self):
        self._molinete()
        self.assertEqual(len(self._buscar(str(CID))["resultados"]), 1)

    def test_no_trae_a_otro_socio(self):
        self._molinete(5001)
        self._molinete(5002, cid=999001)
        res = self._buscar("SIMOUR")["resultados"]
        self.assertEqual([r["id_cliente"] for r in res], [CID])

    def test_menos_de_dos_letras_no_busca(self):
        self._molinete()
        self.assertEqual(self._buscar("S")["resultados"], [])

    def test_solo_el_dia_pedido(self):
        self._molinete()
        ExternalAccessLogEntry.objects.create(
            external_id=4000, tipo="E", id_cliente=CID,
            fecha=timezone.now() - datetime.timedelta(days=1), resultado="S",
            id_acceso=14, id_controlador=59, observacion="viejo")
        self.assertEqual(len(self._buscar("SIMOUR")["resultados"]), 1)

    def test_sin_token_da_400(self):
        self.assertEqual(
            self.client.get("/api/xsys/accesos/buscar/?q=SIMOUR").status_code, 400)

"""El día completo por molinete, sin reventar el sondeo del visor.

El visor mostraba 10 páginas de a 5: los 50 eventos que manda cada sondeo. Para
ver TODO el día no alcanza con subir ese número —las pantallas sondean 2 veces
por segundo y el día del facial más movido son casi 4 MB—, así que el día
completo se pide aparte, una vez, cuando el operador llega al final.
"""

from django.test import TestCase
from django.utils import timezone

from access_control.models import BiostarAccessEvent
from access_control.models.models import ExternalAccessLogEntry
from institutions.models import AccessDoor, DoorController
from xsys.api_views import HISTORIAL_DIA_MAX, HISTORIAL_LEN
from xsys.models import PantallaPuerta, XsysAcceso, XsysControlador, XsysSocio

TOKEN = "token-historial-abc"


class HistorialDelDiaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        XsysAcceso.objects.create(id_acceso=14, descripcion="SM-Alcorta", activo=1)
        XsysControlador.objects.create(id_controlador=59, id_acceso=14,
                                       descripcion="Alcorta Mol1", tipo_cont="K", activo=1)
        XsysSocio.objects.create(id_cliente=944426, apellido="SIMOUR", nombre="GERMAN",
                                 activo=1, categoria="SOCIO ACTIVO",
                                 ult_cuota_paga=timezone.now())
        cls.door = AccessDoor.objects.create(name="SM-Alcorta", xsys_id_acceso=14)
        DoorController.objects.create(door=cls.door, id_controlador=59, orden=0)

    def setUp(self):
        PantallaPuerta.objects.create(token=TOKEN, door=self.door)

    def _eventos(self, n):
        ahora = timezone.now()
        ExternalAccessLogEntry.objects.bulk_create([
            ExternalAccessLogEntry(
                external_id=1000 + i, tipo="E", id_cliente=944426, fecha=ahora,
                resultado="S", id_acceso=14, id_controlador=59, observacion="ok")
            for i in range(n)
        ])

    def _get(self, url):
        return self.client.get(url, HTTP_X_PANTALLA_TOKEN=TOKEN)

    def _key(self):
        d = self._get("/api/xsys/puerta/estado/").json()
        return d["columnas"][0]["key"]

    # -- el sondeo sigue acotado ------------------------------------------
    def test_el_sondeo_no_manda_el_dia_entero(self):
        """Si mandara todo, un kiosco que sondea 2 veces por segundo se ahoga."""
        self._eventos(HISTORIAL_LEN + 40)
        col = self._get("/api/xsys/puerta/estado/").json()["columnas"][0]
        self.assertEqual(len(col["historial"]), HISTORIAL_LEN)

    # -- el enddpoint del día completo ------------------------------------
    def test_devuelve_todos_los_del_dia(self):
        self._eventos(HISTORIAL_LEN + 40)
        d = self._get("/api/xsys/puerta/historial/?col=" + self._key()).json()
        self.assertEqual(d["total"], HISTORIAL_LEN + 40)
        self.assertFalse(d["truncado"])

    def test_viene_del_mas_nuevo_al_mas_viejo(self):
        self._eventos(10)
        h = self._get("/api/xsys/puerta/historial/?col=" + self._key()).json()["historial"]
        ids = [e["id_es"] for e in h]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_los_payloads_son_los_mismos_que_los_del_sondeo(self):
        """Si divergieran, el operador vería otra cosa al pasar de página."""
        self._eventos(3)
        estado = self._get("/api/xsys/puerta/estado/").json()["columnas"][0]
        dia = self._get("/api/xsys/puerta/historial/?col=" + self._key()).json()
        self.assertEqual(dia["historial"][0], estado["ultimo"])
        self.assertEqual(dia["historial"][1:], estado["historial"])

    def test_mezcla_los_faciales(self):
        self._eventos(2)
        from institutions.models import DoorTurnstileGroup
        BiostarAccessEvent.objects.create(
            biostar_id="9", device_id=777, device_name="Facial", id_cliente=944426,
            fecha=timezone.now(), event_code=4867, event_name="VERIFY_SUCCESS",
            permitido=True)
        d = self._get("/api/xsys/puerta/historial/?col=" + self._key()).json()
        self.assertGreaterEqual(d["total"], 2)

    def test_solo_de_ese_dia(self):
        import datetime
        self._eventos(3)
        ayer = timezone.now() - datetime.timedelta(days=1)
        ExternalAccessLogEntry.objects.create(
            external_id=1, tipo="E", id_cliente=944426, fecha=ayer, resultado="S",
            id_acceso=14, id_controlador=59, observacion="viejo")
        d = self._get("/api/xsys/puerta/historial/?col=" + self._key()).json()
        self.assertEqual(d["total"], 3)

    def test_una_columna_que_no_existe_da_404(self):
        self.assertEqual(self._get("/api/xsys/puerta/historial/?col=inventada").status_code, 404)

    def test_sin_token_da_400(self):
        self.assertEqual(
            self.client.get("/api/xsys/puerta/historial/?col=x").status_code, 400)

    def test_el_tope_existe_y_es_holgado(self):
        """Un día anómalo no puede armar una respuesta sin límite."""
        self.assertGreater(HISTORIAL_DIA_MAX, HISTORIAL_LEN * 10)


class ContextoCompartidoTests(TestCase):
    """Las dos vistas arman los payloads con el mismo contexto, en una pasada."""

    def test_devuelve_los_argumentos_de_los_dos_payloads(self):
        from xsys.api_views import _contexto_eventos
        ctx = _contexto_eventos([], [])
        self.assertEqual(len(ctx["xsys"]), 12)
        self.assertEqual(len(ctx["facial"]), 8)

    def test_sin_eventos_no_falla(self):
        from xsys.api_views import _contexto_eventos
        self.assertIsInstance(_contexto_eventos([], [])["xsys"][0], dict)

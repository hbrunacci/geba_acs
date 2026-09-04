"""El historial permanente de accesos por socio.

Los dos espejos que ya teníamos se purgan —CD_ES a los 7 días, los faciales a los
2— y están separados por canal. Esta tabla junta los dos y no se purga: es la que
contesta "¿cuándo entró esta persona y qué le dijo el molinete?".
"""

import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from access_control.models import BiostarAccessEvent, SocioAcceso
from access_control.models.models import ExternalAccessLogEntry
from access_control.services import historial_socio
from institutions.models import AccessDoor, DoorController, DoorTurnstileGroup
from xsys.models import XsysAcceso, XsysControlador, XsysMotivo

CID = 929935
CTRL = 59
DEVICE = 777


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        XsysAcceso.objects.create(id_acceso=14, descripcion="SM-Alcorta", activo=1)
        XsysControlador.objects.create(id_controlador=CTRL, id_acceso=14,
                                       descripcion="Alcorta Mol1", tipo_cont="K", activo=1)
        XsysMotivo.objects.create(id_cd_motivo=118, tipo="R",
                                  descripcion="Deuda de Actividades",
                                  descripcion_pantalla="Deuda de Actividades: pasá por Administración")
        cls.door = AccessDoor.objects.create(name="Alcorta", xsys_id_acceso=14)
        DoorController.objects.create(door=cls.door, id_controlador=CTRL, orden=0)
        DoorTurnstileGroup.objects.create(door=cls.door, nombre="Molinete 1", orden=0,
                                          id_controladores=[CTRL], biostar_device_ids=[DEVICE])

    def setUp(self):
        historial_socio.invalidar_cache()

    @staticmethod
    def _mov(external_id=5001, id_cliente=CID, resultado="S", motivo=None,
             observacion="Habilit. por Tipo de Cliente EMPLEADO", ctrl=CTRL):
        return ExternalAccessLogEntry(
            external_id=external_id, tipo="E", id_cliente=id_cliente,
            fecha=timezone.now(), resultado=resultado, id_acceso=14,
            id_controlador=ctrl, observacion=observacion, id_cd_motivo=motivo)

    @staticmethod
    def _facial(biostar_id="1", id_cliente=CID, permitido=True,
                event_name="VERIFY_SUCCESS", device=DEVICE):
        return BiostarAccessEvent(
            biostar_id=biostar_id, device_id=device, device_name="Facial Alcorta",
            id_cliente=id_cliente, fecha=timezone.now(), event_code=4867,
            event_name=event_name, permitido=permitido)


class MovimientosTests(_Base):
    def test_registra_el_paso(self):
        historial_socio.registrar_movimientos([self._mov()])
        a = SocioAcceso.objects.get(id_cliente=CID)
        self.assertTrue(a.permitido)
        self.assertEqual(a.origen, SocioAcceso.ORIGEN_CREDENCIAL)
        self.assertEqual(a.referencia, "cdes:5001")

    def test_ubica_puerta_y_molinete(self):
        historial_socio.registrar_movimientos([self._mov()])
        a = SocioAcceso.objects.get(id_cliente=CID)
        self.assertEqual(a.puerta, "Alcorta")
        self.assertEqual(a.molinete, "Molinete 1")

    def test_el_mensaje_sale_del_motivo_de_pantalla(self):
        historial_socio.registrar_movimientos([self._mov(motivo=118)])
        self.assertEqual(SocioAcceso.objects.get(id_cliente=CID).mensaje,
                         "Deuda de Actividades: pasá por Administración")

    def test_sin_motivo_queda_la_observacion(self):
        historial_socio.registrar_movimientos([self._mov()])
        self.assertEqual(SocioAcceso.objects.get(id_cliente=CID).mensaje,
                         "Habilit. por Tipo de Cliente EMPLEADO")

    def test_un_rechazo_queda_como_rechazo(self):
        historial_socio.registrar_movimientos([self._mov(resultado="N")])
        self.assertFalse(SocioAcceso.objects.get(id_cliente=CID).permitido)

    def test_reprocesar_el_mismo_evento_no_duplica(self):
        """Los pollers releen; el backfill se corre más de una vez."""
        historial_socio.registrar_movimientos([self._mov()])
        historial_socio.registrar_movimientos([self._mov()])
        self.assertEqual(SocioAcceso.objects.count(), 1)

    def test_saltea_lo_que_xsys_no_identifico(self):
        """Id_Cliente 0 es una lectura de nadie: este historial es por socio."""
        historial_socio.registrar_movimientos([self._mov(id_cliente=0)])
        self.assertEqual(SocioAcceso.objects.count(), 0)

    def test_un_controlador_sin_molinete_armado_usa_el_nombre_de_xsys(self):
        """Media docena de puertas del club no están armadas como molinetes.
        Para ésas, "BICI-Ombues" ubica y "Ctrl 999" no."""
        XsysControlador.objects.create(id_controlador=999, id_acceso=14,
                                       descripcion="BICI-Ombues", tipo_cont="L", activo=1)
        historial_socio.invalidar_cache()
        historial_socio.registrar_movimientos([self._mov(ctrl=999)])
        a = SocioAcceso.objects.get(id_cliente=CID)
        self.assertEqual(a.molinete, "BICI-Ombues")
        self.assertEqual(a.puerta, "SM-Alcorta")   # respaldo por CD_Accesos

    def test_un_controlador_desconocido_igual_se_guarda(self):
        historial_socio.registrar_movimientos([self._mov(ctrl=998)])
        self.assertEqual(SocioAcceso.objects.get(id_cliente=CID).molinete, "Ctrl 998")

    def test_nunca_rompe_la_ingesta(self):
        with patch.object(historial_socio, "_guardar", side_effect=RuntimeError("boom")):
            self.assertEqual(historial_socio.registrar_movimientos([self._mov()]), 0)


class FacialTests(_Base):
    def test_registra_el_paso_por_la_cara(self):
        historial_socio.registrar_faciales([self._facial()])
        a = SocioAcceso.objects.get(id_cliente=CID)
        self.assertEqual(a.origen, SocioAcceso.ORIGEN_FACIAL)
        self.assertEqual(a.molinete, "Molinete 1")
        self.assertEqual(a.mensaje, historial_socio.MENSAJE_FACIAL_OK)

    def test_un_rechazo_del_facial(self):
        historial_socio.registrar_faciales([
            self._facial(permitido=False, event_name="VERIFY_FAIL")])
        a = SocioAcceso.objects.get(id_cliente=CID)
        self.assertFalse(a.permitido)
        self.assertEqual(a.mensaje, historial_socio.MENSAJE_FACIAL_NO)
        self.assertEqual(a.detalle, "VERIFY_FAIL")

    def test_un_equipo_no_asignado_deja_el_nombre_del_equipo(self):
        historial_socio.registrar_faciales([self._facial(device=4242)])
        self.assertEqual(SocioAcceso.objects.get(id_cliente=CID).molinete, "Facial Alcorta")

    def test_los_dos_canales_conviven_en_la_misma_ficha(self):
        """El socio entra por credencial y por la cara: el historial es uno solo."""
        historial_socio.registrar_movimientos([self._mov()])
        historial_socio.registrar_faciales([self._facial()])
        self.assertEqual(
            set(SocioAcceso.objects.filter(id_cliente=CID).values_list("origen", flat=True)),
            {SocioAcceso.ORIGEN_CREDENCIAL, SocioAcceso.ORIGEN_FACIAL})


class SobreviveALaPurgaTests(_Base):
    """El motivo de existir de la tabla."""

    def test_el_historial_queda_aunque_se_purguen_los_espejos(self):
        viejo = self._mov()
        viejo.fecha = timezone.now() - datetime.timedelta(days=40)
        viejo.save()
        historial_socio.registrar_movimientos([viejo])

        from xsys.services.sync import XsysSyncService

        XsysSyncService({"CD_ES_RETENTION_DAYS": 7}).purge_old_movements()
        self.assertEqual(ExternalAccessLogEntry.objects.count(), 0)
        self.assertEqual(SocioAcceso.objects.filter(id_cliente=CID).count(), 1)

    def test_la_purga_de_faciales_tampoco_lo_toca(self):
        from access_control.services import biostar_events

        ev = self._facial()
        ev.fecha = timezone.now() - datetime.timedelta(days=10)
        ev.save()
        historial_socio.registrar_faciales([ev])

        biostar_events.purge_old(2)
        self.assertEqual(BiostarAccessEvent.objects.count(), 0)
        self.assertEqual(SocioAcceso.objects.filter(id_cliente=CID).count(), 1)


class CableadoTests(_Base):
    """Que las dos ingestas lo llamen de verdad. Sin esto, el historial arranca
    vacío y se llena sólo si alguien corre el backfill a mano."""

    def test_la_sincronizacion_de_cd_es_lo_registra(self):
        from xsys.services.sync import XsysSyncService

        class Cursor:
            def __init__(self):
                self._servido = False
                self._last = ""

            def execute(self, sql, params=()):
                self._last = sql
                return self

            def fetchmany(self, n):
                if "FROM CD_ES" in self._last and not self._servido:
                    self._servido = True
                    return [(
                        8534484, "E", "F", "311363", CID,
                        datetime.datetime(2026, 9, 2, 15, 10, 13), "S", CTRL, 14,
                        "Habilit. por Tipo de Cliente EMPLEADO", "reg", None, "0", None, None,
                    )]
                return []

        self.assertEqual(XsysSyncService().sync_movements(Cursor()), 1)
        a = SocioAcceso.objects.get(id_cliente=CID)
        self.assertEqual(a.referencia, "cdes:8534484")
        self.assertEqual(a.molinete, "Molinete 1")

    def test_la_ingesta_de_biostar_lo_registra(self):
        from access_control.services import biostar_events

        evento = {
            "id": "77001",
            "event_type_id": {"code": "4867"},
            "datetime": "2026-09-02T15:10:13.00Z",
            "device_id": {"id": str(DEVICE), "name": "Facial Alcorta"},
            "user_id": {"user_id": str(CID)},
        }
        self.assertTrue(biostar_events._store_event({"4867": "VERIFY_SUCCESS"}, evento))
        a = SocioAcceso.objects.get(id_cliente=CID)
        self.assertEqual(a.origen, SocioAcceso.ORIGEN_FACIAL)
        self.assertEqual(a.referencia, "biostar:77001")


class ComandoTests(_Base):
    """El comando de relleno: recupera lo que los espejos todavía tengan."""

    def _correr(self):
        from io import StringIO

        from django.core.management import call_command

        salida = StringIO()
        call_command("historial_socio_backfill", stdout=salida)
        return salida.getvalue()

    def test_registra_lo_que_esta_en_los_espejos(self):
        self._mov().save()
        self._facial().save()
        self._correr()
        self.assertEqual(SocioAcceso.objects.count(), 2)

    def test_correrlo_dos_veces_no_duplica(self):
        self._mov().save()
        self._correr()
        self._correr()
        self.assertEqual(SocioAcceso.objects.count(), 1)

    def test_no_lee_la_historia_de_cd_es(self):
        """El club pidió que el historial arranque con lo que registra este
        sistema. Si el comando volviera a pegarle a xSys, esto lo señala."""
        import inspect

        from access_control.management.commands import historial_socio_backfill

        codigo = inspect.getsource(historial_socio_backfill)
        self.assertNotIn("FROM CD_ES", codigo)
        self.assertNotIn("MSSQL_XSYS", codigo)


class CacheTests(_Base):
    def test_no_relee_los_catalogos_en_cada_evento(self):
        """El poller de BioStar persiste de a uno: sin caché serían 4 consultas por cara."""
        historial_socio.registrar_faciales([self._facial(biostar_id="1")])
        with patch.object(historial_socio, "_armar_contexto") as armar:
            historial_socio.registrar_faciales([self._facial(biostar_id="2")])
            historial_socio.registrar_faciales([self._facial(biostar_id="3")])
        armar.assert_not_called()

    def test_invalidar_fuerza_a_releer(self):
        historial_socio._contexto()
        historial_socio.invalidar_cache()
        with patch.object(historial_socio, "_armar_contexto", return_value={}) as armar:
            historial_socio._contexto()
        armar.assert_called_once()

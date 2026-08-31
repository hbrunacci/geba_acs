"""Bajas en revisión: el socio pasa, pero el visor lo marca y queda el aviso.

Contexto: el 28/08/2026 un proceso externo marcó 1.259 socios como fallecidos
sin dejar rastro en la auditoría de xSys. Mientras la oficina de Socios revisa,
esa gente entra igual (lo decide xSys), y acá se comprueba lo que hacemos
nosotros: avisarle al operador y dejar el pendiente en el legajo.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from access_control.models import SocioAviso
from access_control.models.models import ExternalAccessLogEntry
from xsys import api_views as A
from xsys.models import XsysBajaRevision, XsysSocio
from xsys.services.sync import XsysSyncService

CID = 872811
OTRO = 915493


def _socio(id_cliente=CID, **kw):
    base = {
        "id_cliente": id_cliente, "doc_nro": 4404953, "apellido": "MURAS",
        "nombre": "DELIA", "categoria": "VITALICIO + 71", "id_tipo_cli": 1010,
        "activo": 0, "ult_cuota_paga": timezone.now(),
    }
    base.update(kw)
    return XsysSocio.objects.create(**base)


def _evento(id_cliente=CID, resultado="S", **kw):
    base = {
        "external_id": 1, "id_cliente": id_cliente, "fecha": timezone.now(),
        "resultado": resultado, "id_acceso": 12, "id_controlador": 45,
        "observacion": "Habilit. por Tipo de Cliente VITALICIO + 71",
    }
    base.update(kw)
    return ExternalAccessLogEntry(**base)


class BajasEnRevisionTests(TestCase):
    def setUp(self):
        XsysBajaRevision.objects.create(
            id_cliente=CID, origen="proceso_masivo_20260828", en_revision=True)

    def test_lista_solo_los_que_siguen_en_revision(self):
        XsysBajaRevision.objects.create(id_cliente=OTRO, en_revision=False)
        self.assertEqual(A._bajas_en_revision([CID, OTRO]), {CID})

    def test_sin_ids_no_consulta(self):
        self.assertEqual(A._bajas_en_revision([]), set())
        self.assertEqual(A._bajas_en_revision([0, None]), set())


class PayloadDelVisorTests(TestCase):
    def setUp(self):
        self.socio = _socio()
        self.socios = {CID: self.socio}
        XsysBajaRevision.objects.create(id_cliente=CID, en_revision=True)

    def _payload(self, ev, en_revision=None):
        return A._evento_payload(ev, self.socios, set(), {}, {}, {}, {}, set(), {},
                                 {CID: "voluntaria"}, en_revision)

    def test_marca_al_socio_que_pasa_con_la_baja_en_revision(self):
        p = self._payload(_evento(), {CID})
        self.assertTrue(p["baja_en_revision"])
        self.assertEqual(p["estado"], "anomalia")
        self.assertIn("dado de baja por error", p["mensaje"])

    def test_no_marca_a_quien_no_esta_en_la_lista(self):
        p = self._payload(_evento(), set())
        self.assertFalse(p["baja_en_revision"])
        self.assertEqual(p["estado"], "ok")

    def test_no_pisa_un_rechazo_por_otro_motivo(self):
        """Si no pasó, el motivo real es el que importa: no se lo tapa."""
        p = self._payload(_evento(resultado="N"), {CID})
        self.assertTrue(p["baja_en_revision"])
        self.assertEqual(p["estado"], "no")
        self.assertNotIn("dado de baja por error", p["mensaje"])

    def test_el_paso_pendiente_sigue_teniendo_prioridad(self):
        ev = _evento()
        ev.conflicto_molinete = "Molinete 2"
        p = self._payload(ev, {CID})
        self.assertEqual(p["estado"], "no")
        self.assertIn("Paso pendiente", p["mensaje"])

    def test_los_eventos_faciales_se_marcan_igual(self):
        from access_control.models import BiostarAccessEvent

        ev = BiostarAccessEvent(
            biostar_id="1", device_id=1, device_name="Facial Alcorta",
            id_cliente=CID, fecha=timezone.now(), event_code=4867,
            event_name="VERIFY_SUCCESS", permitido=True)
        ev.id = 1   # el payload usa -ev.id como clave; sin guardar en la base
        p = A._facial_evento_payload(ev, self.socios, set(), {}, {},
                                     {CID: "voluntaria"}, {CID})
        self.assertTrue(p["baja_en_revision"])
        self.assertEqual(p["estado"], "anomalia")


class AvisoAutomaticoTests(TestCase):
    def setUp(self):
        _socio()
        XsysBajaRevision.objects.create(id_cliente=CID, en_revision=True)
        self.svc = XsysSyncService({})

    def test_deja_el_aviso_cuando_el_socio_pasa(self):
        self.svc._avisar_baja_en_revision([_evento()])
        aviso = SocioAviso.objects.get(id_cliente=CID)
        self.assertEqual(aviso.tipo, SocioAviso.TIPO_DATOS_A_ACTUALIZAR)
        self.assertEqual(aviso.creado_por, "sistema")
        self.assertIn("oficina de socios", aviso.texto)

    def test_no_repite_el_aviso_el_mismo_dia(self):
        for i in range(3):
            self.svc._avisar_baja_en_revision([_evento(external_id=i + 1)])
        self.assertEqual(SocioAviso.objects.filter(id_cliente=CID).count(), 1)

    def test_vuelve_a_avisar_al_dia_siguiente(self):
        SocioAviso.objects.create(
            id_cliente=CID, tipo=SocioAviso.TIPO_DATOS_A_ACTUALIZAR,
            texto=SocioAviso.TEXTO_DATOS_A_ACTUALIZAR,
            created_at=timezone.now() - timedelta(days=1))
        self.svc._avisar_baja_en_revision([_evento()])
        self.assertEqual(SocioAviso.objects.filter(id_cliente=CID).count(), 2)

    def test_no_avisa_de_un_socio_ya_revisado(self):
        XsysBajaRevision.objects.filter(id_cliente=CID).update(en_revision=False)
        self.svc._avisar_baja_en_revision([_evento()])
        self.assertFalse(SocioAviso.objects.exists())

    def test_no_avisa_de_quien_no_esta_en_la_lista(self):
        self.svc._avisar_baja_en_revision([_evento(id_cliente=OTRO)])
        self.assertFalse(SocioAviso.objects.exists())

    def test_ignora_los_eventos_sin_socio_identificado(self):
        self.svc._avisar_baja_en_revision([_evento(id_cliente=0)])
        self.assertFalse(SocioAviso.objects.exists())

    def test_un_fallo_no_rompe_la_ingesta(self):
        """El aviso es accesorio: perderlo es menor, perder el movimiento no."""
        roto = object()   # sin .id_cliente -> revienta adentro
        self.svc._avisar_baja_en_revision([roto])   # no debe propagar


class SyncDeLaTablaTests(TestCase):
    """El espejo se arma desde xSys; acá se fija el contrato del cursor."""

    class _Cur:
        def __init__(self, existe, filas):
            self._existe = existe
            self._filas = filas
            self._ultimo = None

        def execute(self, sql, *a):
            self._ultimo = "OBJECT_ID" if "OBJECT_ID" in sql else "SELECT"
            return self

        def fetchone(self):
            return (1 if self._existe else None,)

        def fetchall(self):
            return self._filas

    def test_si_la_tabla_no_existe_en_xsys_devuelve_cero(self):
        svc = XsysSyncService({})
        self.assertEqual(svc.sync_bajas_revision(self._Cur(False, [])), 0)

    def test_espeja_las_filas(self):
        svc = XsysSyncService({})
        filas = [(CID, "proceso_masivo_20260828", True, None, 3, "pendiente"),
                 (OTRO, "proceso_masivo_20260828", False, None, 3, "revisado")]
        self.assertEqual(svc.sync_bajas_revision(self._Cur(True, filas)), 2)
        self.assertEqual(XsysBajaRevision.objects.count(), 2)
        self.assertTrue(XsysBajaRevision.objects.get(id_cliente=CID).en_revision)
        self.assertFalse(XsysBajaRevision.objects.get(id_cliente=OTRO).en_revision)

    def test_borra_los_que_xsys_ya_no_lista(self):
        XsysBajaRevision.objects.create(id_cliente=999999, en_revision=True)
        svc = XsysSyncService({})
        filas = [(CID, "x", True, None, 3, "")]
        svc.sync_bajas_revision(self._Cur(True, filas))
        self.assertEqual(list(XsysBajaRevision.objects.values_list("id_cliente", flat=True)), [CID])

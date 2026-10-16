"""Apto físico de los deportistas: etiqueta en el monitor, sin frenar a nadie.

Se avisa sólo a quien tiene un deporte vigente y no presentó el apto, o lo tiene
vencido. Es una etiqueta: no cambia el color del marco ni el mensaje, porque el
amarillo es para la deuda y 9 de cada 10 deportistas todavía no lo presentaron.
"""

import datetime as dt

from django.test import TestCase, override_settings
from django.utils import timezone

from access_control.models.models import ExternalAccessLogEntry
from xsys import api_views as A
from xsys.models import XsysAptoFisico, XsysDeudaActividades, XsysSocio
from xsys.services import apto_fisico as S

CID_SIN = 910001
CID_VENCIDO = 910002
CID_VIGENTE = 910003
CID_NO_DEPORTISTA = 910004

HOY = timezone.localdate()


def _socio(id_cliente):
    return XsysSocio.objects.create(
        id_cliente=id_cliente, apellido="PEREZ", nombre="JUAN", doc_nro=id_cliente,
        categoria="CADETE", id_tipo_cli=1001, activo=1, ult_cuota_paga=timezone.now())


def _evento(id_cliente, resultado="S"):
    return ExternalAccessLogEntry(
        external_id=id_cliente, id_cliente=id_cliente, fecha=timezone.now(),
        resultado=resultado, id_acceso=14, id_controlador=59,
        observacion="Habilit. por Produc. Comprado CUOTA SOCIAL")


class EstadoTests(TestCase):
    def test_sin_fecha_es_sin_apto(self):
        a = XsysAptoFisico(id_cliente=1, vence=None)
        self.assertEqual(a.estado(HOY), "sin_apto")
        self.assertEqual(a.texto(HOY), "Sin Apto Físico")

    def test_vencido_dice_desde_cuando(self):
        a = XsysAptoFisico(id_cliente=1, vence=dt.date(2026, 3, 1))
        self.assertEqual(a.estado(dt.date(2026, 10, 15)), "vencido")
        self.assertEqual(a.texto(dt.date(2026, 10, 15)), "Apto Físico vencido 01/03/2026")

    def test_vence_hoy_todavia_vale(self):
        a = XsysAptoFisico(id_cliente=1, vence=HOY)
        self.assertEqual(a.estado(HOY), "vigente")
        self.assertEqual(a.texto(HOY), "")


class CopiaTests(TestCase):
    class _Cur:
        def __init__(self, filas):
            self.filas, self.params = filas, None

        def execute(self, sql, params):
            self.sql, self.params = sql, params

        def fetchall(self):
            return self.filas

    @override_settings(XSYS_APTO_FAMILIAS=(23, 34))
    def test_consulta_solo_las_familias_configuradas(self):
        cur = self._Cur([])
        S.leer(cur)
        self.assertEqual(cur.params, ["AF", 23, 34])

    @override_settings(XSYS_APTO_FAMILIAS=())
    def test_sin_familias_no_consulta(self):
        cur = self._Cur([])
        self.assertEqual(S.leer(cur), {})
        self.assertIsNone(cur.params)

    def test_junta_los_deportes_de_cada_persona(self):
        filas = [(CID_VENCIDO, 23, "Torneo Futbol", dt.datetime(2026, 1, 5)),
                 (CID_VENCIDO, 34, "Rugby", dt.datetime(2026, 1, 5)),
                 (CID_SIN, 23, "Torneo Futbol", None)]
        datos = S._agrupar(filas)
        self.assertEqual(datos[CID_VENCIDO]["deportes"], ["Torneo Futbol", "Rugby"])
        self.assertEqual(datos[CID_VENCIDO]["vence"], dt.date(2026, 1, 5))
        self.assertIsNone(datos[CID_SIN]["vence"])

    def test_la_fecha_de_relleno_de_1950_es_sin_apto(self):
        datos = S._agrupar([(CID_SIN, 23, "Torneo Futbol", dt.datetime(1950, 1, 1))])
        self.assertIsNone(datos[CID_SIN]["vence"])

    def test_la_de_2200_cuenta_como_vigente(self):
        datos = S._agrupar([(CID_VIGENTE, 23, "Torneo Futbol", dt.datetime(2200, 1, 1))])
        S.guardar(datos)
        self.assertEqual(XsysAptoFisico.objects.get(pk=CID_VIGENTE).estado(HOY), "vigente")

    def test_reemplaza_la_copia_y_saca_al_que_dejo_el_deporte(self):
        XsysAptoFisico.objects.create(id_cliente=999999, vence=None)
        inf = S.guardar({CID_SIN: {"vence": None, "deportes": ["Rugby"]},
                         CID_VENCIDO: {"vence": dt.date(2020, 1, 1), "deportes": ["Voley"]}})
        self.assertEqual(set(XsysAptoFisico.objects.values_list("id_cliente", flat=True)),
                         {CID_SIN, CID_VENCIDO})
        self.assertEqual((inf["deportistas"], inf["sin_apto"], inf["vencidos"], inf["salieron"]),
                         (2, 1, 1, 1))


class VisorTests(TestCase):
    def setUp(self):
        self.socios = {c: _socio(c) for c in (CID_SIN, CID_VENCIDO, CID_VIGENTE, CID_NO_DEPORTISTA)}
        XsysAptoFisico.objects.create(id_cliente=CID_SIN, vence=None, deportes="Rugby")
        XsysAptoFisico.objects.create(id_cliente=CID_VENCIDO, vence=HOY - dt.timedelta(days=1))
        XsysAptoFisico.objects.create(id_cliente=CID_VIGENTE, vence=HOY + dt.timedelta(days=200))

    def _payload(self, cid, resultado="S"):
        apto = A._apto_fisico([cid])
        deuda = A._deuda_actividades([cid])
        return A._evento_payload(_evento(cid, resultado), self.socios, set(), {}, {}, {},
                                 {}, set(), {}, {cid: "producto"}, set(), {}, deuda, apto)

    def test_sin_apto_pasa_en_verde_con_la_etiqueta(self):
        p = self._payload(CID_SIN)
        self.assertEqual(p["estado"], "ok")                 # no cambia el color
        self.assertEqual(p["mensaje"], "Acceso Concedido")  # ni el mensaje
        self.assertEqual(p["apto_fisico"]["estado"], "sin_apto")
        self.assertEqual(p["apto_fisico"]["texto"], "Sin Apto Físico")

    def test_vencido_lleva_la_etiqueta_con_la_fecha(self):
        p = self._payload(CID_VENCIDO)
        self.assertEqual(p["estado"], "ok")
        self.assertIn("vencido", p["apto_fisico"]["texto"])

    def test_vigente_no_lleva_nada(self):
        self.assertIsNone(self._payload(CID_VIGENTE)["apto_fisico"])

    def test_al_que_no_hace_deporte_no_se_le_controla(self):
        self.assertIsNone(self._payload(CID_NO_DEPORTISTA)["apto_fisico"])

    def test_no_tapa_el_amarillo_de_la_deuda(self):
        XsysDeudaActividades.objects.create(id_cliente=CID_SIN, cuotas=2, bloquea=False, activo=True)
        p = self._payload(CID_SIN)
        self.assertEqual(p["estado"], "anomalia")
        self.assertIn("Deuda de Actividades", p["mensaje"])
        self.assertEqual(p["apto_fisico"]["estado"], "sin_apto")

    def test_tambien_en_el_facial(self):
        from access_control.models import BiostarAccessEvent
        ev = BiostarAccessEvent(id=1, id_cliente=CID_SIN, permitido=True,
                                fecha=timezone.now(), event_name="VERIFY_SUCCESS")
        p = A._facial_evento_payload(ev, self.socios, set(), {}, {}, {}, set(), {}, {},
                                     A._apto_fisico([CID_SIN]))
        self.assertEqual(p["apto_fisico"]["texto"], "Sin Apto Físico")

    def test_sin_ids_no_consulta(self):
        self.assertEqual(A._apto_fisico([]), {})

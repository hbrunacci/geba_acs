"""Por qué el facial rechazó a alguien, y qué debe, en el monitor de la garita.

El caso que lo pidió: la ficha 892646 aparecía en el facial con "Acceso
Denegado" y sus contratos vigentes en verde. Estaba frenada por seis cuotas de
un vóley ya dado de baja, que no figuraba en ningún lado de la pantalla.
"""

from __future__ import annotations

import datetime as dt
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from access_control.models import BiostarAccessEvent
from xsys import api_views as A
from xsys.models import XsysSocio, XsysWhitelist
from xsys.services import deuda_actividades as da

CID = 920001


def _socio(id_cliente):
    return XsysSocio.objects.create(
        id_cliente=id_cliente, apellido="PEREZ", nombre="ANA", doc_nro=id_cliente,
        categoria="INFANTIL", id_tipo_cli=1000, activo=1, ult_cuota_paga=timezone.now())


def _facial(id_cliente, permitido=False):
    return BiostarAccessEvent(id=1, id_cliente=id_cliente, permitido=permitido,
                              fecha=timezone.now(), event_name="ACCESS_DENIED_EXPIRED")


class MotivoEnElMonitorTests(TestCase):
    def setUp(self):
        self.socios = {CID: _socio(CID)}

    def _payload(self, permitido=False):
        rechazos = A._motivos_rechazo([CID])
        return A._facial_evento_payload(_facial(CID, permitido), self.socios, set(), {}, {}, {},
                                        set(), {}, {}, {}, rechazos)

    def test_el_rechazo_dice_el_motivo_de_la_lista_blanca(self):
        XsysWhitelist.objects.create(id_cliente=CID, habilitado=False, motivo_code=118,
                                     motivo="Deuda de Actividades")
        p = self._payload()
        self.assertEqual(p["estado"], "no")
        self.assertEqual(p["mensaje"], "Acceso Denegado · Deuda de Actividades")

    def test_sin_motivo_queda_como_estaba(self):
        self.assertEqual(self._payload()["mensaje"], "Acceso Denegado")

    def test_el_habilitado_no_toma_motivo(self):
        XsysWhitelist.objects.create(id_cliente=CID, habilitado=True,
                                     motivo="Habilitado por producto comprado")
        self.assertEqual(A._motivos_rechazo([CID]), {})

    def test_si_pasa_no_se_le_pega_el_motivo(self):
        XsysWhitelist.objects.create(id_cliente=CID, habilitado=False, motivo="Persona inactiva")
        self.assertEqual(self._payload(permitido=True)["mensaje"], "Acceso Concedido")


class _Cur:
    def __init__(self, filas):
        self.filas, self.params, self.sql = filas, None, ""

    def execute(self, sql, params=()):
        self.sql, self.params = " ".join(sql.split()), list(params)

    def fetchall(self):
        return self.filas


class _Conn:
    def __init__(self, cur):
        self.cur, self.cerrada = cur, False

    def cursor(self):
        return self.cur

    def close(self):
        self.cerrada = True


class DetalleSocioTests(TestCase):
    def test_abre_la_deuda_por_contrato_aunque_este_dado_de_baja(self):
        cur = _Cur([(754550, 0, dt.datetime(2026, 9, 5), "Voley ", 6, 298300,
                     dt.datetime(2026, 4, 1), dt.datetime(2026, 9, 1))])
        conn = _Conn(cur)
        with mock.patch.object(da, "_conectar", return_value=conn):
            filas = da.detalle_socio(892646)
        self.assertTrue(conn.cerrada)
        self.assertEqual(filas, [{
            "id_contrato": 754550, "actividad": "Voley", "contrato_activo": False,
            "fecha_baja": "2026-09-05", "cuotas": 6, "importe": 298300.0,
            "desde": "2026-04-01", "hasta": "2026-09-01"}])

    def test_mismo_criterio_que_el_bloqueo(self):
        cur = _Cur([])
        with mock.patch.object(da, "_conectar", return_value=_Conn(cur)):
            da.detalle_socio(892646)
        self.assertEqual(cur.params[0], dt.datetime(2026, 1, 1))   # desde el ejercicio
        self.assertEqual(cur.params[-1], 892646)
        self.assertIn("CC.Saldo > 0", cur.sql)
        self.assertNotIn("Activo = 1", cur.sql)                      # incluye los dados de baja


class EndpointTests(TestCase):
    def test_devuelve_el_detalle_y_los_totales(self):
        deudas = [{"cuotas": 2, "importe": 103900.0}, {"cuotas": 1, "importe": 100.0}]
        with mock.patch.object(da, "detalle_socio", return_value=deudas):
            r = APIClient().get(f"/api/xsys/socios/{CID}/deuda-actividades/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.data["cuotas"], r.data["importe"]), (3, 104000.0))

    def test_xsys_caido_no_rompe_el_modal(self):
        with mock.patch.object(da, "detalle_socio", side_effect=da.RevisionError("sin red")):
            r = APIClient().get(f"/api/xsys/socios/{CID}/deuda-actividades/")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.data["deudas"], [])

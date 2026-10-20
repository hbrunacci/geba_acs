"""Rechazo de barrera por cuota de cochera impaga.

xSys dice "No cumple ninguna condición habilitante" (112) y el visor mostraba
"Chequear Oficina de Socios". Con un cupón de la cochera impago, el operador
tiene que ver "Cochera impaga" y de qué mes: es lo que le explica al socio.
"""

import datetime as dt
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from access_control.models.models import ExternalAccessLogEntry
from xsys import api_views as A
from xsys.models import XsysSocio
from xsys.services import cochera

CID = 900201
BARRERA = 18
NOBLE = "COCHERA MENSUAL NOBLE Nro. 109"


def _evento(motivo=112, resultado="E", acceso=BARRERA, cuando=None):
    return ExternalAccessLogEntry(
        external_id=1, id_cliente=CID, resultado=resultado, id_acceso=acceso,
        id_controlador=67, id_cd_motivo=motivo,
        fecha=cuando or timezone.make_aware(dt.datetime(2026, 10, 13, 19, 22)),
        observacion="No cumple ninguna condición habilitante")


class CocheraImpagaTests(TestCase):

    def setUp(self):
        cache.clear()
        self.socios = {CID: XsysSocio.objects.create(
            id_cliente=CID, apellido="PEREZ", nombre="ANA", doc_nro=CID, categoria="ACTIVO",
            id_tipo_cli=1002, activo=1, ult_cuota_paga=timezone.now())}

    def _payload(self, ev, cupones):
        with mock.patch.object(cochera, "_consultar",
                               side_effect=lambda pares: {p: cupones for p in pares}):
            imp = A._cochera_impaga([ev], {BARRERA})
        return A._evento_payload(ev, self.socios, set(), {}, {}, {}, {}, {BARRERA}, {}, {},
                                 set(), {}, {}, {}, imp)

    def test_cuota_del_mes_impaga(self):
        p = self._payload(_evento(), [(dt.date(2026, 10, 1), NOBLE), (dt.date(2026, 11, 1), NOBLE)])
        self.assertEqual(p["estado"], "no")
        # La de noviembre ya está emitida pero todavía no se debe.
        self.assertEqual(p["mensaje"], "Cochera impaga · MENSUAL NOBLE 109 · octubre")
        self.assertTrue(p["cochera_impaga"])

    def test_varias_cuotas(self):
        p = self._payload(_evento(), [(dt.date(2026, 9, 1), NOBLE), (dt.date(2026, 10, 1), NOBLE)])
        self.assertEqual(p["mensaje"], "Cochera impaga · MENSUAL NOBLE 109 · 2 cuotas desde septiembre")

    def test_al_dia_sigue_el_mensaje_de_siempre(self):
        p = self._payload(_evento(), [(dt.date(2026, 11, 1), NOBLE)])
        self.assertEqual(p["mensaje"], "Chequear Oficina de Socios")
        self.assertFalse(p["cochera_impaga"])

    def test_otro_motivo_no_se_atribuye_a_la_cochera(self):
        p = self._payload(_evento(motivo=108), [(dt.date(2026, 10, 1), NOBLE)])
        self.assertEqual(p["mensaje"], "Chequear Oficina de Socios")

    def test_molinete_peatonal_no_consulta(self):
        with mock.patch.object(cochera, "_consultar") as consultar:
            self.assertEqual(A._cochera_impaga([_evento(acceso=14)], {BARRERA}), {})
        consultar.assert_not_called()

    def test_cachea_y_si_clever_no_responde_no_rompe(self):
        ev = _evento()
        with mock.patch.object(cochera, "_consultar", side_effect=RuntimeError("caído")):
            self.assertEqual(A._cochera_impaga([ev], {BARRERA}), {})
        with mock.patch.object(cochera, "_consultar", return_value={(CID, BARRERA): []}) as c1:
            A._cochera_impaga([ev], {BARRERA})
            A._cochera_impaga([ev], {BARRERA})
        self.assertEqual(c1.call_count, 1)

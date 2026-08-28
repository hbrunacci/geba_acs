"""Regla de paso pendiente: que marque el reuso real y NADA más.

El caso que motivó estos tests: un socio que pasaba normalmente por un facial
aparecía como "Paso pendiente" cada vez. No estaba reusando nada — su único cruce
se registraba dos veces, por BioStar y por xSys, y la regla los tomaba por dos
molinetes distintos. Medido sobre 3 días reales: 3.688 conflictos, de los cuales
sólo 4 eran genuinos.
"""

from __future__ import annotations

import datetime as dt

from django.test import TestCase
from django.utils import timezone

from access_control.models import PasoPendiente
from access_control.services import paso_pendiente as pp
from institutions.models import AccessDoor, DoorTurnstileGroup
from xsys.models import XsysControlador

SOCIO = 944426


class ControladoresPuenteTests(TestCase):
    """Los 'Sup BioStar API' no son molinetes: son el aviso del facial a xSys."""

    @classmethod
    def setUpTestData(cls):
        XsysControlador.objects.create(id_controlador=59, id_acceso=14,
                                       descripcion="Alcorta Mol1", tipo_cont="K", activo=1)
        XsysControlador.objects.create(id_controlador=68, id_acceso=4,
                                       descripcion="Sup BioStar API Alcorta", tipo_cont="F", activo=1)
        XsysControlador.objects.create(id_controlador=56, id_acceso=20,
                                       descripcion="Sup BioStar API Futbol", tipo_cont="W", activo=1)

    def test_detecta_los_puentes_por_tipo(self):
        self.assertEqual(pp.controladores_puente(), {68, 56})

    def test_un_molinete_de_verdad_no_es_puente(self):
        self.assertNotIn(59, pp.controladores_puente())


class EvaluarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        door = AccessDoor.objects.create(name="Ombues", xsys_id_acceso=15)
        DoorTurnstileGroup.objects.create(door=door, nombre="Molinete 1",
                                          id_controladores=[55], biostar_device_ids=[])
        DoorTurnstileGroup.objects.create(door=door, nombre="Molinete 2",
                                          id_controladores=[61], biostar_device_ids=[538150641])

    def setUp(self):
        PasoPendiente.objects.all().delete()
        self.mapa = pp.mapa_molinetes()

    def _mol(self, **kw):
        return pp.resolver_molinete(self.mapa, **kw)

    def test_revalidar_en_el_mismo_molinete_no_es_conflicto(self):
        """Es la persona que todavía no cruzó, no un reuso."""
        ahora = timezone.now()
        self.assertEqual(pp.evaluar(SOCIO, self._mol(id_controlador=55), cuando=ahora), "")
        self.assertEqual(
            pp.evaluar(SOCIO, self._mol(id_controlador=55), cuando=ahora + dt.timedelta(seconds=2)), "")

    def test_otro_molinete_dentro_de_la_ventana_es_conflicto(self):
        ahora = timezone.now()
        pp.evaluar(SOCIO, self._mol(id_controlador=55), cuando=ahora)
        self.assertEqual(
            pp.evaluar(SOCIO, self._mol(id_controlador=61), cuando=ahora + dt.timedelta(seconds=2)),
            "Molinete 1")

    def test_pasada_la_ventana_ya_no_es_conflicto(self):
        ahora = timezone.now()
        pp.evaluar(SOCIO, self._mol(id_controlador=55), cuando=ahora)
        self.assertEqual(
            pp.evaluar(SOCIO, self._mol(id_controlador=61), cuando=ahora + dt.timedelta(seconds=30)), "")

    def test_el_equipo_facial_y_su_molinete_son_el_mismo_grupo(self):
        """Molinete 2 agrupa el controlador 61 y el equipo 538150641: el mismo
        cruce visto por los dos lados no puede ser conflicto."""
        ahora = timezone.now()
        pp.evaluar(SOCIO, self._mol(device_id=538150641), cuando=ahora)
        self.assertEqual(
            pp.evaluar(SOCIO, self._mol(id_controlador=61), cuando=ahora + dt.timedelta(seconds=1)), "")

    def test_el_conflicto_no_pisa_la_reserva_original(self):
        """El socio sigue debiendo cruzar el primero: un tercer intento tiene que
        seguir señalando ese mismo molinete."""
        ahora = timezone.now()
        pp.evaluar(SOCIO, self._mol(id_controlador=55), cuando=ahora)
        pp.evaluar(SOCIO, self._mol(id_controlador=61), cuando=ahora + dt.timedelta(seconds=1))
        self.assertEqual(
            pp.evaluar(SOCIO, self._mol(id_controlador=61), cuando=ahora + dt.timedelta(seconds=2)),
            "Molinete 1")

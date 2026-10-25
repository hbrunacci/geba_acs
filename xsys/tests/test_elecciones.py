"""Pruebas de la pantalla de Elecciones.

xSys no está en el entorno de tests: los votos se inyectan reemplazando
``listar`` y ``_votos`` del servicio. Lo que se prueba es lo que, roto, daría un
número creíble pero falso (los tramos que no suman el total, la comparación con
la elección anterior a la misma hora) y que cada sector respete su grupo en el
servidor y no sólo en la pantalla.
"""

from __future__ import annotations

import datetime as dt
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from common import nav
from common.roles import secciones_elecciones
from xsys.services import elecciones

ELECCIONES = [
    {"id": 239, "nombre": "Elecciones 25/10/2026", "fecha": "2026-10-25", "padron": 6, "votaron": 3},
    {"id": 233, "nombre": "Elecciones 27/10/2024", "fecha": "2024-10-27", "padron": 4, "votaron": 2},
]


def _f(dia: str, hora: str) -> dt.datetime:
    return dt.datetime.fromisoformat(f"{dia} {hora}")


VOTOS = {
    # 6 empadronados en dos mesas; votaron 3: 09:05, 10:15 y 10:16.
    239: [(1, _f("2026-10-25", "09:05")), (1, _f("2026-10-25", "10:15")), (1, None),
          (2, _f("2026-10-25", "10:16")), (2, None), (2, None)],
    # 2024: 4 empadronados; votaron 2, a las 09:30 y a las 15:00.
    233: [(1, _f("2024-10-27", "09:30")), (1, None), (2, _f("2024-10-27", "15:00")), (2, None)],
}


class _ConVotos(TestCase):
    def setUp(self):
        cache.clear()
        p1 = mock.patch.object(elecciones, "listar", return_value=ELECCIONES)
        p2 = mock.patch.object(elecciones, "_votos", side_effect=lambda i: VOTOS[i])
        p1.start(); p2.start()
        self.addCleanup(mock.patch.stopall)

    def _a_las(self, cuando: str):
        return mock.patch.object(elecciones, "_ahora", return_value=dt.datetime.fromisoformat(cuando))


class ParticipacionTests(_ConVotos):

    def test_en_curso_cuenta_y_compara_a_la_misma_hora(self):
        with self._a_las("2026-10-25 10:30"):
            d = elecciones.participacion()
        self.assertEqual(d["momento"]["estado"], "en_curso")
        self.assertEqual((d["padron"], d["votaron"], d["restan"]), (6, 3, 3))
        self.assertEqual(d["pct"], 50.0)
        self.assertEqual(sum(d["tramos"]["votos"]), 3)
        self.assertEqual(d["tramos"]["en_curso"], 3)        # abre 10:00; 10:30 -> tramo 10:30-10:40
        self.assertEqual(d["comparada"]["id"], 233)
        # En 2024, antes de las 10:30 había votado 1 de 4.
        self.assertEqual(d["comparacion"]["a_esta_hora"], 1)
        self.assertEqual(d["comparacion"]["pct_a_esta_hora"], 25.0)
        # Ritmo: 2 votos desde la apertura (10:15 y 10:16) -> proyecta 2/h por 10,5 h más.
        self.assertEqual(d["ultima_hora"], 2)
        self.assertEqual(d["proyeccion"], 3 + 21)
        # Primera hora: todavía no hay una cerrada, va la que corre (el de 09:05 cuenta en ella).
        self.assertEqual(d["hora_corte"], {"desde": "10:00", "hasta": "11:00", "votos": 3, "completa": False})

    def test_acreditados_ultima_hora_es_la_hora_de_reloj_cerrada(self):
        with self._a_las("2026-10-25 11:40"):
            d = elecciones.participacion()
        self.assertEqual(d["hora_corte"], {"desde": "10:00", "hasta": "11:00", "votos": 3, "completa": True})
        with self._a_las("2026-10-25 12:05"):
            d = elecciones.participacion()
        self.assertEqual(d["hora_corte"], {"desde": "11:00", "hasta": "12:00", "votos": 0, "completa": True})

    def test_antes_de_la_eleccion_no_proyecta(self):
        with self._a_las("2026-10-20 12:00"):
            d = elecciones.participacion()
        self.assertEqual(d["momento"]["estado"], "pendiente")
        self.assertEqual(d["proyeccion"], d["votaron"])
        self.assertIsNone(d["tramos"]["en_curso"])

    def test_una_eleccion_pasada_se_compara_completa(self):
        with self._a_las("2026-10-20 12:00"):
            d = elecciones.participacion(233)
        self.assertEqual(d["momento"]["estado"], "cerrada")
        self.assertIsNone(d["comparada"])


class MesasHorariosTests(_ConVotos):

    def test_mesas(self):
        with self._a_las("2026-10-25 12:00"):
            d = elecciones.mesas()
        self.assertEqual([(m["mesa"], m["padron"], m["votaron"], m["ultimo"]) for m in d["mesas"]],
                         [(1, 3, 2, "10:15"), (2, 3, 1, "10:16")])
        self.assertEqual(d["promedio_pct"], 50.0)

    def test_horarios_suman_el_total_y_cortan_el_acumulado_en_la_hora(self):
        with self._a_las("2026-10-25 10:30"):
            d = elecciones.horarios()
        self.assertEqual(d["por_hora"][:2], [3, 0])         # el de 09:05 cae en la primera franja
        self.assertEqual(sum(d["por_hora"]), 3)
        self.assertEqual(d["hora_en_curso"], 0)
        self.assertEqual(len(d["acumulado"]["pct"]), 4)     # hasta el tramo 10:30
        self.assertEqual(d["acumulado"]["pct"][-1], 50.0)
        self.assertEqual(d["comparacion"]["acumulado_pct"][-1], 50.0)


class ActividadesTests(_ConVotos):

    def test_agrupa_y_separa_a_los_que_no_tienen_actividad(self):
        filas = [(23, "Torneo Futbol", 4, 2), (9, "Cocheras San Martin", 2, 1), (-1, "", 1, 1)]
        with self._a_las("2026-10-25 12:00"), \
                mock.patch.object(elecciones, "_consultar", return_value=filas), \
                self.settings(XSYS_APTO_FAMILIAS=(23,)):
            d = elecciones.actividades()
        self.assertEqual(d["votaron"], 3)
        self.assertEqual(d["sin_actividad"]["votaron"], 1)
        self.assertEqual(d["con_actividad"], 2)
        futbol = d["actividades"][0]
        self.assertEqual((futbol["actividad"], futbol["tipo"], futbol["pct"]), ("Torneo Fútbol", "deporte", 50.0))
        self.assertEqual(d["actividades"][1]["tipo"], "servicio")


def _usuario(nombre, grupos=(), **extra):
    u = get_user_model().objects.create_user(nombre, password="x", **extra)
    for g in grupos:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class PermisosTests(_ConVotos):

    def test_cada_sector_responde_solo_a_su_grupo(self):
        u = _usuario("mesa", ["elecciones_mesas"])
        self.assertEqual(secciones_elecciones(u), ["mesas"])
        self.client.force_login(u)
        with self._a_las("2026-10-25 12:00"):
            self.assertEqual(self.client.get(reverse("xsys_elecciones_mesas_api")).status_code, 200)
            self.assertEqual(self.client.get(reverse("xsys_elecciones_participacion_api")).status_code, 404)
            self.assertEqual(self.client.get(reverse("xsys_elecciones_actividades_api")).status_code, 404)
            self.assertEqual(self.client.get(reverse("xsys_elecciones_lista_api")).status_code, 200)
        r = self.client.get(reverse("xsys_elecciones"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'data-seccion="mesas"')
        self.assertNotContains(r, 'data-seccion="actividades"')

    def test_pantalla_trae_resumen_horarios_y_mesas_sin_dar_los_otros_sectores(self):
        u = _usuario("tele", ["elecciones_pantalla"])
        self.assertEqual(secciones_elecciones(u), ["pantalla"])
        self.client.force_login(u)
        with self._a_las("2026-10-25 12:00"):
            r = self.client.get(reverse("xsys_elecciones_pantalla_api"))
            self.assertEqual(self.client.get(reverse("xsys_elecciones_mesas_api")).status_code, 404)
            self.assertEqual(self.client.get(reverse("xsys_elecciones_horarios_api")).status_code, 404)
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["votaron"], 3)
        self.assertEqual(sum(d["horarios"]["por_hora"]), 3)
        self.assertEqual(sum(m["votaron"] for m in d["mesas"]), 3)
        r = self.client.get(reverse("xsys_elecciones"))
        self.assertContains(r, 'id="t-mesas"')
        self.assertNotContains(r, 'id="panel-mesas"')

    def test_sin_grupo_no_entra_ni_lo_ve_en_el_menu(self):
        u = _usuario("nadie", ["Administrador"])
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("xsys_elecciones")).status_code, 404)
        self.assertEqual(self.client.get(reverse("xsys_elecciones_mesas_api")).status_code, 404)
        urls = [e.get("url") for e in nav.construir(u)]
        self.assertNotIn("xsys_elecciones", urls)

    def test_staff_ve_todos_los_sectores(self):
        u = _usuario("staff", is_staff=True)
        self.assertEqual(secciones_elecciones(u), ["participacion", "mesas", "horarios", "actividades", "pantalla"])
        self.assertIn("xsys_elecciones", [e.get("url") for e in nav.construir(u)])

"""PDF de acreditados de la elección, por hora y por mesa."""

import datetime as dt
from unittest import mock

from django.test import SimpleTestCase

from xsys.services import elecciones_reporte as R

ELECCION = {"id": 239, "nombre": "Elecciones 25/10/2026", "fecha": "2026-10-25", "padron": 10154}


class ReporteTests(SimpleTestCase):
    def test_demo_es_estable_y_cae_en_la_jornada(self):
        a = R.demo(dt.date(2026, 10, 25), cantidad=300)
        self.assertEqual(a, R.demo(dt.date(2026, 10, 25), cantidad=300))
        self.assertTrue(all(10 <= f["fecha"].hour <= 20 for f in a))
        self.assertTrue(all(1 <= f["mesa"] <= 11 for f in a))

    def test_arma_un_pdf_con_y_sin_acreditados(self):
        for filas in (R.demo(dt.date(2026, 10, 25), cantidad=200), []):
            contenido = R.pdf(ELECCION, filas, demo=bool(filas))
            self.assertTrue(contenido.startswith(b"%PDF"))

    def test_acreditados_lee_solo_los_que_votaron(self):
        fila = (3, 17, dt.datetime(2026, 10, 25, 10, 5), 892646, "YANORNO ", "ISABELLA")
        with mock.patch.object(R.E, "_consultar", return_value=[fila]) as q:
            r = R.acreditados(239)
        self.assertIn("Fecha_Proceso IS NOT NULL", q.call_args[0][0])
        self.assertEqual(r[0]["mesa"], 3)
        self.assertEqual((r[0]["socio"], r[0]["apellido"]), (892646, "YANORNO"))

    def test_el_numero_de_socio_es_el_externo_no_la_ficha(self):
        # Blanco Claudio Hernán: ficha 896332, socio 895752.
        fila = (2, 1132, dt.datetime(2026, 10, 25, 12, 16), "895752", "BLANCO", "CLAUDIO HERNAN")
        with mock.patch.object(R.E, "_consultar", return_value=[fila]) as q:
            r = R.acreditados(239)
        self.assertIn("Id_Cliente_Externo", q.call_args[0][0])
        self.assertEqual(r[0]["socio"], 895752)

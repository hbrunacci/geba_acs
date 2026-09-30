"""Tests del enrolamiento de rostros: a quién hay que (re)cargar en BioStar.

xSys y BioStar se simulan: se fija ``_rows`` con las filas que devolverían las
tres consultas de ``build_candidates`` (universo, T_CRDT, T_USR).
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from access_control.models import FaceEnrollment
from access_control.services import biostar_face_sync as fs


def _t(*args):
    return datetime(*args)


class BuildCandidatesTests(TestCase):
    def _correr(self, universo, crdt, usuarios):
        with patch("access_control.services.diag_facial._rows", side_effect=[universo, crdt, usuarios]):
            return fs.build_candidates(MagicMock(), "[X].[B].dbo")

    def _ids(self, lista):
        return [w["id_cliente"] for w in lista]

    def test_clasifica_alta_sin_rostro_y_al_dia(self):
        r = self._correr(
            [
                {"cid": 1, "nombre": "SIN USUARIO", "foto_fecha": _t(2026, 9, 1)},
                {"cid": 2, "nombre": "SIN ROSTRO", "foto_fecha": _t(2026, 9, 1)},
                {"cid": 3, "nombre": "AL DIA", "foto_fecha": _t(2026, 9, 1)},
            ],
            [{"USRUID": 30, "n": 1}],
            [
                {"USRID": "2", "USRUID": 20, "DEL": "N", "LSTMODDT": _t(2026, 9, 2)},
                {"USRID": "3", "USRUID": 30, "DEL": "N", "LSTMODDT": _t(2026, 9, 2)},
            ],
        )
        self.assertEqual(self._ids(r["to_create"]), [1])
        self.assertEqual(self._ids(r["to_enroll"]), [2])
        self.assertEqual(r["to_refresh"], [])

    def test_foto_mas_nueva_que_la_enrolada_se_refresca(self):
        # Caso del ticket G5L-QJH-X6GU: le cambiaron la foto y el cambio no llegó.
        FaceEnrollment.objects.create(id_cliente=3, foto_fecha=timezone.make_aware(_t(2026, 9, 1)))
        r = self._correr(
            [{"cid": 3, "nombre": "FOTO NUEVA", "foto_fecha": _t(2026, 9, 29, 14, 44)}],
            [{"USRUID": 30, "n": 1}],
            # BioStar tocó el usuario después (p.ej. grupos) sin cambiar el rostro:
            # el registro propio manda sobre LSTMODDT.
            [{"USRID": "3", "USRUID": 30, "DEL": "N", "LSTMODDT": _t(2026, 9, 30)}],
        )
        self.assertEqual(self._ids(r["to_refresh"]), [3])
        self.assertTrue(r["to_refresh"][0]["exists"])

    def test_foto_ya_enrolada_no_se_repite(self):
        FaceEnrollment.objects.create(id_cliente=3, foto_fecha=timezone.make_aware(_t(2026, 9, 29, 14, 44)))
        r = self._correr(
            [{"cid": 3, "nombre": "AL DIA", "foto_fecha": _t(2026, 9, 29, 14, 44)}],
            [{"USRUID": 30, "n": 1}],
            [{"USRID": "3", "USRUID": 30, "DEL": "N", "LSTMODDT": _t(2026, 9, 1)}],
        )
        self.assertEqual(r["to_refresh"], [])

    def test_sin_registro_propio_compara_contra_la_ultima_modificacion_de_biostar(self):
        # Enrolados por CleverSoft: no hay registro, se usa LSTMODDT.
        r = self._correr(
            [
                {"cid": 4, "nombre": "VIEJO", "foto_fecha": _t(2026, 9, 10)},
                {"cid": 5, "nombre": "AL DIA", "foto_fecha": _t(2026, 9, 10)},
            ],
            [{"USRUID": 40, "n": 1}, {"USRUID": 50, "n": 1}],
            [
                {"USRID": "4", "USRUID": 40, "DEL": "N", "LSTMODDT": _t(2026, 9, 5)},
                {"USRID": "5", "USRUID": 50, "DEL": "N", "LSTMODDT": _t(2026, 9, 11)},
            ],
        )
        self.assertEqual(self._ids(r["to_refresh"]), [4])


class RegistroTests(TestCase):
    def test_registrar_guarda_y_actualiza_la_foto_enrolada(self):
        fs.registrar_enrolamiento(7, _t(2026, 9, 1))
        fs.registrar_enrolamiento(7, _t(2026, 9, 29, 14, 44))
        fila = FaceEnrollment.objects.get(id_cliente=7)
        self.assertEqual(timezone.localtime(fila.foto_fecha).replace(tzinfo=None), _t(2026, 9, 29, 14, 44))
        self.assertEqual(FaceEnrollment.objects.count(), 1)


class ConexionTests(TestCase):
    @override_settings(
        MSSQL_XSYS={"USER": "geba_acs", "PASSWORD": "x"},
        MSSQL_XSYS_BIOSTAR={"USER": "sa", "PASSWORD": "y"},
    )
    def test_conectar_usa_el_login_con_acceso_a_biostar(self):
        # geba_acs no tiene login-mapping en el linked server (error 7416).
        from access_control.services import diag_facial

        with patch("xsys.services.mssql.connect", return_value=MagicMock()) as connect:
            _conn, driver = diag_facial.conectar()
        self.assertEqual(driver, "pyodbc")
        self.assertEqual(connect.call_args.args[0]["USER"], "sa")

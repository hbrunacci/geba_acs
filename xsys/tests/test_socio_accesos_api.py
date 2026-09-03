"""La API que alimenta el historial de accesos en la ficha del socio.

Contesta desde la tabla local (``SocioAcceso``), así que anda con la VPN caída y
no le pega a xSys por cada pantalla que se abre.
"""

import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from access_control.models import SocioAcceso

CID = 929935
URL = f"/api/xsys/socios/{CID}/accesos/"


def _acceso(ref, *, dias=0, permitido=True, origen=SocioAcceso.ORIGEN_CREDENCIAL,
            id_cliente=CID, mensaje="Acceso Concedido"):
    return SocioAcceso.objects.create(
        id_cliente=id_cliente, fecha=timezone.now() - datetime.timedelta(days=dias),
        origen=origen, referencia=ref, permitido=permitido, mensaje=mensaje,
        puerta="Alcorta", molinete="Molinete 1")


class SocioAccesosAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User.objects.create_user("op", password="x")

    def setUp(self):
        self.client.login(username="op", password="x")

    def _get(self, qs=""):
        return self.client.get(URL + qs).json()

    def test_devuelve_el_historial(self):
        _acceso("a")
        self.assertEqual(self._get()["total"], 1)

    def test_del_mas_nuevo_al_mas_viejo(self):
        _acceso("viejo", dias=10)
        _acceso("nuevo", dias=1)
        fechas = [r["fecha"] for r in self._get()["resultados"]]
        self.assertEqual(fechas, sorted(fechas, reverse=True))

    def test_no_trae_los_de_otro_socio(self):
        _acceso("mio")
        _acceso("ajeno", id_cliente=111222)
        self.assertEqual(self._get()["total"], 1)

    def test_cuenta_pasos_y_rechazos(self):
        _acceso("ok1")
        _acceso("ok2")
        _acceso("no1", permitido=False)
        d = self._get()
        self.assertEqual((d["permitidos"], d["rechazados"]), (2, 1))

    def test_los_contadores_son_del_filtro_completo_no_de_la_pagina(self):
        """Si contaran lo visible, el número cambiaría al pasar de página."""
        for i in range(7):
            _acceso(f"r{i}", dias=i)
        d = self._get("?limit=3")
        self.assertEqual(d["total"], 7)
        self.assertEqual(len(d["resultados"]), 3)

    def test_pagina_con_offset(self):
        for i in range(5):
            _acceso(f"r{i}", dias=i)
        primeros = [r["id"] for r in self._get("?limit=2")["resultados"]]
        siguientes = [r["id"] for r in self._get("?limit=2&offset=2")["resultados"]]
        self.assertEqual(len(set(primeros) & set(siguientes)), 0)

    def test_filtra_por_origen(self):
        _acceso("cred")
        _acceso("cara", origen=SocioAcceso.ORIGEN_FACIAL)
        d = self._get("?origen=facial")
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["resultados"][0]["origen"], "facial")

    def test_filtra_por_resultado(self):
        _acceso("ok")
        _acceso("no", permitido=False)
        self.assertEqual(self._get("?resultado=rechazo")["total"], 1)
        self.assertEqual(self._get("?resultado=ok")["total"], 1)

    def test_filtra_por_fechas(self):
        _acceso("viejo", dias=30)
        _acceso("hoy")
        desde = (timezone.localdate() - datetime.timedelta(days=2)).isoformat()
        self.assertEqual(self._get(f"?desde={desde}")["total"], 1)

    def test_un_filtro_invalido_no_rompe(self):
        _acceso("a")
        self.assertEqual(self._get("?origen=cualquiera&limit=x&offset=y")["total"], 1)

    def test_el_limite_tiene_techo(self):
        """Un socio puede tener años de pasos: no se sirve todo de una."""
        from xsys.api_views import ACCESOS_PAGINA_MAX

        self.assertEqual(self._get("?limit=99999")["limit"], ACCESOS_PAGINA_MAX)

    def test_trae_lo_que_la_pantalla_muestra(self):
        _acceso("a")
        r = self._get()["resultados"][0]
        for campo in ("fecha", "origen", "permitido", "mensaje", "puerta",
                      "molinete", "conflicto_molinete", "detalle"):
            self.assertIn(campo, r, campo)

    def test_sin_socio_no_falla(self):
        d = self.client.get("/api/xsys/socios/1/accesos/").json()
        self.assertEqual((d["total"], d["resultados"]), (0, []))

    def test_pide_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(URL).status_code, 403)

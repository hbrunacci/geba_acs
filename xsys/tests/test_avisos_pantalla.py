import datetime
import json
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from access_control.models import SocioAviso
from institutions.models import AccessDoor
from xsys.models import PantallaPuerta

TOKEN = "tok-aviso-abcdef"
URL = "/api/xsys/socios/914988/aviso/"


class PantallaAvisoAPITests(TestCase):
    """Avisos de un toque desde el monitor, que es un kiosco sin login."""

    @classmethod
    def setUpTestData(cls):
        door = AccessDoor.objects.create(name="Ombues", is_active=True, xsys_id_acceso=15)
        PantallaPuerta.objects.create(token=TOKEN, door=door, ip="10.0.0.9", nombre="Molinete 3")

    def _post(self, tipo, token=TOKEN):
        kw = {"HTTP_X_PANTALLA_TOKEN": token} if token else {}
        return self.client.post(URL, json.dumps({"tipo": tipo}), content_type="application/json", **kw)

    def test_crea_el_aviso_con_el_texto_del_servidor(self):
        r = self._post("tomar_foto")
        self.assertEqual(r.status_code, 201)
        aviso = SocioAviso.objects.get()
        self.assertEqual(aviso.id_cliente, 914988)
        self.assertEqual(aviso.tipo, "tomar_foto")
        self.assertEqual(aviso.texto, "Se indica tomar foto")

    def test_los_tres_tipos_del_visor(self):
        esperados = {
            "tomar_foto": "Se indica tomar foto",
            "deuda": "Se notifica deuda",
            "pase_por_socios": "Se indica pasar por oficina de socios",
        }
        for tipo, texto in esperados.items():
            self.assertEqual(self._post(tipo).status_code, 201)
            self.assertTrue(SocioAviso.objects.filter(tipo=tipo, texto=texto).exists())

    def test_registra_de_que_pantalla_salio(self):
        """En un kiosco sin login es lo único que se sabe del origen."""
        self._post("deuda")
        self.assertEqual(SocioAviso.objects.get().creado_por, "monitor: Molinete 3")

    def test_rechaza_un_tipo_desconocido(self):
        r = self._post("borrar_socio")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(SocioAviso.objects.exists())

    def test_no_acepta_texto_libre(self):
        """El molinete no tiene teclado: el texto lo fija el servidor."""
        r = self.client.post(
            URL, json.dumps({"tipo": "libre", "texto": "lo que sea"}),
            content_type="application/json", HTTP_X_PANTALLA_TOKEN=TOKEN,
        )
        self.assertEqual(r.status_code, 400)
        self.assertFalse(SocioAviso.objects.exists())

    def test_exige_token_de_pantalla(self):
        r = self._post("deuda", token=None)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(SocioAviso.objects.exists())

    def test_el_aviso_creado_aparece_en_el_visor(self):
        """Lo que se deja acá tiene que salir con 📢 cuando el socio vuelve a pasar."""
        self._post("pase_por_socios")
        avisos = list(
            SocioAviso.objects.filter(id_cliente=914988).values_list("texto", flat=True)
        )
        self.assertEqual(avisos, ["Se indica pasar por oficina de socios"])

    # ----------------------------------------------- un aviso por tipo y día --

    def test_no_repite_el_mismo_aviso_el_mismo_dia(self):
        """El socio pasa muchas veces por día y por varios molinetes: sin esto se
        le juntaban tres o cuatro veces el mismo aviso."""
        self.assertEqual(self._post("deuda").status_code, 201)
        r = self._post("deuda")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "duplicado_hoy")
        self.assertEqual(SocioAviso.objects.filter(tipo="deuda").count(), 1)

    def test_otro_tipo_el_mismo_dia_si_se_permite(self):
        self.assertEqual(self._post("deuda").status_code, 201)
        self.assertEqual(self._post("tomar_foto").status_code, 201)
        self.assertEqual(SocioAviso.objects.count(), 2)

    def test_el_mismo_aviso_otro_dia_si_se_permite(self):
        self.assertEqual(self._post("deuda").status_code, 201)
        viejo = SocioAviso.objects.get()
        viejo.created_at = timezone.now() - datetime.timedelta(days=1)
        viejo.save(update_fields=["created_at"])
        self.assertEqual(self._post("deuda").status_code, 201)
        self.assertEqual(SocioAviso.objects.filter(tipo="deuda").count(), 2)

    def test_el_dia_es_el_del_club_y_no_el_utc(self):
        """A las 22 de Buenos Aires ya es el día siguiente en UTC: si el corte se
        tomara del reloj del contenedor, el aviso de la noche se repetiría."""
        anoche = timezone.localtime().replace(hour=22, minute=30) - datetime.timedelta(days=0)
        with mock.patch("django.utils.timezone.localdate", return_value=anoche.date()):
            self.assertEqual(self._post("deuda").status_code, 201)
            self.assertEqual(self._post("deuda").status_code, 409)

    def test_el_alta_devuelve_los_ultimos_avisos(self):
        """El modal se repinta con lo que contesta el POST, sin pedir el detalle."""
        self._post("deuda")
        r = self._post("pase_por_socios")
        self.assertEqual(r.status_code, 201)
        d = r.json()
        self.assertEqual([a["tipo"] for a in d["avisos"]], ["pase_por_socios", "deuda"])
        self.assertEqual(sorted(d["avisos_hoy"]), ["deuda", "pase_por_socios"])

    def test_el_rechazo_tambien_devuelve_el_estado(self):
        """Si otra pantalla se adelantó, la que reintenta tiene que poder pintar
        el aviso que ya existe en vez de quedarse sin nada."""
        self._post("deuda")
        d = self._post("deuda").json()
        self.assertEqual([a["tipo"] for a in d["avisos"]], ["deuda"])
        self.assertEqual(d["avisos_hoy"], ["deuda"])


class SocioDetalleAvisosTests(TestCase):
    """El modal del visor trae los últimos avisos junto con el detalle."""

    URL = "/api/xsys/socios/914988/detalle/"

    def test_devuelve_los_ultimos_tres_y_los_de_hoy(self):
        for i in range(5):
            SocioAviso.objects.create(
                id_cliente=914988, tipo="deuda", texto=f"aviso {i}",
                created_at=timezone.now() - datetime.timedelta(days=5 - i),
            )
        d = self.client.get(self.URL).json()
        self.assertEqual([a["texto"] for a in d["avisos"]], ["aviso 4", "aviso 3", "aviso 2"])
        # El más nuevo es de ayer: hoy no se le dejó ninguno.
        self.assertEqual(d["avisos_hoy"], [])

    def test_marca_el_tipo_dejado_hoy(self):
        SocioAviso.objects.create(id_cliente=914988, tipo="tomar_foto", texto="hoy")
        d = self.client.get(self.URL).json()
        self.assertEqual(d["avisos_hoy"], ["tomar_foto"])

    def test_socio_sin_avisos(self):
        d = self.client.get(self.URL).json()
        self.assertEqual(d["avisos"], [])
        self.assertEqual(d["avisos_hoy"], [])

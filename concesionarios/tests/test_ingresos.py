"""Ingresos al club, foto para el facial y el aviso en el visor de puerta."""

import io
from datetime import date, datetime, timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from concesionarios import fotos, services
from concesionarios.models import (
    Concesionario,
    Documento,
    Empresa,
    FotoPersona,
    TipoDocumento,
)
from xsys.models import XsysSocio


def _jpeg(color=(90, 120, 200), tamano=(160, 160)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", tamano, color).save(buf, format="JPEG")
    return buf.getvalue()


class _Cursor:
    """Cursor de mentira con las filas que devolvería CD_ES."""

    def __init__(self, filas):
        self.filas = filas
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.filas


class IngresosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.buffet = Empresa.objects.create(nombre="Buffet SM")
        cls.otra = Empresa.objects.create(nombre="Limpieza JN")
        XsysSocio.objects.create(id_cliente=5001, apellido="PEREZ", nombre="JUAN",
                                 doc_nro=20111222, id_tipo_cli=1015, activo=1)
        XsysSocio.objects.create(id_cliente=5002, apellido="GOMEZ", nombre="ANA",
                                 doc_nro=30222333, id_tipo_cli=1015, activo=1)
        Concesionario.objects.create(id_cliente=5001, empresa=cls.buffet)
        Concesionario.objects.create(id_cliente=5002, empresa=cls.otra)

    def _filas(self):
        ahora = datetime(2026, 9, 2, 9, 30)
        return [
            (900001, ahora, 5001, "S", 14, 59, "Alcorta Mol1", "K",
             "Habilit. por Tipo de Contrato CONCESIONARIOS SM"),
            (900002, ahora - timedelta(hours=1), 5002, "E", 15, 62, "Ombues Mol3", "K",
             "No cumple ninguna condición habilitante"),
            (900003, ahora - timedelta(hours=2), 5001, "S", 4, 68, "Facial Alcorta", "F",
             "Habilit. por Tipo de Contrato CONCESIONARIOS SM"),
        ]

    def _ingresos(self, **kw):
        kw.setdefault("desde", date(2026, 9, 1))
        kw.setdefault("hasta", date(2026, 9, 2))
        kw.setdefault("cursor", _Cursor(self._filas()))
        return services.ingresos(**kw)

    def test_trae_persona_empresa_hora_y_resultado(self):
        d = self._ingresos()
        self.assertEqual(len(d["results"]), 3)
        f = d["results"][0]
        self.assertEqual(f["persona"]["nombre_completo"], "PEREZ, JUAN")
        self.assertEqual(f["empresa"], "Buffet SM")
        self.assertTrue(f["permitido"])
        self.assertIn("CONCESIONARIOS", f["motivo"])
        self.assertEqual(f["lector"], "Alcorta Mol1")

    def test_distingue_el_facial_de_la_credencial(self):
        lecturas = {f["id_es"]: f["lectura"] for f in self._ingresos()["results"]}
        self.assertEqual(lecturas[900003], "facial")
        self.assertEqual(lecturas[900001], "credencial")

    def test_marca_el_que_no_paso(self):
        f = next(x for x in self._ingresos()["results"] if x["id_es"] == 900002)
        self.assertFalse(f["permitido"])
        self.assertIn("No cumple", f["motivo"])

    def test_resumen(self):
        r = self._ingresos()["resumen"]
        self.assertEqual((r["eventos"], r["personas"], r["pasaron"], r["rechazos"]), (3, 2, 2, 1))

    def test_filtra_por_empresa_antes_de_consultar(self):
        """El filtro entra en el IN de la consulta: no se traen y descartan."""
        cur = _Cursor([])
        self._ingresos(empresa_id=self.otra.id, cursor=cur)
        self.assertIn("5002", cur.sql)
        self.assertNotIn("5001", cur.sql)

    def test_el_rango_es_inclusivo_en_el_dia_de_fin(self):
        cur = _Cursor([])
        self._ingresos(desde=date(2026, 9, 1), hasta=date(2026, 9, 5), cursor=cur)
        self.assertEqual(cur.params[0], datetime(2026, 9, 1, 0, 0))
        self.assertEqual(cur.params[1], datetime(2026, 9, 6, 0, 0))

    def test_no_acepta_un_rango_gigante(self):
        d = services.ingresos(desde=date(2025, 1, 1), hasta=date(2026, 9, 1))
        self.assertIn("error", d)
        self.assertEqual(d["results"], [])

    def test_sin_concesionarios_cargados_avisa(self):
        Concesionario.objects.all().delete()
        d = self._ingresos()
        self.assertTrue(d["sin_concesionarios"])

    def test_si_xsys_no_responde_devuelve_error_y_no_revienta(self):
        """La pantalla tiene que decir "no pude consultar", no tirar un 500."""
        from unittest.mock import patch
        with patch("xsys.services.mssql.xsys_cursor", side_effect=OSError("sin red")):
            d = services.ingresos(desde=date(2026, 9, 1), hasta=date(2026, 9, 2))
        self.assertIn("error", d)
        self.assertEqual(d["results"], [])


class EstadoOperativoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Buffet SM")
        cls.art = TipoDocumento.objects.create(codigo="x_art", nombre="ART",
                                               bloquea_acceso=True, dias_aviso=30)
        cls.nota = TipoDocumento.objects.create(codigo="x_nota", nombre="Nota",
                                                bloquea_acceso=False)
        XsysSocio.objects.create(id_cliente=6001, apellido="RUIZ", nombre="LEO",
                                 doc_nro=1, id_tipo_cli=1015, activo=1)

    def test_sin_problemas_no_hay_alerta(self):
        Concesionario.objects.create(id_cliente=6001, empresa=self.empresa)
        self.assertFalse(services.estado_operativo([6001])[6001]["alerta"])

    def test_dado_de_baja_en_la_concesion(self):
        Concesionario.objects.create(id_cliente=6001, empresa=self.empresa, activo=False)
        e = services.estado_operativo([6001])[6001]
        self.assertTrue(e["alerta"])
        self.assertIn("baja", e["motivo"])

    def test_documento_que_habilita_vencido(self):
        Concesionario.objects.create(id_cliente=6001, empresa=self.empresa)
        Documento.objects.create(id_cliente=6001, tipo=self.art,
                                 fecha_vencimiento=timezone.localdate() - timedelta(days=1))
        e = services.estado_operativo([6001])[6001]
        self.assertTrue(e["alerta"])
        self.assertTrue(e["doc_bloqueado"])
        self.assertIn("ART", e["motivo"])

    def test_un_documento_que_no_habilita_no_dispara_alerta(self):
        Concesionario.objects.create(id_cliente=6001, empresa=self.empresa)
        Documento.objects.create(id_cliente=6001, tipo=self.nota,
                                 fecha_vencimiento=timezone.localdate() - timedelta(days=1))
        self.assertFalse(services.estado_operativo([6001])[6001]["alerta"])

    def test_la_empresa_inactiva_tambien_avisa(self):
        empresa = Empresa.objects.create(nombre="Cerrada", activa=False)
        Concesionario.objects.create(id_cliente=6001, empresa=empresa)
        self.assertIn("inactiva", services.estado_operativo([6001])[6001]["motivo"])

    def test_quien_no_es_concesionario_no_figura(self):
        self.assertEqual(services.estado_operativo([999999]), {})


class FotoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("jefe", "j@x.com", "x")
        self.client.force_login(self.user)
        self.empresa = Empresa.objects.create(nombre="Buffet")
        XsysSocio.objects.create(id_cliente=7001, apellido="SOSA", nombre="EVA",
                                 doc_nro=2, id_tipo_cli=1015, activo=1)
        Concesionario.objects.create(id_cliente=7001, empresa=self.empresa)

    def test_guarda_normaliza_a_jpeg_y_arma_miniatura(self):
        foto = fotos.guardar(7001, _jpeg(), usuario="jefe")
        self.assertEqual(foto.content_type, "image/jpeg")
        self.assertTrue(foto.thumbnail)
        self.assertEqual(len(foto.sha256), 64)

    def test_convierte_un_png(self):
        import io as _io

        from PIL import Image
        buf = _io.BytesIO()
        Image.new("RGBA", (80, 80), (10, 20, 30, 255)).save(buf, format="PNG")
        foto = fotos.guardar(7001, buf.getvalue())
        self.assertTrue(bytes(foto.imagen).startswith(b"\xff\xd8"))   # JPEG

    def test_rechaza_algo_que_no_es_imagen(self):
        with self.assertRaises(fotos.FotoInvalida):
            fotos.guardar(7001, b"esto no es una imagen")

    def test_rechaza_vacio(self):
        with self.assertRaises(fotos.FotoInvalida):
            fotos.guardar(7001, b"")

    def test_subir_por_la_api_y_volver_a_pedirla(self):
        r = self.client.post("/api/concesionarios/foto/7001/", {
            "archivo": SimpleUploadedFile("cara.jpg", _jpeg(), content_type="image/jpeg")})
        self.assertEqual(r.status_code, 201, r.content)
        img = self.client.get("/api/concesionarios/foto/7001/")
        self.assertEqual(img.status_code, 200)
        self.assertEqual(img["Content-Type"], "image/jpeg")

    def test_sin_foto_da_404(self):
        self.assertEqual(self.client.get("/api/concesionarios/foto/7001/").status_code, 404)

    def test_la_foto_cargada_le_gana_a_la_de_xsys(self):
        from xsys.models import XsysSocioFoto
        XsysSocioFoto.objects.create(id_cliente=7001, nro=1, imagen=_jpeg((1, 1, 1)), sha256="a")
        propia = _jpeg((250, 250, 250))
        fotos.guardar(7001, propia)
        datos, _ = fotos.bytes_para_mostrar(7001, miniatura=False)
        self.assertEqual(datos, bytes(FotoPersona.objects.get(id_cliente=7001).imagen))

    def test_si_no_hay_propia_cae_a_la_de_xsys(self):
        from xsys.models import XsysSocioFoto
        XsysSocioFoto.objects.create(id_cliente=7001, nro=1, imagen=_jpeg(), sha256="a")
        datos, _ = fotos.bytes_para_mostrar(7001, miniatura=False)
        self.assertTrue(datos)

    def test_volver_a_subir_reemplaza_y_desmarca_el_enrolamiento(self):
        foto = fotos.guardar(7001, _jpeg())
        foto.enrolada_at = timezone.now()
        foto.enrolada_resultado = "enrolled"
        foto.save()
        fotos.guardar(7001, _jpeg((5, 5, 5)))
        foto.refresh_from_db()
        self.assertIsNone(foto.enrolada_at)
        self.assertEqual(FotoPersona.objects.filter(id_cliente=7001).count(), 1)

    def test_borrar_la_foto(self):
        fotos.guardar(7001, _jpeg())
        self.assertEqual(self.client.delete("/api/concesionarios/foto/7001/").status_code, 204)
        self.assertFalse(FotoPersona.objects.filter(id_cliente=7001).exists())

    def test_enrolar_sin_foto_no_llama_a_biostar(self):
        r = fotos.enrolar(7001)
        self.assertFalse(r["ok"])
        self.assertIn("no tiene foto", r["detalle"])

    def test_la_foto_no_se_sirve_a_cualquiera(self):
        fotos.guardar(7001, _jpeg())
        self.client.logout()
        self.assertEqual(self.client.get("/api/concesionarios/foto/7001/").status_code, 403)


class VisorTests(TestCase):
    """El mismo estado que muestra el listado tiene que verse en el visor."""

    def setUp(self):
        from access_control.models.models import ExternalAccessLogEntry
        from xsys import api_views as A
        self.A = A
        self.empresa = Empresa.objects.create(nombre="Buffet SM")
        self.art = TipoDocumento.objects.create(codigo="v_art", nombre="ART",
                                                bloquea_acceso=True)
        self.socio = XsysSocio.objects.create(
            id_cliente=8001, apellido="DIAZ", nombre="RAUL", doc_nro=3,
            id_tipo_cli=1015, activo=1, ult_cuota_paga=timezone.now())
        Concesionario.objects.create(id_cliente=8001, empresa=self.empresa)
        self.ev = ExternalAccessLogEntry(
            external_id=1, id_cliente=8001, fecha=timezone.now(), resultado="S",
            id_acceso=14, id_controlador=59, observacion="Habilit. por Tipo de Contrato")

    def _payload(self):
        estado = self.A._estado_concesionarios([8001])
        return self.A._evento_payload(self.ev, {8001: self.socio}, set(), {}, {}, {}, {},
                                      set(), {}, {8001: "contrato"}, set(), estado)

    def test_sin_problemas_pasa_limpio(self):
        p = self._payload()
        self.assertEqual(p["estado"], "ok")
        self.assertEqual(p["concesionario_alerta"], "")

    def test_con_la_documentacion_vencida_avisa_sin_bloquear(self):
        Documento.objects.create(id_cliente=8001, tipo=self.art,
                                 fecha_vencimiento=timezone.localdate() - timedelta(days=3))
        p = self._payload()
        self.assertEqual(p["estado"], "anomalia")
        self.assertIn("ART", p["mensaje"])
        self.assertIn("Buffet SM", p["mensaje"])
        self.assertTrue(p["permitido"])   # xSys lo dejó pasar; acá sólo se avisa

    def test_dado_de_baja_en_la_concesion_tambien_avisa(self):
        Concesionario.objects.filter(id_cliente=8001).update(activo=False)
        p = self._payload()
        self.assertEqual(p["estado"], "anomalia")
        self.assertIn("baja", p["mensaje"])

    def test_un_rechazo_conserva_su_motivo(self):
        """Si no pasó, el motivo real manda sobre el aviso del concesionario."""
        Concesionario.objects.filter(id_cliente=8001).update(activo=False)
        self.ev.resultado = "N"
        p = self._payload()
        self.assertEqual(p["estado"], "no")
        self.assertNotIn("baja", p["mensaje"])

    def test_quien_no_es_concesionario_no_se_toca(self):
        Concesionario.objects.all().delete()
        self.assertEqual(self._payload()["estado"], "ok")

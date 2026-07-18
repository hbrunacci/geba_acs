import io

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from PIL import Image

from xsys.models import SyncState, XsysSocio, XsysSocioFoto, XsysWhitelist
from xsys.services.images import make_thumbnail


def _png_bytes(color=(54, 122, 199), size=(60, 80)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


class XsysApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.socio = XsysSocio.objects.create(
            id_cliente=944426,
            doc_nro=31850936,
            apellido="SIMOUR",
            nombre="GERMAN",
            activo=1,
            tipo_persona="F",
            credencial_nro="BCB30514",
            ult_cuota_paga=timezone.now(),
        )
        XsysWhitelist.objects.create(
            id_cliente=944426,
            habilitado=True,
            motivo="Habilit. por Produc. Comprado CUOTA SOCIAL",
        )
        cls.png = _png_bytes()
        XsysSocioFoto.objects.create(id_cliente=944426, nro=1, imagen=cls.png, sha256="x")

    def setUp(self):
        self.user = User.objects.create_user("op", password="pw")
        self.client.force_login(self.user)

    def test_lookup_por_doc(self):
        r = self.client.get("/api/xsys/socios/lookup/", {"doc": 31850936})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["socio"]["id_cliente"], 944426)
        self.assertEqual(data["socio"]["nombre_completo"], "SIMOUR, GERMAN")
        self.assertTrue(data["whitelist"]["habilitado"])
        self.assertTrue(data["foto_disponible"])
        self.assertEqual(data["socio"]["foto_url"], "/api/xsys/socios/944426/foto/")

    def test_lookup_por_credencial_case_insensitive(self):
        r = self.client.get("/api/xsys/socios/lookup/", {"credencial": "bcb30514"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["socio"]["id_cliente"], 944426)

    def test_lookup_sin_parametros_400(self):
        self.assertEqual(self.client.get("/api/xsys/socios/lookup/").status_code, 400)

    def test_lookup_no_encontrado_404(self):
        self.assertEqual(self.client.get("/api/xsys/socios/lookup/", {"doc": 1}).status_code, 404)

    def test_foto_devuelve_imagen(self):
        r = self.client.get("/api/xsys/socios/944426/foto/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/png")
        self.assertEqual(r.content, self.png)

    def test_foto_thumb_genera_jpeg_y_persiste(self):
        self.assertFalse(XsysSocioFoto.objects.get(id_cliente=944426, nro=1).thumbnail)
        r = self.client.get("/api/xsys/socios/944426/foto/", {"thumb": 1})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/jpeg")
        self.assertEqual(r.content[:2], b"\xff\xd8")
        self.assertLess(len(r.content), len(self.png) + 5000)
        # se persiste para próximas consultas
        self.assertTrue(XsysSocioFoto.objects.get(id_cliente=944426, nro=1).thumbnail)

    def test_serializer_incluye_thumb_url(self):
        r = self.client.get("/api/xsys/socios/lookup/", {"id": 944426})
        self.assertEqual(r.json()["socio"]["foto_thumb_url"], "/api/xsys/socios/944426/foto/?thumb=1")

    def test_make_thumbnail_reduce_tamano(self):
        thumb = make_thumbnail(self.png)
        self.assertIsNotNone(thumb)
        self.assertEqual(thumb[:2], b"\xff\xd8")  # JPEG
        img = Image.open(io.BytesIO(thumb))
        self.assertLessEqual(img.width, 96)
        self.assertLessEqual(img.height, 120)

    def test_foto_inexistente_404(self):
        self.assertEqual(self.client.get("/api/xsys/socios/999/foto/").status_code, 404)

    def test_whitelist_endpoint(self):
        r = self.client.get("/api/xsys/socios/944426/whitelist/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["habilitado"])

    def test_search(self):
        r = self.client.get("/api/xsys/socios/", {"q": "simour"})
        self.assertEqual(r.status_code, 200)
        results = r.json().get("results", r.json())
        self.assertTrue(any(s["id_cliente"] == 944426 for s in results))

    def test_requiere_autenticacion(self):
        self.client.logout()
        self.assertIn(self.client.get("/api/xsys/socios/lookup/", {"doc": 31850936}).status_code, (401, 403))

    def test_consola_render(self):
        r = self.client.get("/xsys/socios/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Socios (espejo xSys)")

    def test_consola_muestra_estado_sync(self):
        SyncState.advance("novedades", last_id=1234, rows=7)
        r = self.client.get("/xsys/socios/")
        self.assertContains(r, "Estado del espejo")
        self.assertContains(r, "Novedades / socios")
        self.assertContains(r, "1234")

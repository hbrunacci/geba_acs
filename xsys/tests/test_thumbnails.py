import io

from django.core.management import call_command
from django.test import TestCase
from PIL import Image

from xsys.models import XsysSocioFoto


def _png(color=(10, 120, 200), size=(60, 80)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


class XsysThumbnailsCommandTests(TestCase):
    def test_backfill_solo_faltantes(self):
        valida = XsysSocioFoto.objects.create(id_cliente=1, nro=1, imagen=_png(), sha256="a")
        corrupta = XsysSocioFoto.objects.create(id_cliente=2, nro=1, imagen=b"noimg", sha256="b")
        ya = XsysSocioFoto.objects.create(id_cliente=3, nro=1, imagen=_png(), thumbnail=b"prev", sha256="c")

        call_command("xsys_thumbnails")

        valida.refresh_from_db(); corrupta.refresh_from_db(); ya.refresh_from_db()
        self.assertTrue(valida.thumbnail)
        self.assertEqual(valida.thumbnail[:2], b"\xff\xd8")  # JPEG
        self.assertFalse(corrupta.thumbnail)                 # no se pudo generar
        self.assertEqual(bytes(ya.thumbnail), b"prev")       # no se tocó (ya tenía)

    def test_all_regenera(self):
        ya = XsysSocioFoto.objects.create(id_cliente=3, nro=1, imagen=_png(), thumbnail=b"prev", sha256="c")
        call_command("xsys_thumbnails", "--all")
        ya.refresh_from_db()
        self.assertNotEqual(bytes(ya.thumbnail), b"prev")
        self.assertEqual(ya.thumbnail[:2], b"\xff\xd8")

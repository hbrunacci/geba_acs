"""Que las pantallas se puedan usar, no sólo que devuelvan 200.

Motivo: la pantalla de concesionarios devolvía 200, el JS parseaba bien y aun
así no se podía hacer clic en nada. ``dashboard.css`` define su propia clase
``.modal`` (position:fixed, inset:0, display:grid, z-index 1000) y se carga
DESPUÉS de Bootstrap, así que un modal de Bootstrap pisaba su ``display:none``,
quedaba desplegado a pantalla completa e invisible —el ``.fade`` lo deja en
opacidad 0— y tapaba todos los clics de la página.
"""

import re

from django.contrib.auth.models import User
from django.test import TestCase

PANTALLAS = (
    "/concesionarios/",
    "/concesionarios/ingresos/",
    "/concesionarios/empresas/",
    "/concesionarios/horarios/",
)

# <div class="modal ..." ...> con todos sus atributos, para poder mirarlos.
_TAG_MODAL = re.compile(r"<div[^>]*\bclass=\"[^\"]*\bmodal\b[^\"]*\"[^>]*>")
# El atributo `hidden` suelto. Ojo: `aria-hidden` NO cuenta —no esconde nada— y
# era justamente lo que traía el markup de Bootstrap que rompió la pantalla.
_ATRIBUTO_HIDDEN = re.compile(r"(?<![\w-])hidden(?=[\s>=])")


class PantallasUsablesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("jefe", "j@x.com", "x")
        self.client.force_login(self.user)

    def test_todas_responden(self):
        for url in PANTALLAS:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_no_se_filtra_sintaxis_de_template_a_la_pantalla(self):
        """`{# … #}` es de UNA línea: en varias, Django lo imprime tal cual.

        No falla, no avisa, y la pantalla aparece con texto crudo arriba. Pasó
        con el comentario de los modales. Un 200 no alcanza para dar por buena
        una pantalla: hay que mirar lo que sale.
        """
        for url in PANTALLAS:
            html = self.client.get(url).content.decode()
            cuerpo = html.split("</head>", 1)[-1]
            for marca in ("{#", "#}", "{% comment %}", "{% endcomment %}", "{% block"):
                self.assertNotIn(marca, cuerpo, f"{url}: quedó «{marca}» en el HTML")

    def test_ningun_modal_queda_desplegado_tapando_la_pagina(self):
        """Un .modal sin `hidden` cubre la pantalla entera y come los clics."""
        for url in PANTALLAS:
            html = self.client.get(url).content.decode()
            for tag in _TAG_MODAL.findall(html):
                # Se ignoran los contenedores internos (.modal__overlay, etc.).
                if re.search(r'class="[^"]*\bmodal__', tag):
                    continue
                self.assertTrue(_ATRIBUTO_HIDDEN.search(tag),
                                f"{url}: modal sin el atributo hidden -> {tag}")

    def test_el_control_distingue_hidden_de_aria_hidden(self):
        """El markup que rompió la pantalla traía aria-hidden y ningún hidden."""
        roto = '<div class="modal fade" id="x" tabindex="-1" aria-hidden="true">'
        sano = '<div class="modal" id="x" hidden>'
        self.assertIsNone(_ATRIBUTO_HIDDEN.search(roto))
        self.assertIsNotNone(_ATRIBUTO_HIDDEN.search(sano))

    def test_no_se_usan_modales_de_bootstrap(self):
        """Chocan con el .modal de la casa. Si hace falta uno, va con `hidden`."""
        for url in PANTALLAS:
            html = self.client.get(url).content.decode()
            self.assertNotIn("data-bs-toggle=\"modal\"", html, url)
            self.assertNotIn("bootstrap.Modal", html, url)

    def test_los_botones_que_abren_modales_apuntan_a_uno_que_existe(self):
        html = self.client.get("/concesionarios/").content.decode()
        objetivos = re.findall(r'data-open-modal="([^"]+)"', html)
        self.assertTrue(objetivos)
        for objetivo in objetivos:
            self.assertIn(f'id="{objetivo}"', html)

    def test_el_js_de_cada_pantalla_parsea(self):
        """Un error de sintaxis deja la página entera sin manejadores."""
        try:
            import esprima
        except ImportError:  # pragma: no cover - depende del entorno
            self.skipTest("esprima no está instalado")
        for url in PANTALLAS:
            html = self.client.get(url).content.decode()
            for bloque in re.findall(r"<script>(.*?)</script>", html, re.S):
                try:
                    esprima.parseScript(bloque)
                except Exception as exc:  # pragma: no cover - sólo si se rompe
                    self.fail(f"{url}: JS inválido -> {exc}")

    def test_la_hoja_de_estilos_neutraliza_un_modal_de_bootstrap(self):
        """Red de seguridad: si alguien vuelve a meter uno, que no tape nada."""
        from pathlib import Path

        from django.conf import settings
        css = Path(settings.BASE_DIR, "common", "static", "common", "css", "dashboard.css")
        self.assertIn(".modal.fade:not(.show)", css.read_text(encoding="utf-8"))

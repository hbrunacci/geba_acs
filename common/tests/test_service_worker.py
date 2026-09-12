"""Pruebas del service worker de la PWA.

El worker anterior interceptaba todo GET del mismo origen y respondía
cache-first. Eso rompía la descarga del Excel del tablero —una navegación que el
worker contestaba desde el cache, con lo que el navegador cancelaba y la página
parecía recargarse sola— y, peor, guardaba en el Cache Storage del navegador las
respuestas de ``/api/``: fichas de socios, cuenta corriente y deuda quedaban en
el disco de la máquina de mostrador y sobrevivían al cierre de sesión.

Estas pruebas leen el archivo servido. No ejecutan JavaScript, así que verifican
la forma del worker, no su comportamiento en el navegador: alcanzan para que la
estrategia no vuelva a caer en cache-first sobre todo sin que nadie se entere.
"""

from __future__ import annotations

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

H = {"HTTP_HOST": "localhost"}


def _fuente() -> str:
    ruta = finders.find("common/js/service-worker.js")
    assert ruta, "No se encontró el service worker"
    with open(ruta, encoding="utf-8") as fh:
        return fh.read()


def _codigo() -> str:
    """La fuente sin las líneas de comentario.

    El encabezado del worker explica qué hacía mal la versión anterior y cita el
    código viejo textualmente, así que buscar "lo que ya no tiene que estar"
    sobre la fuente cruda da falsos positivos contra la propia documentación.
    """
    return "\n".join(l for l in _fuente().splitlines()
                     if not l.lstrip().startswith("//"))


class ServiceWorkerTests(TestCase):

    def test_se_sirve_desde_la_raiz(self):
        # Tiene que estar en / para poder controlar todo el sitio.
        r = self.client.get(reverse("common:service_worker"), **H)
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r["Content-Type"])
        self.assertEqual(r["Service-Worker-Allowed"], "/")

    def test_no_intercepta_las_llamadas_a_la_api(self):
        # La regla de oro: sin respondWith para /api/, el navegador va derecho a
        # la red y nada con datos de socios entra al cache.
        codigo = _codigo()
        self.assertIn('request.mode === "navigate"', codigo)
        self.assertIn("/static/", codigo)
        # Ya no existe el catch-all que devolvía cache para cualquier cosa.
        self.assertNotIn("return cachedResponse || networkResponse", codigo)

    def test_las_navegaciones_van_primero_a_la_red(self):
        codigo = _codigo()
        i = codigo.index('request.mode === "navigate"')
        bloque = codigo[i:i + 260]
        self.assertIn("fetch(request)", bloque)
        self.assertIn("catch", bloque)   # el cache es sólo el paracaídas

    def test_el_cache_cambio_de_nombre_para_purgar_el_viejo(self):
        # `activate` borra todo cache cuyo nombre no sea el actual: subir la
        # versión es lo que se lleva puesto el cache con datos de socios.
        codigo = _codigo()
        self.assertIn('CACHE_NAME = "acs-pwa-v2"', codigo)
        self.assertNotIn('"acs-pwa-v1"', codigo)

    def test_sigue_sin_tocar_los_metodos_que_escriben(self):
        self.assertIn('request.method !== "GET"', _codigo())

    def test_el_shell_cacheado_es_solo_estatico_y_la_portada(self):
        codigo = _codigo()
        i = codigo.index("APP_SHELL = [")
        bloque = codigo[i:codigo.index("]", i)]
        self.assertNotIn("/api/", bloque)
        self.assertNotIn("/xsys/", bloque)


class DescargaExcelTests(TestCase):
    """El botón de Excel no puede volver a ser una navegación."""

    def test_el_tablero_baja_el_excel_por_fetch_y_no_por_location(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        User = get_user_model()
        u = User.objects.create_user("bajador", password="x")
        u.groups.add(Group.objects.get_or_create(name="tableros")[0])
        self.client.force_login(u)

        html = self.client.get(reverse("xsys_tableros"), **H).content.decode()
        self.assertIn("/api/xsys/tableros/deuda/excel/", html)
        # window.location convertía la descarga en navegación y el service
        # worker se la comía.
        self.assertNotIn('window.location = "/api/xsys/tableros/deuda/excel/', html)
        self.assertIn("createObjectURL", html)
        self.assertIn("revokeObjectURL", html)

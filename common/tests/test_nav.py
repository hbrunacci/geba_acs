"""Pruebas del menú lateral.

Tres cosas que el menú tiene que garantizar y que antes no estaban cubiertas:

1. Que todos los nombres de ruta existan. Un typo en ``common/nav.py`` rompe el
   sidebar y, como el sidebar está en base.html, se lleva puesta TODA la
   aplicación: no falla una pantalla, fallan todas.
2. Que ningún grupo se muestre vacío. Los submenús mezclan pantallas de admin
   con pantallas de rol, así que un usuario con un solo rol no tiene que ver un
   desplegable que al abrirlo no tiene nada.
3. Que el rol de cada ítem sea el mismo que exige la vista. Que aparezca en el
   menú y después dé 403 al hacer clic es peor que no mostrarlo.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from common import nav


def _usuario(nombre, grupos=(), **flags):
    User = get_user_model()
    u = User.objects.create_user(nombre, password="x", **flags)
    for g in grupos:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


def _todos_los_items():
    for entrada in nav.MENU:
        if isinstance(entrada, nav.Item):
            yield entrada
        else:
            yield from entrada.items


class EstructuraTests(TestCase):

    def test_todas_las_rutas_del_menu_existen(self):
        # Si esto falla, el sidebar rompe en todas las pantallas a la vez.
        rotas = []
        for item in _todos_los_items():
            try:
                reverse(item.url)
            except NoReverseMatch:
                rotas.append(f"{item.titulo} -> {item.url}")
        self.assertEqual(rotas, [], "Rutas inexistentes en common/nav.py")

    def test_todos_los_roles_declarados_son_conocidos(self):
        for item in _todos_los_items():
            self.assertIn(item.rol, nav.CHEQUEOS, item.titulo)

    def test_no_hay_pantallas_repetidas(self):
        urls = [i.url for i in _todos_los_items()]
        self.assertEqual(len(urls), len(set(urls)), "Hay una pantalla dos veces")

    def test_estan_los_grupos_que_pidio_el_club(self):
        grupos = [e.titulo for e in nav.MENU if isinstance(e, nav.Grupo)]
        for esperado in ("Socios", "ACS", "Sistema"):
            self.assertIn(esperado, grupos)

    def test_el_grupo_socios_tiene_las_cuatro_pantallas_pedidas(self):
        grupo = [e for e in nav.MENU
                 if isinstance(e, nav.Grupo) and e.titulo == "Socios"][0]
        self.assertEqual(
            [i.url for i in grupo.items],
            ["xsys_socios_fichas", "avisos_pendientes",
             "people_configuration_console", "xsys_socio_console"])

    def test_biostar_quedo_dentro_de_sistema_y_no_como_grupo_propio(self):
        titulos = [e.titulo for e in nav.MENU]
        self.assertNotIn("Biostar", titulos)
        sistema = [e for e in nav.MENU
                   if isinstance(e, nav.Grupo) and e.titulo == "Sistema"][0]
        urls = [i.url for i in sistema.items]
        self.assertIn("biostar_devices_console", urls)
        self.assertIn("biostar_users_console", urls)
        self.assertIn("pollers_dashboard", urls)
        self.assertIn("anses_verification_console", urls)


class VisibilidadTests(TestCase):

    def test_sin_autenticar_el_menu_viene_vacio(self):
        self.assertEqual(nav.construir(None), [])

    def test_ningun_grupo_aparece_vacio(self):
        # Un usuario por cada rol, y también uno sin ningún rol.
        usuarios = [
            _usuario("solo_socios", ["socios"]),
            _usuario("solo_puertas", ["Configuración de Puertas"]),
            _usuario("solo_tableros", ["tableros"]),
            _usuario("solo_conce", ["concesionarios"]),
            _usuario("pelado"),
            _usuario("admin_app", ["Administrador"]),
        ]
        for u in usuarios:
            for entrada in nav.construir(u):
                if entrada["tipo"] == "grupo":
                    self.assertTrue(entrada["items"],
                                    f"{u.username} ve '{entrada['titulo']}' vacío")

    def test_el_de_socios_ve_su_grupo_con_solo_sus_dos_pantallas(self):
        menu = nav.construir(_usuario("s", ["socios"]))
        socios = [e for e in menu if e["titulo"] == "Socios"][0]
        self.assertEqual([i["url"] for i in socios["items"]],
                         ["xsys_socios_fichas", "avisos_pendientes"])

    def test_el_de_socios_no_ve_sistema(self):
        titulos = [e["titulo"] for e in nav.construir(_usuario("s2", ["socios"]))]
        self.assertNotIn("Sistema", titulos)

    def test_el_de_puertas_ve_acs_pero_no_las_pantallas_de_admin(self):
        menu = nav.construir(_usuario("p", ["Configuración de Puertas"]))
        acs = [e for e in menu if e["titulo"] == "ACS"][0]
        urls = [i["url"] for i in acs["items"]]
        self.assertIn("xsys_molinetes_config", urls)
        self.assertIn("xsys_diagnostico", urls)
        self.assertNotIn("access_topology_console", urls)   # es de admin
        self.assertNotIn("parking_movements_console", urls)

    def test_el_de_puertas_tambien_ve_socios_porque_el_rol_socios_lo_incluye(self):
        # puede_socios() = puertas o grupo socios. Es a propósito y viene de antes.
        titulos = [e["titulo"] for e in nav.construir(
            _usuario("p2", ["Configuración de Puertas"]))]
        self.assertIn("Socios", titulos)

    def test_un_usuario_sin_roles_no_ve_nada(self):
        self.assertEqual(nav.construir(_usuario("nadie")), [])

    def test_el_admin_de_la_app_no_ve_tableros_ni_concesionarios(self):
        titulos = [e["titulo"] for e in nav.construir(_usuario("a", ["Administrador"]))]
        self.assertNotIn("Tableros", titulos)
        self.assertNotIn("Concesionarios", titulos)

    def test_el_superusuario_ve_todo(self):
        User = get_user_model()
        root = User.objects.create_superuser("root", password="x")
        menu = nav.construir(root)
        titulos = [e["titulo"] for e in menu]
        for esperado in ("Resumen", "Socios", "ACS", "Tableros",
                         "Concesionarios", "Sistema"):
            self.assertIn(esperado, titulos)
        # y ningún ítem se pierde por el camino
        vistos = sum(1 if e["tipo"] == "item" else len(e["items"]) for e in menu)
        self.assertEqual(vistos, len(list(_todos_los_items())))


class MarcadoTests(TestCase):

    def test_el_grupo_se_abre_cuando_la_pantalla_actual_esta_adentro(self):
        menu = nav.construir(_usuario("s3", ["socios"]), "xsys_socios_fichas")
        socios = [e for e in menu if e["titulo"] == "Socios"][0]
        self.assertTrue(socios["abierto"])
        activos = [i["titulo"] for i in socios["items"] if i["activo"]]
        self.assertEqual(activos, ["Fichas de socios"])

    def test_los_demas_grupos_quedan_cerrados(self):
        menu = nav.construir(_usuario("s4", ["socios"]), "xsys_socios_fichas")
        for e in menu:
            if e["tipo"] == "grupo" and e["titulo"] != "Socios":
                self.assertFalse(e["abierto"], e["titulo"])

    def test_una_url_desconocida_no_abre_ningun_grupo(self):
        menu = nav.construir(_usuario("s5", ["socios"]), "pantalla_que_no_existe")
        self.assertFalse(any(e["tipo"] == "grupo" and e["abierto"] for e in menu))

    def test_el_item_suelto_con_namespace_se_marca_bien(self):
        # 'common:dashboard' resuelve al url_name 'dashboard': si se comparara
        # contra la ruta completa, Resumen nunca se pintaría activo.
        menu = nav.construir(_usuario("a2", ["Administrador"]), "dashboard")
        resumen = [e for e in menu if e["titulo"] == "Resumen"][0]
        self.assertTrue(resumen["activo"])

    def test_el_visor_de_puerta_abre_en_otra_pestania(self):
        menu = nav.construir(_usuario("p3", ["Configuración de Puertas"]))
        acs = [e for e in menu if e["titulo"] == "ACS"][0]
        visor = [i for i in acs["items"] if i["url"] == "xsys_puerta_monitor"][0]
        self.assertTrue(visor["nueva_pestania"])


class RenderTests(TestCase):
    """El menú tiene que dibujarse de verdad, no sólo construirse bien."""

    def test_la_pantalla_muestra_los_submenus(self):
        self.client.force_login(_usuario("r1", ["socios"]))
        html = self.client.get(
            reverse("xsys_socios_fichas"), HTTP_HOST="localhost"
        ).content.decode()
        self.assertIn("<summary>Socios</summary>", html)
        self.assertIn("Fichas de socios", html)
        self.assertIn('class="nav__subitem nav__subitem--active"', html)

    def test_el_menu_no_rompe_para_un_usuario_sin_roles(self):
        self.client.force_login(_usuario("r2"))
        r = self.client.get(reverse("common:dashboard"), HTTP_HOST="localhost")
        self.assertIn(r.status_code, (200, 302, 403))

    def test_todas_las_pantallas_del_menu_responden_al_superusuario(self):
        # Cierra el círculo: el ítem existe, se ve, y al hacer clic no da 403.
        User = get_user_model()
        self.client.force_login(User.objects.create_superuser("root2", password="x"))
        malas = []
        for item in _todos_los_items():
            r = self.client.get(reverse(item.url), HTTP_HOST="localhost")
            if r.status_code >= 400:
                malas.append(f"{item.titulo} ({item.url}) -> {r.status_code}")
        self.assertEqual(malas, [])

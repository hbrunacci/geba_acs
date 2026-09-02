"""Los grupos que dejan las migraciones y a quién habilitan."""

from django.contrib.auth.models import Group, User
from django.test import TestCase

from common.roles import (
    GRUPO_ADMIN,
    GRUPO_CONCESIONARIOS,
    GRUPO_RESPONSABLES,
    puede_concesionarios,
)


class GruposTests(TestCase):
    def test_las_migraciones_dejan_los_dos_grupos(self):
        for nombre in (GRUPO_CONCESIONARIOS, GRUPO_RESPONSABLES):
            self.assertTrue(Group.objects.filter(name=nombre).exists(), nombre)

    def test_no_quedo_el_grupo_viejo_con_mayuscula(self):
        """Dos grupos casi iguales terminan con alguien asignando el que no rige."""
        self.assertFalse(Group.objects.filter(name="Concesionarios").exists())

    def test_los_nombres_son_los_que_pidio_el_club(self):
        self.assertEqual(GRUPO_CONCESIONARIOS, "concesionarios")
        self.assertEqual(GRUPO_RESPONSABLES, "responsables")


class PuedeConcesionariosTests(TestCase):
    def test_superusuario(self):
        self.assertTrue(puede_concesionarios(
            User.objects.create_superuser("s", "s@x.com", "x")))

    def test_staff(self):
        self.assertTrue(puede_concesionarios(
            User.objects.create_user("t", password="x", is_staff=True)))

    def test_con_el_grupo(self):
        u = User.objects.create_user("g", password="x")
        u.groups.add(Group.objects.get(name=GRUPO_CONCESIONARIOS))
        self.assertTrue(puede_concesionarios(u))

    def test_el_grupo_Administrador_no_alcanza(self):
        u = User.objects.create_user("a", password="x")
        u.groups.add(Group.objects.get_or_create(name=GRUPO_ADMIN)[0])
        self.assertFalse(puede_concesionarios(u))

    def test_responsables_no_alcanza(self):
        u = User.objects.create_user("r", password="x")
        u.groups.add(Group.objects.get(name=GRUPO_RESPONSABLES))
        self.assertFalse(puede_concesionarios(u))

    def test_un_usuario_suelto_no(self):
        self.assertFalse(puede_concesionarios(User.objects.create_user("x", password="x")))

    def test_anonimo_no(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(puede_concesionarios(AnonymousUser()))

    def test_inactivo_con_grupo_no_entra(self):
        """Un usuario dado de baja no puede seguir entrando por el grupo."""
        u = User.objects.create_user("i", password="x", is_active=False)
        u.groups.add(Group.objects.get(name=GRUPO_CONCESIONARIOS))
        self.assertEqual(self.client.login(username="i", password="x"), False)

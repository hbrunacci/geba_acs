"""El fixture de la nómina de concesiones tiene que poder cargarse y ser coherente.

Se generó cruzando la planilla de concesiones contra el espejo de xSys: 99 de
las 101 personas resolvieron por DNI exacto y 2 eran legajos duplicados del
mismo DNI, resueltos a mano hacia el legajo que funciona en el control de
acceso. Si alguien lo edita y lo rompe, esto lo dice.
"""

from django.test import TestCase

from concesionarios.models import Concesionario, Empresa


class FixtureNominaTests(TestCase):
    fixtures = ["nomina_concesiones"]

    def test_carga_las_siete_concesiones_con_su_cuit(self):
        self.assertEqual(Empresa.objects.count(), 7)
        self.assertFalse(Empresa.objects.filter(cuit="").exists())

    def test_carga_las_ciento_una_personas(self):
        self.assertEqual(Concesionario.objects.count(), 101)

    def test_no_hay_un_legajo_en_dos_empresas(self):
        ids = list(Concesionario.objects.values_list("id_cliente", flat=True))
        self.assertEqual(len(ids), len(set(ids)))

    def test_el_reparto_por_empresa_es_el_de_la_planilla(self):
        esperado = {
            "Bar Hockey - Camacho": 9,
            "El Castillo Eventos": 14,
            "Hoyo 9": 7,
            "Confitería Tenis - Sinato": 31,
            "Corner y Pro Shop": 15,
            "Punto Cero - Take Away": 7,
            "Estadio, SA": 18,
        }
        real = {e.nombre: e.concesionarios.count() for e in Empresa.objects.all()}
        self.assertEqual(real, esperado)

    def test_los_dos_duplicados_quedan_explicados(self):
        """No se elige un legajo sobre otro sin dejar dicho por qué."""
        con_nota = Concesionario.objects.exclude(observaciones="")
        self.assertEqual(con_nota.count(), 2)
        for c in con_nota:
            self.assertIn("duplicado", c.observaciones)

    def test_todos_arrancan_activos_y_sin_horario(self):
        """El horario lo asigna el club; el fixture no inventa restricciones."""
        self.assertEqual(Concesionario.objects.filter(activo=False).count(), 0)
        self.assertEqual(Concesionario.objects.exclude(horario=None).count(), 0)
        self.assertEqual(Empresa.objects.exclude(horario=None).count(), 0)

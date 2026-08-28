from datetime import date

from django.test import SimpleTestCase

from xsys.services.cuota import cuota_al_dia, fecha_limite_ingreso

# Config del club en CD_Accesos para los accesos que exigen cuota.
DIAS, MESES = 40, 0


class CuotaRuleTests(SimpleTestCase):
    """La regla del visor tiene que dar lo MISMO que CF_SCA_ValidarUltCuotaPaga.

    Los valores esperados no salen de reimplementar la fórmula acá: se sacaron
    preguntándole a la función de xSys, día por día, cuál es el último que
    habilita para cada mes de cuota.
    """

    def test_todo_el_mes_pagado_mas_la_gracia(self):
        # Agosto pago -> entra todo agosto y 40 días más: hasta el 10/10.
        self.assertEqual(fecha_limite_ingreso(date(2026, 8, 1), DIAS, MESES), date(2026, 10, 10))

    def test_el_mes_pagado_entero_habilita(self):
        # El último día del mes pagado todavía entra: la gracia arranca después.
        self.assertTrue(cuota_al_dia(date(2026, 8, 1), hoy=date(2026, 8, 31),
                                     dias_gracia=DIAS, meses_gracia=MESES))

    def test_limite_justo(self):
        self.assertTrue(cuota_al_dia(date(2026, 8, 1), hoy=date(2026, 10, 10),
                                     dias_gracia=DIAS, meses_gracia=MESES))
        self.assertFalse(cuota_al_dia(date(2026, 8, 1), hoy=date(2026, 10, 11),
                                      dias_gracia=DIAS, meses_gracia=MESES))

    def test_coincide_con_xsys_los_doce_meses(self):
        """Tabla verificada contra la función de xSys (ver docstring de la clase).

        Es el test que importa: el bug anterior era justamente que la gracia
        real variaba entre 39 y 42 días según el largo del mes.
        """
        esperado = {
            1: date(2026, 3, 12), 2: date(2026, 4, 9), 3: date(2026, 5, 10),
            4: date(2026, 6, 9), 5: date(2026, 7, 10), 6: date(2026, 8, 9),
            7: date(2026, 9, 9), 8: date(2026, 10, 10), 9: date(2026, 11, 9),
            10: date(2026, 12, 10), 11: date(2027, 1, 9), 12: date(2027, 2, 9),
        }
        for mes, limite in esperado.items():
            with self.subTest(mes=mes):
                self.assertEqual(fecha_limite_ingreso(date(2026, mes, 1), DIAS, MESES), limite)

    def test_la_gracia_es_siempre_de_40_dias_corridos(self):
        for mes in range(1, 13):
            with self.subTest(mes=mes):
                ucp = date(2026, mes, 1)
                limite = fecha_limite_ingreso(ucp, DIAS, MESES)
                fin_de_mes = date(2026, mes, 1).replace(day=28)
                while True:
                    siguiente = fin_de_mes.toordinal() + 1
                    if date.fromordinal(siguiente).month != mes:
                        break
                    fin_de_mes = date.fromordinal(siguiente)
                self.assertEqual((limite - fin_de_mes).days, 40)

    def test_ult_cuota_paga_que_no_cae_dia_1(self):
        """Hay miles de socios así: se toma el fin del mes del pago, como EOMONTH."""
        self.assertEqual(fecha_limite_ingreso(date(2026, 8, 16), DIAS, MESES), date(2026, 10, 10))

    def test_meses_de_gracia_extra(self):
        # El parámetro sigue existiendo por si algún acceso lo usa.
        self.assertEqual(fecha_limite_ingreso(date(2026, 8, 1), 10, 2), date(2026, 11, 10))

    def test_sin_cuota_no_al_dia(self):
        self.assertIsNone(fecha_limite_ingreso(None))
        self.assertFalse(cuota_al_dia(None))

    def test_cuenta_master_futuro(self):
        # ult_cuota_paga muy en el futuro (cuentas master 2050) -> siempre al día.
        self.assertTrue(cuota_al_dia(date(2050, 1, 1), hoy=date(2026, 7, 18)))

from datetime import date

from django.test import SimpleTestCase

from xsys.services.cuota import cuota_al_dia, fecha_limite_ingreso


class CuotaRuleTests(SimpleTestCase):
    def test_fecha_limite_pago_junio(self):
        # Pagó hasta junio; 1er impago=julio(31), 2do=agosto(31), gracia=10.
        self.assertEqual(fecha_limite_ingreso(date(2026, 6, 1), 10), date(2026, 8, 12))

    def test_fecha_limite_pago_mayo(self):
        # Pagó hasta mayo; junio(30)+julio(31)+10.
        self.assertEqual(fecha_limite_ingreso(date(2026, 5, 1), 10), date(2026, 7, 11))

    def test_al_dia_adeuda_una(self):
        # Pagó junio, hoy 18/jul -> adeuda solo julio -> al día.
        self.assertTrue(cuota_al_dia(date(2026, 6, 1), hoy=date(2026, 7, 18), dias_vencimiento=10))

    def test_vencida_adeuda_dos(self):
        # Pagó mayo, hoy 18/jul -> adeuda junio+julio pasada la gracia -> vencida.
        self.assertFalse(cuota_al_dia(date(2026, 5, 1), hoy=date(2026, 7, 18), dias_vencimiento=10))

    def test_limite_justo(self):
        self.assertTrue(cuota_al_dia(date(2026, 5, 1), hoy=date(2026, 7, 11), dias_vencimiento=10))
        self.assertFalse(cuota_al_dia(date(2026, 5, 1), hoy=date(2026, 7, 12), dias_vencimiento=10))

    def test_sin_cuota_no_al_dia(self):
        self.assertIsNone(fecha_limite_ingreso(None))
        self.assertFalse(cuota_al_dia(None))

    def test_cuenta_master_futuro(self):
        # ult_cuota_paga muy en el futuro (cuentas master 2050) -> siempre al día.
        self.assertTrue(cuota_al_dia(date(2050, 1, 1), hoy=date(2026, 7, 18)))

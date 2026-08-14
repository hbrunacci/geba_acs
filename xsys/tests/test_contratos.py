from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from xsys.models import XsysContrato, XsysSocio
from xsys.services import contratos


def _contrato(id_contrato, id_cliente, descripcion, **kw):
    base = dict(
        id_contrato=id_contrato, id_cliente=id_cliente, descripcion=descripcion,
        activo=1, ultimo_cbte_fecha=date.today(),
    )
    base.update(kw)
    return XsysContrato.objects.create(**base)


class ResumenPorSocioTests(TestCase):
    def test_usa_el_producto_como_nombre_y_recorta_el_sufijo(self):
        """El tipo dice DEPORTES FEDERADOS para todos; la disciplina es el producto."""
        _contrato(1, 100, "DEPORTES FEDERADOS", producto_desc="ESGRIMA - ACT MAYOR..CUOTA")
        fila = contratos.resumen_por_socio([100])[100][0]
        self.assertEqual(fila["nombre"], "ESGRIMA")
        self.assertEqual(fila["tipo"], "DEPORTES FEDERADOS")

    def test_cae_a_la_descripcion_si_no_hay_producto(self):
        _contrato(1, 100, "CUOTA SOCIAL", producto_desc="")
        self.assertEqual(contratos.resumen_por_socio([100])[100][0]["nombre"], "CUOTA SOCIAL")

    def test_estados_segun_deuda_y_pago(self):
        _contrato(1, 100, "CON DEUDA", deuda=1000, ultimo_pago_fecha=date(2026, 8, 3))
        _contrato(2, 100, "AL DIA", deuda=0, ultimo_pago_fecha=date(2026, 8, 3))
        _contrato(3, 100, "NUNCA PAGO", deuda=0, ultimo_pago_fecha=None)
        estados = {f["nombre"]: f["estado"] for f in contratos.resumen_por_socio([100])[100]}
        self.assertEqual(estados, {"CON DEUDA": "deuda", "AL DIA": "ok", "NUNCA PAGO": "sin_pagos"})

    def test_excluye_contratos_sin_facturacion_reciente(self):
        """Los residuos históricos (PANDEMIA 2020, RECUPERO 2021) no deben aparecer."""
        _contrato(1, 100, "VIGENTE", ultimo_cbte_fecha=date.today())
        _contrato(2, 100, "RESIDUO 2021", ultimo_cbte_fecha=date.today() - timedelta(days=400))
        _contrato(3, 100, "SIN FACTURAR NUNCA", ultimo_cbte_fecha=None)
        nombres = [f["nombre"] for f in contratos.resumen_por_socio([100])[100]]
        self.assertEqual(nombres, ["VIGENTE"])

    def test_excluye_contratos_ya_vencidos(self):
        _contrato(1, 100, "VENCIDO", fecha_hasta=timezone.now() - timedelta(days=1))
        _contrato(2, 100, "ABIERTO", fecha_hasta=None)
        _contrato(3, 100, "HASTA FIN DE ANIO", fecha_hasta=timezone.now() + timedelta(days=90))
        nombres = sorted(f["nombre"] for f in contratos.resumen_por_socio([100])[100])
        self.assertEqual(nombres, ["ABIERTO", "HASTA FIN DE ANIO"])

    def test_adherente_hereda_los_contratos_del_titular(self):
        """A los adherentes la cuota se les factura al titular: sin esto quedan vacíos."""
        XsysSocio.objects.create(id_cliente=200, apellido="GARCIA", nombre="MORA", id_cliente_ref=300)
        XsysSocio.objects.create(id_cliente=300, apellido="SAITA", nombre="MARIA FLORENCIA")
        _contrato(1, 300, "CUOTA SOCIAL", ultimo_pago_fecha=date(2026, 8, 3), deuda=0)

        filas = contratos.resumen_por_socio([200])[200]
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["nombre"], "CUOTA SOCIAL")
        self.assertEqual(filas[0]["via_titular"], "SAITA, MARIA FLORENCIA")

    def test_los_contratos_propios_ganan_sobre_los_del_titular(self):
        XsysSocio.objects.create(id_cliente=200, apellido="GARCIA", nombre="MORA", id_cliente_ref=300)
        XsysSocio.objects.create(id_cliente=300, apellido="SAITA", nombre="MARIA FLORENCIA")
        _contrato(1, 300, "CUOTA SOCIAL")
        _contrato(2, 200, "DEPORTES FEDERADOS", producto_desc="HOCKEY - ACT MENOR..CUOTA")

        filas = contratos.resumen_por_socio([200])[200]
        self.assertEqual([f["nombre"] for f in filas], ["HOCKEY"])
        self.assertEqual(filas[0]["via_titular"], "")

    def test_socio_sin_contratos_no_aparece(self):
        self.assertEqual(contratos.resumen_por_socio([999]), {})

    def test_sin_ids_no_consulta(self):
        with self.assertNumQueries(0):
            self.assertEqual(contratos.resumen_por_socio([]), {})

    def test_unifica_contratos_repetidos_del_mismo_producto(self):
        """Dos altas sucesivas de RUGBY no deben verse como dos líneas iguales."""
        _contrato(1, 100, "DEPORTES FEDERADOS", producto_desc="RUGBY - ACT MENOR..CUOTA",
                  deuda=0, ultimo_pago_fecha=date(2026, 3, 1))
        _contrato(2, 100, "DEPORTES FEDERADOS", producto_desc="RUGBY - ACT MAYOR..CUOTA",
                  deuda=500, ultimo_pago_fecha=date(2026, 8, 4))
        filas = contratos.resumen_por_socio([100])[100]
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["nombre"], "RUGBY")
        self.assertEqual(filas[0]["deuda"], 500.0)          # deudas sumadas
        self.assertEqual(filas[0]["estado"], "deuda")       # gana el peor estado
        self.assertEqual(filas[0]["ultimo_pago"], "2026-08-04")  # gana el pago más reciente

    def test_lo_urgente_va_primero(self):
        """La tarjeta muestra 3 líneas: la deuda no puede quedar tapada."""
        _contrato(1, 100, "AL DIA", deuda=0, ultimo_pago_fecha=date(2026, 8, 3))
        _contrato(2, 100, "DEBE POCO", deuda=100)
        _contrato(3, 100, "NUNCA PAGO", deuda=0, ultimo_pago_fecha=None)
        _contrato(4, 100, "DEBE MUCHO", deuda=90000)
        nombres = [f["nombre"] for f in contratos.resumen_por_socio([100])[100]]
        self.assertEqual(nombres, ["DEBE MUCHO", "DEBE POCO", "NUNCA PAGO", "AL DIA"])

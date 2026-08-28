"""Tests del diagnóstico "¿por qué no entra?".

Todo lo que toca xSys se hace contra un cursor falso: el valor de estos tests no
es comprobar SQL, es fijar las conclusiones que el módulo saca de los datos —
que son las que se le muestran al operador y las que costó descubrir.
"""

from datetime import date, datetime

from django.test import TestCase

from xsys.services import diagnostico as dx

HOY = date(2026, 8, 28)


def _comprobante(**kw):
    """Una fila de comprobante ya normalizada, con valores por defecto sanos."""
    base = {
        "id_trans": 1, "id_producto": "CS", "producto": "CUOTA SOCIAL",
        "tipo": "CUPON CUOTA", "comprobante_nro": 1, "fecha": "2026-06-01",
        "periodo": "2026-06-01", "importe": 100.0, "estado": "COMPLETO",
        "estado_id": 2, "vale_hasta": "2026-12-31", "habilita": True,
        "porque_no": "", "es_factura": True, "gracia": "2 mes(es) + 10 día(s)",
    }
    base.update(kw)
    return base


def _socio(**kw):
    base = {
        "id_cliente": 872811, "nombre": "VAZ CLAUDIO", "doc_nro": "25436475",
        "id_tipo_cli": 1003, "categoria": "ACTIVO MAYOR", "activo": True,
        "fecha_baja": None, "ult_cuota_paga": "2026-12-01", "id_cliente_ref": 0,
        "credencial": "EF2A984A",
    }
    base.update(kw)
    return base


def _acceso(**kw):
    base = {
        "id_acceso": 22, "descripcion": "Acceso CS", "habilitado": False,
        "motivo": "No cumple ninguna condición habilitante", "detalle": "",
        "controla_cuota": 0, "es_barrera": False, "id_contrato": None, "id_tipo_cli": None,
    }
    base.update(kw)
    return base


class NormalizacionTests(TestCase):
    def test_documento_se_compara_sin_puntos_ni_guiones(self):
        self.assertEqual(dx._norm_doc("25.436.475"), "25436475")
        self.assertEqual(dx._norm_doc(" 25-436-475 "), "25436475")

    def test_fecha_a_iso_tolera_none_y_datetime(self):
        self.assertIsNone(dx._d(None))
        self.assertEqual(dx._d(datetime(2026, 5, 20, 13, 4)), "2026-05-20")
        self.assertEqual(dx._d(date(2026, 5, 20)), "2026-05-20")


class ElegirRegistroTests(TestCase):
    """El caso real: un DNI en 15 clientes, 14 dados de baja."""

    def test_prefiere_el_activo_sobre_los_de_baja(self):
        cands = [
            {"id_cliente": 916879, "activo": False},
            {"id_cliente": 872811, "activo": True},
            {"id_cliente": 922065, "activo": False},
        ]
        self.assertEqual(dx._elegir(cands)["id_cliente"], 872811)

    def test_entre_varios_activos_toma_el_ultimo_creado(self):
        cands = [{"id_cliente": 100, "activo": True}, {"id_cliente": 900, "activo": True}]
        self.assertEqual(dx._elegir(cands)["id_cliente"], 900)

    def test_sin_candidatos_devuelve_none(self):
        self.assertIsNone(dx._elegir([]))


class AlertasTests(TestCase):
    """Cada alerta corresponde a una confusión real que costó tiempo."""

    def _alertar(self, **kw):
        base = {
            "candidatos": [_socio()], "socio": _socio(), "accesos": [_acceso()],
            "comprobantes": [], "contratos": [], "local": {},
            "id_acceso_wl": 22, "hoy": HOY,
        }
        base.update(kw)
        return dx._alertas(**base)

    def _titulos(self, alertas):
        return [a["titulo"] for a in alertas]

    def test_avisa_cuando_el_documento_esta_en_varios_clientes(self):
        cands = [_socio(), _socio(id_cliente=916879, activo=False, categoria="INVITADOS")]
        al = self._alertar(candidatos=cands)
        self.assertTrue(any("2 registros" in t for t in self._titulos(al)))

    def test_no_avisa_de_duplicados_cuando_hay_uno_solo(self):
        self.assertFalse(any("registros de xSys" in t for t in self._titulos(self._alertar())))

    def test_ult_cuota_paga_al_dia_pero_sin_comprobante_habilitante(self):
        """El corazón del caso: el campo manual dice una cosa y los pagos otra."""
        comps = [_comprobante(habilita=False, estado="PENDIENTE", estado_id=1,
                              porque_no="comprobante PENDIENTE")]
        al = self._alertar(comprobantes=comps)
        self.assertTrue(any("«Última cuota paga»" in t for t in self._titulos(al)))

    def test_no_alerta_si_algun_comprobante_habilita(self):
        al = self._alertar(comprobantes=[_comprobante(habilita=True)])
        self.assertFalse(any("«Última cuota paga»" in t for t in self._titulos(al)))

    def test_cupon_emitido_pero_pendiente(self):
        comps = [_comprobante(habilita=False, estado="PENDIENTE", estado_id=1, importe=133800.0)]
        al = self._alertar(comprobantes=comps)
        aviso = next(a for a in al if "PENDIENTE" in a["titulo"])
        self.assertIn("133,800.00", aviso["detalle"])
        self.assertIn("PARCIAL o COMPLETO", aviso["detalle"])

    def test_una_nota_de_credito_pendiente_no_cuenta_como_cupon_impago(self):
        comps = [_comprobante(habilita=False, estado="PENDIENTE", estado_id=1,
                              importe=1000.0, es_factura=False)]
        self.assertFalse(any("PENDIENTE" in t for t in self._titulos(self._alertar(comprobantes=comps))))

    def test_avisa_que_el_ultimo_pago_vencio_con_la_fecha(self):
        comps = [_comprobante(habilita=False, estado_id=2, vale_hasta="2026-01-11",
                              periodo="2025-11-01")]
        aviso = next(a for a in self._alertar(comprobantes=comps) if "venció" in a["titulo"])
        self.assertIn("2026-01-11", aviso["detalle"])
        self.assertIn("2 mes(es) + 10 día(s)", aviso["detalle"])

    def test_avisa_cuando_dejo_de_facturarse(self):
        comps = [_comprobante(periodo="2026-06-01")]
        aviso = next(a for a in self._alertar(comprobantes=comps) if "emite comprobante" in a["titulo"])
        self.assertIn("2 meses", aviso["titulo"])

    def test_no_avisa_de_facturacion_si_el_periodo_es_del_mes(self):
        comps = [_comprobante(periodo="2026-08-01")]
        self.assertFalse(any("emite comprobante" in t
                             for t in self._titulos(self._alertar(comprobantes=comps))))

    def test_marca_el_contrato_dado_de_baja_que_igual_habilita(self):
        contratos = [{
            "id_contrato": 759005, "id_tipo_con": 13, "tipo": "CONCESIONARIOS SM",
            "desde": "2026-08-01", "hasta": "2027-01-10", "baja": "2026-08-20",
            "activo": False, "habilita_por_fecha": True, "dado_de_baja_pero_habilita": True,
        }]
        aviso = next(a for a in self._alertar(contratos=contratos) if "dado de baja" in a["titulo"])
        self.assertIn("759005", aviso["detalle"])

    def test_avisa_cuando_la_lista_blanca_local_discrepa_de_xsys(self):
        local = {"whitelist": {"habilitado": True, "motivo": "x", "detalle": "",
                               "id_acceso": 22, "fecha_calculo": "2026-08-01T10:00:00"}}
        al = self._alertar(local=local, accesos=[_acceso(habilitado=False)])
        self.assertTrue(any("no coincide con xSys" in t for t in self._titulos(al)))

    def test_no_avisa_de_discrepancia_cuando_coinciden(self):
        local = {"whitelist": {"habilitado": True, "motivo": "x", "detalle": "",
                               "id_acceso": 22, "fecha_calculo": "2026-08-01T10:00:00"}}
        al = self._alertar(local=local, accesos=[_acceso(habilitado=True)])
        self.assertFalse(any("no coincide con xSys" in t for t in self._titulos(al)))

    def test_avisa_que_el_facial_lo_tiene_cerrado(self):
        local = {"biostar": {"enrolado": True, "nombre": "X", "permite_paso": False}}
        self.assertTrue(any("acceso cerrado" in t for t in self._titulos(self._alertar(local=local))))


class ConclusionTests(TestCase):
    """La frase que el operador lee con la fila esperando."""

    def test_persona_inactiva(self):
        txt = dx._conclusion(_socio(activo=False), [_acceso()], [], 22)
        self.assertIn("inactiva", txt)

    def test_no_entra_por_ningun_lado_usa_la_alerta_grave(self):
        alertas = [{"nivel": "danger", "titulo": "El cupón está PENDIENTE", "detalle": ""}]
        txt = dx._conclusion(_socio(), [_acceso(habilitado=False)], alertas, 22)
        self.assertIn("No entra por ninguna puerta", txt)
        self.assertIn("PENDIENTE", txt)

    def test_entra_por_molinetes_pero_no_por_el_facial(self):
        accesos = [
            _acceso(id_acceso=22, descripcion="Acceso CS", habilitado=False),
            _acceso(id_acceso=14, descripcion="SM-Alcorta", habilitado=True,
                    motivo="Contrato habilitante"),
        ]
        txt = dx._conclusion(_socio(), accesos, [], 22)
        self.assertIn("No entra por los faciales", txt)
        self.assertIn("SM-Alcorta", txt)

    def test_entra_normalmente(self):
        accesos = [_acceso(id_acceso=22, habilitado=True, motivo="Producto comprado")]
        self.assertIn("Entra por 1 acceso", dx._conclusion(_socio(), accesos, [], 22))


class CascadaTests(TestCase):
    """El orden de la cascada es el de xSys: se fija acá para que no se mueva."""

    class _Cur:
        def __init__(self, filas):
            self._filas = filas

        def execute(self, *a, **k):
            return self

        def fetchall(self):
            return self._filas

    def _evaluar(self, *, venc=0, master=0, ucp=0, contrato=0, tipo=0,
                 prod=None, prod_tit=None, flag_ucp=0, activo=True):
        fila = (22, "Acceso CS", flag_ucp, venc, master, ucp, contrato, tipo, prod, prod_tit)
        return dx._evaluar_accesos(self._Cur([fila]), 1, datetime(2026, 8, 28), activo)[0]

    def test_persona_inactiva_gana_sobre_todo_lo_demas(self):
        r = self._evaluar(activo=False, master=1, contrato=99)
        self.assertFalse(r["habilitado"])
        self.assertIn("inactiva", r["motivo"])

    def test_vencimiento_rechaza_aunque_tenga_contrato(self):
        r = self._evaluar(venc=7, contrato=99)
        self.assertFalse(r["habilitado"])

    def test_cuota_obligatoria_rechaza_aunque_tenga_contrato(self):
        r = self._evaluar(flag_ucp=2, ucp=0, contrato=99)
        self.assertFalse(r["habilitado"])
        self.assertIn("Cuota social vencida", r["motivo"])

    def test_master_habilita(self):
        self.assertTrue(self._evaluar(master=1)["habilitado"])

    def test_contrato_habilita_y_se_informa_cual(self):
        r = self._evaluar(contrato=759005)
        self.assertTrue(r["habilitado"])
        self.assertEqual(r["id_contrato"], 759005)

    def test_producto_habilita_cuando_no_hay_contrato_ni_categoria(self):
        r = self._evaluar(prod="CUOTA SOCIAL")
        self.assertTrue(r["habilitado"])
        self.assertEqual(r["detalle"], "CUOTA SOCIAL")

    def test_producto_del_titular_es_el_ultimo_recurso(self):
        r = self._evaluar(prod_tit="CUOTA SOCIAL")
        self.assertTrue(r["habilitado"])
        self.assertIn("titular", r["motivo"])

    def test_sin_nada_no_entra(self):
        r = self._evaluar()
        self.assertFalse(r["habilitado"])
        self.assertIn("No cumple ninguna condición", r["motivo"])


class EntradaTests(TestCase):
    def test_exige_documento_o_id(self):
        with self.assertRaises(ValueError):
            dx.diagnosticar()

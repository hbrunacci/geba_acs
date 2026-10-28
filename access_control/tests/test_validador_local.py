"""Pruebas del validador local (réplica de la cascada de Clever) sin base xSys."""

from __future__ import annotations

import re
from datetime import date, datetime

from django.test import SimpleTestCase

from access_control.services import validador_local as vl


class FuncionesPurasTestCase(SimpleTestCase):
    def test_dia_semana_como_clever(self):
        # DATEPART(DW) con DATEFIRST 7, menos 1: domingo 0 ... sábado 6.
        self.assertEqual(vl.dia_semana_clever(date(2026, 10, 25)), 0)   # domingo
        self.assertEqual(vl.dia_semana_clever(date(2026, 10, 28)), 3)   # miércoles
        self.assertEqual(vl.dia_semana_clever(date(2026, 10, 31)), 6)   # sábado

    def test_fin_de_mes_como_eomonth(self):
        self.assertEqual(vl.fin_de_mes(date(2026, 2, 10)), date(2026, 2, 28))
        self.assertEqual(vl.fin_de_mes(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(vl.fin_de_mes(date(2026, 11, 5), 2), date(2027, 1, 31))

    def test_limite_cuota_como_clever(self):
        # Cuota de agosto, 0 meses + 40 días: entra todo agosto y hasta el 10/10.
        self.assertEqual(vl.limite_cuota(date(2026, 8, 1), 40, 0), date(2026, 10, 10))
        self.assertEqual(vl.limite_cuota(date(2026, 8, 17), 40, 0), date(2026, 10, 10))

    def test_edad_categ_como_sf_edad_categ(self):
        nac = date(2010, 6, 15)
        self.assertEqual(vl.edad_categ(date(2026, 6, 1), nac), 15)   # mismo mes: resta 1
        self.assertEqual(vl.edad_categ(date(2026, 7, 1), nac), 16)   # mes posterior
        self.assertEqual(vl.edad_categ(date(2026, 5, 1), nac), 15)   # mes anterior: resta 1

    def test_es_numerico(self):
        self.assertTrue(vl.es_numerico("9123456789"))
        self.assertFalse(vl.es_numerico("EF2A984A"))
        self.assertFalse(vl.es_numerico("7B55EB04"))


class StubSQL:
    """Simula xSys: cada consulta se identifica por su /*q:nombre*/ y devuelve filas fijas."""

    def __init__(self, **respuestas):
        self.respuestas = respuestas
        self.llamadas: list[tuple[str, tuple]] = []

    def __call__(self, sql: str, params: tuple) -> list[tuple]:
        nombre = re.match(r"/\*q:([a-z_]+)\*/", sql).group(1)
        self.llamadas.append((nombre, params))
        valor = self.respuestas.get(nombre, [])
        return valor(params) if callable(valor) else valor


ACCESO_14 = [(14, "SM-Alcorta", 1, 0, 2, 40, 0, 0, 300, 0, 0, 5)]
MOTIVOS = [(103, "La Persona es inválida", "La Persona es inválida", "La Persona es inválida", ""),
           (104, "La Persona desactivada", "", "", ""), (112, "No cumple ninguna condición habilitante", "", "", ""),
           (118, "Deuda de Actividades", "DEUDA DE ACTIVIDADES", "Deuda de Actividades: pasá por Administración", ""),
           (204, "Habilit. por Tipo de Contrato", "", "", ""), (205, "Habilit. por Tipo de Cliente", "", "", ""),
           (206, "Habilit. por Produc. Comprado", "", "", ""), (309, "La Persona no posee UCP al dia", "", "", "")]
CUANDO = datetime(2026, 10, 28, 15, 0, 0)


def _persona(activo=1, ref=0, tipo=1003, nac=datetime(1990, 5, 1), sexo="M", ucp=datetime(2026, 10, 1)):
    return [(872811, activo, "", ref, tipo, nac, sexo, ucp)]


def _validador(**respuestas) -> tuple[vl.ValidadorLocal, StubSQL]:
    base = dict(acceso=ACCESO_14, motivos=MOTIVOS, tipo_controlador=[("E",)],
                cont_tipos=[(13, "H"), (44, "H")], cli_tipos=[(1006, "H"), (1010, "H")],
                xparam=lambda p: [("INTEGER", None, {"QR_Dinamico": 1, "SCA_Visit_TmpLimit": 15}.get(p[0], 0), None, None)],
                cli_credencial=[(872811,)], persona=_persona(), tipo_flag=[("P",)])
    base.update(respuestas)
    stub = StubSQL(**base)
    return vl.ValidadorLocal(ejecutar=stub, config={"HOST": "x", "DATABASE": "x", "USER": "x", "PASSWORD": "x"}), stub


class CascadaTestCase(SimpleTestCase):
    def test_sin_tag_101(self):
        v, _ = _validador()
        d = v.decidir(tipo_acceso="E", tag="", id_acceso=14, cuando=CUANDO)
        self.assertEqual((d.habilitado, d.id_motivo, d.info), (False, 101, "ERROR"))

    def test_credencial_desconocida_103(self):
        v, _ = _validador(cli_credencial=[(0,)], cli_token=[(0,)])
        d = v.decidir(tipo_acceso="E", tag="ZZZZZZZZ", id_acceso=14, cuando=CUANDO)
        self.assertEqual((d.habilitado, d.id_motivo, d.mensaje), (False, 103, "La Persona es inválida"))
        self.assertEqual(d.id_cliente, 0)

    def test_inactivo_104_salvo_baja_en_revision(self):
        v, _ = _validador(persona=_persona(activo=0))
        d = v.decidir(tipo_acceso="E", tag="EF2A984A", id_acceso=14, cuando=CUANDO)
        self.assertEqual((d.habilitado, d.id_motivo), (False, 104))
        v, _ = _validador(persona=_persona(activo=0), baja_revision=[(1,)], contrato=[(759005,)],
                          contrato_descripcion=[("CONCESIONARIOS SM",)])
        d = v.decidir(tipo_acceso="E", tag="EF2A984A", id_acceso=14, cuando=CUANDO)
        self.assertEqual((d.habilitado, d.id_motivo), (True, 204))

    def test_deuda_actividades_118_con_mensaje_pantalla(self):
        v, _ = _validador(deuda_actividades=[(1,)])
        d = v.decidir(tipo_acceso="E", tag="EF2A984A", id_acceso=14, cuando=CUANDO)
        self.assertEqual((d.habilitado, d.id_motivo, d.info), (False, 118, "ERROR"))
        self.assertEqual(d.mensaje, "Deuda de Actividades: pasá por Administración")

    def test_cuota_vencida_309_solo_para_socios(self):
        # Socio (Flag_Tipo P) con cuota de julio: 31/07 + 40 días = 09/09 < 28/10.
        v, _ = _validador(persona=_persona(ucp=datetime(2026, 7, 1)))
        d = v.decidir(tipo_acceso="E", tag="EF2A984A", id_acceso=14, cuando=CUANDO)
        self.assertEqual((d.habilitado, d.id_motivo), (False, 309))
        # No socio (Flag_Tipo N): la cuota no se le exige y sigue la cascada.
        v, _ = _validador(persona=_persona(ucp=datetime(2026, 7, 1), tipo=1015), tipo_flag=[("N",)],
                          contrato=[(1,)], contrato_descripcion=[("CONCESIONARIOS SM",)])
        d = v.decidir(tipo_acceso="E", tag="EF2A984A", id_acceso=14, cuando=CUANDO)
        self.assertEqual((d.habilitado, d.id_motivo), (True, 204))
        self.assertEqual(d.mensaje, "Habilit. por Tipo de Contrato CONCESIONARIOS SM")

    def test_categoria_habilitada_205_usa_sf_categ(self):
        # Categoría 1003 (ACTIVO MAYOR, hasta 70 -> 1130) y el acceso habilita 1130.
        v, stub = _validador(persona=_persona(tipo=1003, nac=datetime(1950, 1, 1)),
                             cli_tipos=[(1130, "H")], categ_tipo=[(70, 1130, 0)],
                             tipo_descripcion=[("ACTIVO MAYOR + 71",)])
        d = v.decidir(tipo_acceso="E", tag="EF2A984A", id_acceso=14, cuando=CUANDO)
        self.assertEqual((d.habilitado, d.id_motivo), (True, 205))
        self.assertEqual(d.mensaje, "Habilit. por Tipo de Cliente ACTIVO MAYOR + 71")
        self.assertIn("categ=1130", d.traza)

    def test_producto_206_y_sin_habilitacion_112(self):
        v, stub = _validador(producto_mes=[("CS",)], producto_descripcion=[("CUOTA SOCIAL",)])
        d = v.decidir(tipo_acceso="E", tag="EF2A984A", id_acceso=14, cuando=CUANDO)
        self.assertEqual((d.habilitado, d.id_motivo, d.detalle), (True, 206, "CUOTA SOCIAL"))
        nombres = [n for n, _ in stub.llamadas]
        self.assertLess(nombres.index("producto_sin_periodo"), nombres.index("producto_mes"))
        v, _ = _validador()
        d = v.decidir(tipo_acceso="E", tag="EF2A984A", id_acceso=14, cuando=CUANDO)
        self.assertEqual((d.habilitado, d.id_motivo, d.info), (False, 112, "ADVER"))

    def test_invitado_217_antes_que_los_rechazos(self):
        v, _ = _validador(persona=_persona(activo=0), invitado=[(1,)])
        d = v.decidir(tipo_acceso="E", tag="EF2A984A", id_acceso=14, cuando=CUANDO)
        self.assertEqual((d.habilitado, d.id_motivo), (True, 217))

    def test_error_de_base_deniega(self):
        def explota(sql, params):
            raise RuntimeError("red caída")
        v = vl.ValidadorLocal(ejecutar=explota, config={"HOST": "x", "DATABASE": "x", "USER": "x", "PASSWORD": "x"})
        d = v.decidir(tipo_acceso="E", tag="EF2A984A", id_acceso=14, cuando=CUANDO)
        self.assertFalse(d.habilitado)
        self.assertEqual(d.mensaje, "SIN CONEXION")
        self.assertIn("red caída", d.error)

    def test_acceso_de_evento_no_soportado(self):
        v, _ = _validador(acceso=[(14, "Evento", 1, 1, 0, 0, 0, 0, 0, 0, 0, 5)])
        d = v.decidir(tipo_acceso="E", tag="EF2A984A", id_acceso=14, cuando=CUANDO)
        self.assertFalse(d.habilitado)
        self.assertIn("evento", d.error)

"""Pruebas de la pantalla de Tableros.

Lo que se prueba y lo que no: xSys no está disponible en el entorno de tests, así
que las tres consultas en vivo (padrón, altas/bajas, categorías) se ejercitan con
el cursor mockeado, comprobando que la aritmética de las series sea la correcta.
La deuda sí se prueba de punta a punta, porque lee de Postgres: se arma una foto
a mano y se verifica el agrupado, los filtros y el Excel resultante.

El foco está puesto en las tres cosas que, si se rompen, dan un número creíble
pero falso: la reconstrucción del padrón hacia atrás, la separación entre
"importe del período" y "personas en total", y que el Excel lleve los importes
como número y no como texto.
"""

from __future__ import annotations

import datetime as dt
import io
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from xsys.models import (
    XsysDeudaFoto,
    XsysDeudaMes,
    XsysDeudaSocio,
    XsysDeudaSocioMes,
)
from xsys.services import tableros


# --------------------------------------------------------------------------- #
# Rango
# --------------------------------------------------------------------------- #

class RangoTests(TestCase):

    def test_las_siete_claves_del_selector_existen(self):
        self.assertEqual(
            set(tableros.RANGOS),
            {"hoy", "semana", "mes_actual", "30d", "3m", "6m", "1a"})

    def test_hoy_es_un_solo_dia(self):
        r = tableros.rango("hoy")
        self.assertEqual(r["desde"], r["hasta"])
        self.assertEqual(r["granularidad"], "dia")

    def test_rangos_cortos_se_agrupan_por_dia_y_largos_por_mes(self):
        self.assertEqual(tableros.rango("semana")["granularidad"], "dia")
        self.assertEqual(tableros.rango("30d")["granularidad"], "dia")
        self.assertEqual(tableros.rango("3m")["granularidad"], "mes")
        self.assertEqual(tableros.rango("1a")["granularidad"], "mes")

    def test_mes_actual_arranca_el_primero(self):
        self.assertEqual(tableros.rango("mes_actual")["desde"].day, 1)

    def test_clave_desconocida_cae_en_el_defecto(self):
        self.assertEqual(tableros.rango("cualquiera")["clave"], tableros.RANGO_DEFECTO)

    def test_fechas_explicitas_ganan_sobre_la_clave(self):
        r = tableros.rango("hoy", desde="2026-01-01", hasta="2026-03-31")
        self.assertEqual(r["clave"], "personalizado")
        self.assertEqual(r["desde"], dt.date(2026, 1, 1))
        self.assertEqual(r["hasta"], dt.date(2026, 3, 31))

    def test_fechas_al_reves_se_ordenan_solas(self):
        r = tableros.rango(None, desde="2026-06-01", hasta="2026-02-01")
        self.assertLess(r["desde"], r["hasta"])

    def test_no_se_baja_del_piso_historico(self):
        # Hay altas cargadas con 1900-01-01; sin el piso, el eje arranca ahí.
        r = tableros.rango(None, desde="1900-01-01")
        self.assertEqual(r["desde"], tableros.PISO_HISTORICO)

    def test_periodos_incluye_los_meses_sin_movimiento(self):
        periodos = tableros._periodos(dt.date(2026, 1, 15), dt.date(2026, 4, 2), "mes")
        self.assertEqual([p.strftime("%Y-%m") for p in periodos],
                         ["2026-01", "2026-02", "2026-03", "2026-04"])

    def test_periodos_por_dia_incluye_las_puntas(self):
        periodos = tableros._periodos(dt.date(2026, 3, 1), dt.date(2026, 3, 5), "dia")
        self.assertEqual(len(periodos), 5)
        self.assertEqual(periodos[0], dt.date(2026, 3, 1))
        self.assertEqual(periodos[-1], dt.date(2026, 3, 5))


# --------------------------------------------------------------------------- #
# Padrón: la reconstrucción hacia atrás
# --------------------------------------------------------------------------- #

class _CursorFalso:
    """Devuelve respuestas preparadas en orden. Imita lo justo de pyodbc."""

    def __init__(self, respuestas):
        self._respuestas = list(respuestas)
        self._actual = None

    def execute(self, sql, params=()):
        self._actual = self._respuestas.pop(0)
        return self

    def fetchall(self):
        return self._actual

    def fetchone(self):
        return self._actual[0] if self._actual else None


class _ConexionFalsa:
    timeout = 0

    def __init__(self, respuestas):
        self._cur = _CursorFalso(respuestas)

    def cursor(self):
        return self._cur

    def close(self):
        pass


def _mock_conexion(respuestas):
    return mock.patch.object(tableros, "connect",
                             lambda *a, **k: _ConexionFalsa(respuestas))


class PadronTests(TestCase):
    """El último punto de la serie tiene que ser el padrón de hoy, siempre."""

    def _datos(self, hoy_socios=100, hoy_no_socios=200, movimientos=()):
        return [
            [(1, hoy_socios), (0, hoy_no_socios)],
            list(movimientos),
        ]

    def test_sin_movimientos_la_serie_es_plana_y_termina_en_el_total_de_hoy(self):
        with _mock_conexion(self._datos(100, 200)):
            d = tableros.padron(None, desde="2026-01-01", hasta="2026-03-31")
        self.assertEqual(d["etiquetas"], ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(d["series"]["socios"]["stock"], [100, 100, 100])
        self.assertEqual(d["series"]["no_socios"]["stock"], [200, 200, 200])
        self.assertEqual(d["totales"]["socios"], 100)

    def test_la_serie_se_desanda_hacia_atras_desde_hoy(self):
        # 10 altas y 4 bajas de socios en marzo: en febrero había 100-10+4 = 94.
        movs = [("alta", 1, "2026-03-10", 10), ("baja", 1, "2026-03-20", 4)]
        with _mock_conexion(self._datos(100, 0, movs)):
            d = tableros.padron(None, desde="2026-01-01", hasta="2026-03-31")
        self.assertEqual(d["series"]["socios"]["stock"], [94, 94, 100])
        self.assertEqual(d["series"]["socios"]["altas"], [0, 0, 10])
        self.assertEqual(d["series"]["socios"]["bajas"], [0, 0, 4])
        self.assertEqual(d["series"]["socios"]["neto"], [0, 0, 6])

    def test_los_movimientos_posteriores_al_rango_corrigen_el_ancla(self):
        # El rango cierra en febrero pero en marzo hubo 30 altas: al cierre de
        # febrero había 30 menos que hoy, no los 100 de hoy.
        movs = [("alta", 1, "2026-03-05", 30)]
        with _mock_conexion(self._datos(100, 0, movs)):
            d = tableros.padron(None, desde="2025-12-01", hasta="2026-02-28")
        self.assertEqual(d["series"]["socios"]["stock"], [70, 70, 70])

    def test_socios_y_no_socios_no_se_mezclan(self):
        movs = [("alta", 1, "2026-03-10", 5), ("alta", 0, "2026-03-10", 50)]
        with _mock_conexion(self._datos(100, 200, movs)):
            d = tableros.padron(None, desde="2026-01-01", hasta="2026-03-31")
        self.assertEqual(d["series"]["socios"]["altas"], [0, 0, 5])
        self.assertEqual(d["series"]["no_socios"]["altas"], [0, 0, 50])

    def test_la_conexion_caida_se_reporta_como_error_del_tablero(self):
        def explota(*a, **k):
            raise OSError("VPN caída")
        with mock.patch.object(tableros, "connect", explota):
            with self.assertRaises(tableros.TableroError):
                tableros.padron("1a")


class AltasBajasTests(TestCase):

    def _datos(self, movimientos):
        return [[(1, 100), (0, 200)], list(movimientos)]

    def test_todos_suma_socios_y_no_socios(self):
        movs = [("alta", 1, "2026-03-10", 5), ("alta", 0, "2026-03-10", 50),
                ("baja", 1, "2026-03-11", 2)]
        with _mock_conexion(self._datos(movs)):
            d = tableros.altas_bajas(None, desde="2026-03-01", hasta="2026-03-31")
        self.assertEqual(d["totales"], {"altas": 55, "bajas": 2, "neto": 53})

    def test_el_selector_acota_a_socios(self):
        movs = [("alta", 1, "2026-03-10", 5), ("alta", 0, "2026-03-10", 50)]
        with _mock_conexion(self._datos(movs)):
            d = tableros.altas_bajas(None, desde="2026-03-01", hasta="2026-03-31",
                                     tipo="socios")
        self.assertEqual(d["totales"]["altas"], 5)

    def test_tipo_invalido_cae_en_todos(self):
        with _mock_conexion(self._datos([])):
            d = tableros.altas_bajas(None, desde="2026-03-01", hasta="2026-03-31",
                                     tipo="marcianos")
        self.assertEqual(d["tipo"], "todos")


# --------------------------------------------------------------------------- #
# Categorías: la torta chica
# --------------------------------------------------------------------------- #

class CategoriasTests(TestCase):

    FILAS = [
        (1018, "INVITADOS", 0, 700),
        (1003, "ACTIVO MAYOR", 1, 250),
        (1006, "EMPLEADO", 0, 40),
        (1100, "SOCIO HONORARIO", 1, 6),
        (1113, "VISITA", 0, 4),
    ]

    def test_las_chicas_se_separan_y_aparece_la_porcion_otras(self):
        with _mock_conexion([self.FILAS]):
            d = tableros.categorias(umbral=1.0)
        principales = [c["categoria"] for c in d["principal"]]
        self.assertIn("INVITADOS", principales)
        self.assertIn("EMPLEADO", principales)          # 4 %, queda arriba
        self.assertIn("Otras (2)", principales)
        self.assertEqual({c["categoria"] for c in d["chicas"]},
                         {"SOCIO HONORARIO", "VISITA"})

    def test_la_porcion_otras_vale_lo_mismo_que_la_torta_chica(self):
        with _mock_conexion([self.FILAS]):
            d = tableros.categorias(umbral=1.0)
        otras = [c for c in d["principal"] if c.get("agrupada")][0]
        self.assertEqual(otras["n"], sum(c["n"] for c in d["chicas"]))

    def test_las_dos_tortas_suman_el_total(self):
        with _mock_conexion([self.FILAS]):
            d = tableros.categorias(umbral=1.0)
        sin_agrupada = sum(c["n"] for c in d["principal"] if not c.get("agrupada"))
        self.assertEqual(sin_agrupada + sum(c["n"] for c in d["chicas"]), d["total"])

    def test_sin_categorias_chicas_no_hay_porcion_otras(self):
        with _mock_conexion([[(1018, "INVITADOS", 0, 700), (1003, "ACTIVO MAYOR", 1, 300)]]):
            d = tableros.categorias(umbral=1.0)
        self.assertEqual(d["chicas"], [])
        self.assertFalse(any(c.get("agrupada") for c in d["principal"]))

    def test_filtrar_por_ids_recalcula_los_porcentajes_sobre_lo_elegido(self):
        with _mock_conexion([self.FILAS]):
            d = tableros.categorias(umbral=1.0, ids=[1003, 1006])
        self.assertEqual(d["total"], 290)
        activo = [c for c in d["principal"] if c["categoria"] == "ACTIVO MAYOR"][0]
        self.assertAlmostEqual(activo["porcentaje"], 86.21, places=1)

    def test_solo_socios_deja_afuera_a_los_no_socios(self):
        with _mock_conexion([self.FILAS]):
            d = tableros.categorias(umbral=1.0, solo="socios")
        self.assertEqual(d["total"], 256)

    def test_el_catalogo_viene_completo_aunque_se_filtre(self):
        # El desplegable de la pantalla se arma con esto: tiene que traer todas.
        with _mock_conexion([self.FILAS]):
            d = tableros.categorias(umbral=1.0, ids=[1003])
        self.assertEqual(len(d["catalogo"]), len(self.FILAS))


# --------------------------------------------------------------------------- #
# Deuda: de punta a punta contra Postgres
# --------------------------------------------------------------------------- #

class DeudaBase(TestCase):

    def setUp(self):
        self.foto = XsysDeudaFoto.objects.create(
            calculado_at=timezone.now(), ok=True, duracion_ms=4200,
            socios=3, cupones=9, importe=Decimal("6000"))
        # Dos socios activos y un no socio, en tres meses.
        for mes, categoria, tipo, es_socio, activo, socios, cupones, importe, rubro, act in [
            (dt.date(2026, 1, 1), "ACTIVO MAYOR", 1003, True, True, 2, 2, "1000", "cuota_social", ""),
            (dt.date(2026, 2, 1), "ACTIVO MAYOR", 1003, True, True, 2, 2, "1200", "cuota_social", ""),
            (dt.date(2026, 2, 1), "CADETE", 1001, True, True, 1, 1, "300", "actividades", "VOLEY"),
            (dt.date(2026, 3, 1), "ACTIVO MAYOR", 1003, True, True, 1, 2, "1400", "cuota_social", ""),
            (dt.date(2026, 3, 1), "ACTIVO MAYOR", 1003, True, True, 1, 1, "600", "otros", ""),
            (dt.date(2026, 3, 1), "ACTIVO MAYOR", 1003, True, True, 1, 1, "900", "actividades", "HOCKEY"),
            (dt.date(2026, 3, 1), "INVITADOS", 1018, False, True, 1, 1, "1500", "cuota_social", ""),
        ]:
            XsysDeudaMes.objects.create(
                foto=self.foto, mes=mes, categoria=categoria, id_tipo_cli=tipo,
                es_socio=es_socio, activo=activo, socios=socios, cupones=cupones,
                importe=Decimal(importe), rubro=rubro, actividad=act)

        for cid, ape, tipo, categoria, es_socio, activo, cuotas, importe, cuota, activ in [
            (1, "PEREZ", 1003, "ACTIVO MAYOR", True, True, 5, "3000", "2400", "600"),
            (2, "GOMEZ", 1003, "ACTIVO MAYOR", True, True, 2, "1200", "1200", "0"),
            (3, "LOPEZ", 1001, "CADETE", True, True, 1, "300", "0", "300"),
            (4, "INVITADO", 1018, "INVITADOS", False, True, 1, "1500", "1500", "0"),
            (5, "BAJA", 1003, "ACTIVO MAYOR", True, False, 9, "9000", "9000", "0"),
        ]:
            XsysDeudaSocio.objects.create(
                foto=self.foto, id_cliente=cid, apellido=ape, nombre="X",
                id_tipo_cli=tipo, categoria=categoria, es_socio=es_socio,
                activo=activo, cuotas=cuotas, importe=Decimal(importe),
                importe_cuota_social=Decimal(cuota), importe_actividades=Decimal(activ),
                importe_otros=Decimal(importe) - Decimal(cuota) - Decimal(activ),
                mes_mas_viejo=dt.date(2026, 1, 1), mes_mas_nuevo=dt.date(2026, 3, 1))


class DeudaTests(DeudaBase):

    def test_sin_foto_la_pantalla_lo_dice_en_vez_de_romperse(self):
        XsysDeudaFoto.objects.all().delete()
        d = tableros.deuda("1a")
        self.assertTrue(d["sin_foto"])
        self.assertEqual(d["totales"]["importe"], 0.0)
        self.assertEqual(d["distribucion_cuotas"], [])

    def test_una_foto_fallida_no_tapa_la_ultima_buena(self):
        XsysDeudaFoto.objects.create(calculado_at=timezone.now(), ok=False,
                                     error="se cayó la VPN")
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31")
        self.assertFalse(d["sin_foto"])
        self.assertEqual(d["foto"]["duracion_ms"], 4200)

    def test_agrupa_por_mes_e_incluye_los_meses_vacios(self):
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-04-30")
        self.assertEqual(d["etiquetas"], ["2026-01", "2026-02", "2026-03", "2026-04"])
        # Sólo socios activos: enero 1000, febrero 1200+300, marzo 2000, abril 0.
        self.assertEqual(d["importe"], [1000.0, 1500.0, 2900.0, 0.0])

    def test_el_total_es_la_suma_de_las_barras(self):
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31")
        self.assertEqual(d["totales"]["importe"], sum(d["importe"]))
        self.assertEqual(d["totales"]["cupones"], sum(d["cupones"]))

    def test_el_rango_recorta_las_barras(self):
        d = tableros.deuda(None, desde="2026-02-01", hasta="2026-02-28")
        self.assertEqual(d["etiquetas"], ["2026-02"])
        self.assertEqual(d["totales"]["importe"], 1500.0)

    def test_las_personas_se_cuentan_aparte_y_sin_duplicar(self):
        # Éste es el error que el diseño evita: sumar el 'socios' de cada mes
        # daría 4 (2+2+1... por mes), cuando las personas distintas son 3.
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31")
        self.assertEqual(d["poblacion"]["socios"], 3)

    def test_socios_activos_es_el_recorte_por_defecto(self):
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31")
        self.assertEqual(d["poblacion"]["socios"], 3)          # PEREZ, GOMEZ, LOPEZ
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31", solo="todos")
        self.assertEqual(d["poblacion"]["socios"], 5)

    def test_las_barras_son_siempre_de_socios_activos(self):
        # Regla del club: "la deuda" es la de los socios que están adentro. La
        # población más amplia es una opción del LISTADO, no del gráfico: si
        # moviera las barras, dos personas mirando la misma pantalla con
        # distinto desplegable discutirían números distintos.
        for solo in ("socios_activos", "socios", "activos", "todos"):
            d = tableros.deuda(None, desde="2026-03-01", hasta="2026-03-31", solo=solo)
            self.assertEqual(d["totales"]["importe"], 2900.0, solo)

    def test_pero_el_listado_si_se_amplia(self):
        chico = tableros.deuda(None, desde="2026-03-01", hasta="2026-03-31")
        grande = tableros.deuda(None, desde="2026-03-01", hasta="2026-03-31",
                                solo="todos")
        self.assertEqual(chico["poblacion"]["socios"], 3)
        self.assertEqual(grande["poblacion"]["socios"], 5)

    def test_filtrar_por_categoria_afecta_barras_y_poblacion(self):
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31",
                           categorias_ids=[1001])
        self.assertEqual(d["totales"]["importe"], 300.0)
        self.assertEqual(d["poblacion"]["socios"], 1)

    def test_el_minimo_de_cuotas_toca_la_poblacion_pero_no_las_barras(self):
        base = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31")
        con_min = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31",
                                 cuotas_min=3)
        self.assertEqual(base["importe"], con_min["importe"])
        self.assertEqual(con_min["poblacion"]["socios"], 1)     # sólo PEREZ, 5 cuotas

    def test_la_distribucion_ignora_el_minimo_para_poder_elegirlo(self):
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31", cuotas_min=3)
        self.assertEqual([f["cuotas"] for f in d["distribucion_cuotas"]], [1, 2, 5])

    # ----------------------------------------------------------------- rubros
    def test_devuelve_los_tres_rubros_en_orden_fijo(self):
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31")
        self.assertEqual([r["clave"] for r in d["rubros"]],
                         ["cuota_social", "actividades", "otros"])

    def test_un_rubro_sin_deuda_igual_aparece_en_cero(self):
        # Si desapareciera, el gráfico cambiaría de forma según el mes y la
        # leyenda no se podría comparar entre períodos.
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-01-31")
        claves = [r["clave"] for r in d["rubros"]]
        self.assertEqual(len(claves), 3)
        self.assertEqual([r["total"] for r in d["rubros"]], [1000.0, 0.0, 0.0])

    def test_los_rubros_suman_exactamente_el_total_de_las_barras(self):
        # El control que importa: si esto no cierra, un peso quedó sin clasificar.
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31")
        self.assertAlmostEqual(sum(r["total"] for r in d["rubros"]),
                               d["totales"]["importe"], places=2)

    def test_cada_rubro_tiene_una_serie_del_largo_del_eje(self):
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-04-30")
        for r in d["rubros"]:
            self.assertEqual(len(r["importe"]), len(d["etiquetas"]), r["clave"])

    def test_el_rubro_se_reparte_bien_mes_a_mes(self):
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31")
        cuota = [r for r in d["rubros"] if r["clave"] == "cuota_social"][0]
        activ = [r for r in d["rubros"] if r["clave"] == "actividades"][0]
        otros = [r for r in d["rubros"] if r["clave"] == "otros"][0]
        self.assertEqual(cuota["importe"], [1000.0, 1200.0, 1400.0])
        self.assertEqual(activ["importe"], [0.0, 300.0, 900.0])
        self.assertEqual(otros["importe"], [0.0, 0.0, 600.0])

    def test_el_no_socio_no_ensucia_los_rubros(self):
        # El invitado de marzo debe 1500 de cuota social y no tiene que sumar.
        d = tableros.deuda(None, desde="2026-03-01", hasta="2026-03-31")
        cuota = [r for r in d["rubros"] if r["clave"] == "cuota_social"][0]
        self.assertEqual(cuota["total"], 1400.0)

    def test_sin_foto_los_rubros_vienen_vacios_y_no_rompen(self):
        XsysDeudaFoto.objects.all().delete()
        self.assertEqual(tableros.deuda("1a")["rubros"], [])


    # ---------------------------------------------- desglose de actividades
    def test_el_desglose_suma_exactamente_el_rubro_actividades(self):
        # Si no cierra, hay plata de actividades sin deporte asignado.
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31")
        act = [r for r in d["rubros"] if r["clave"] == "actividades"][0]
        self.assertAlmostEqual(sum(a["importe"] for a in d["actividades"]),
                               act["total"], places=2)

    def test_el_desglose_viene_ordenado_de_mayor_a_menor(self):
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31")
        importes = [a["importe"] for a in d["actividades"]]
        self.assertEqual(importes, sorted(importes, reverse=True))
        self.assertEqual(d["actividades"][0]["actividad"], "HOCKEY")

    def test_cada_actividad_trae_su_serie_mensual(self):
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31")
        voley = [a for a in d["actividades"] if a["actividad"] == "VOLEY"][0]
        self.assertEqual(voley["serie"], [0.0, 300.0, 0.0])

    def test_sin_actividades_el_desglose_viene_vacio(self):
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-01-31")
        self.assertEqual(d["actividades"], [])

    def test_la_cola_larga_se_agrupa_en_otras(self):
        largo = 3
        muchas = {}
        for i in range(20):
            muchas["DEP%02d" % i] = {"actividad": "DEP%02d" % i,
                                     "importe": float(100 - i), "cupones": 1,
                                     "serie": [float(100 - i), 0.0, 0.0]}
        salida = tableros._ranking_actividades(muchas, largo, tope=5)
        self.assertEqual(len(salida), 6)
        agrupada = salida[-1]
        self.assertTrue(agrupada["agrupada"])
        self.assertEqual(agrupada["actividad"], "Otras (15)")
        self.assertEqual(agrupada["cupones"], 15)
        # y la agrupada conserva la serie sumada, para que el apilado cierre
        self.assertEqual(len(agrupada["serie"]), largo)
        self.assertAlmostEqual(sum(a["importe"] for a in salida),
                               sum(a["importe"] for a in muchas.values()), places=2)

    def test_la_actividad_sin_detalle_no_se_pierde(self):
        XsysDeudaMes.objects.create(
            foto=self.foto, mes=dt.date(2026, 2, 1), categoria="ACTIVO MAYOR",
            id_tipo_cli=1003, es_socio=True, activo=True, rubro="actividades",
            actividad="", socios=1, cupones=1, importe=Decimal("50"))
        d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31")
        nombres = [a["actividad"] for a in d["actividades"]]
        self.assertIn("(sin detalle)", nombres)

    def test_la_distribucion_agrupa_la_cola_en_el_tope(self):
        filas = tableros._distribucion_cuotas(self.foto, None, "socios", tope=3)
        ultima = filas[-1]
        self.assertEqual(ultima["cuotas"], 3)
        self.assertTrue(ultima["tope"])
        self.assertEqual(ultima["socios"], 2)                  # el de 5 y el de 9


class DeudaExcelTests(DeudaBase):

    def _abrir(self, **kwargs):
        from openpyxl import load_workbook

        from xsys.services import tableros_excel
        contenido = tableros_excel.generar(self.foto, **kwargs)
        return load_workbook(io.BytesIO(contenido))

    def test_tiene_las_tres_hojas(self):
        wb = self._abrir()
        self.assertEqual(wb.sheetnames, ["Informe", "Detalle", "Por categoría"])

    def test_el_detalle_trae_una_fila_por_persona_mas_encabezado_y_total(self):
        wb = self._abrir()
        # 3 socios activos + encabezado + fila de total.
        self.assertEqual(wb["Detalle"].max_row, 5)

    def test_los_importes_van_como_numero_y_no_como_texto(self):
        # Si van como texto, la primera suma en Excel da cero y el archivo
        # no sirve para nada.
        celda = self._abrir()["Detalle"].cell(row=2, column=10)
        self.assertIsInstance(celda.value, (int, float))
        self.assertNotIsInstance(celda.value, str)
        self.assertIn("#,##0.00", celda.number_format)

    def test_la_fila_de_total_queda_fuera_del_autofiltro(self):
        ws = self._abrir()["Detalle"]
        self.assertEqual(ws.auto_filter.ref, "A1:Q4")
        self.assertEqual(ws.cell(row=5, column=8).value, "TOTAL")

    def test_el_panel_queda_congelado_bajo_el_encabezado(self):
        self.assertEqual(self._abrir()["Detalle"].freeze_panes, "A2")

    def test_el_minimo_de_cuotas_recorta_el_archivo(self):
        wb = self._abrir(cuotas_min=3)
        self.assertEqual(wb["Detalle"].max_row, 3)             # PEREZ + encabezado + total

    def test_la_portada_deja_asentado_de_que_foto_salio(self):
        ws = self._abrir(etiqueta_poblacion="Socios activos",
                         etiqueta_categorias="Todas", usuario="mario")["Informe"]
        etiquetas = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value
                     for r in range(1, 20)}
        self.assertEqual(etiquetas["Generado por"], "mario")
        self.assertEqual(etiquetas["Población incluida"], "Socios activos")
        self.assertEqual(etiquetas["Personas con deuda"], 3)

    def test_el_nombre_del_archivo_lleva_la_fecha_de_la_foto(self):
        from xsys.services import tableros_excel
        esperado = timezone.localtime(self.foto.calculado_at).strftime("%Y-%m-%d_%H%M")
        self.assertEqual(tableros_excel.nombre_archivo(self.foto),
                         f"deuda_socios_{esperado}.xlsx")

    def test_la_hoja_por_categoria_agrupa_bien(self):
        ws = self._abrir()["Por categoría"]
        filas = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value
                 for r in range(2, ws.max_row)}
        self.assertEqual(filas["ACTIVO MAYOR"], 2)
        self.assertEqual(filas["CADETE"], 1)


# --------------------------------------------------------------------------- #
# Permisos y ruteo
# --------------------------------------------------------------------------- #

class PermisosTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.grupo = Group.objects.get_or_create(name="tableros")[0]
        self.con_rol = User.objects.create_user("conrol", password="x")
        self.con_rol.groups.add(self.grupo)
        self.sin_rol = User.objects.create_user("sinrol", password="x")
        self.admin_app = User.objects.create_user("adminapp", password="x")
        self.admin_app.groups.add(Group.objects.get_or_create(name="Administrador")[0])

    def test_sin_login_manda_al_login(self):
        r = self.client.get(reverse("xsys_tableros"))
        self.assertIn(r.status_code, (302, 301))

    def test_con_el_grupo_entra(self):
        self.client.force_login(self.con_rol)
        self.assertEqual(self.client.get(reverse("xsys_tableros")).status_code, 200)

    def test_sin_el_grupo_no_entra(self):
        self.client.force_login(self.sin_rol)
        self.assertEqual(self.client.get(reverse("xsys_tableros")).status_code, 403)

    def test_ser_administrador_de_la_app_no_alcanza(self):
        # Decisión explícita del club, igual que en concesionarios.
        self.client.force_login(self.admin_app)
        self.assertEqual(self.client.get(reverse("xsys_tableros")).status_code, 403)

    def test_el_superusuario_siempre_entra(self):
        User = get_user_model()
        self.client.force_login(User.objects.create_superuser("root", password="x"))
        self.assertEqual(self.client.get(reverse("xsys_tableros")).status_code, 200)

    def test_las_apis_tambien_exigen_el_rol(self):
        self.client.force_login(self.sin_rol)
        for nombre in ("xsys_tablero_padron_api", "xsys_tablero_altas_bajas_api",
                       "xsys_tablero_categorias_api", "xsys_tablero_deuda_api",
                       "xsys_tablero_deuda_excel_api"):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 403, nombre)

    def test_recalcular_no_responde_a_get(self):
        # Escribe y tarda 40 segundos: no puede dispararse desde un enlace ni
        # desde un prefetch del navegador.
        self.client.force_login(self.con_rol)
        r = self.client.get(reverse("xsys_tablero_deuda_recalcular_api"))
        self.assertEqual(r.status_code, 405)


class ApiDeudaTests(DeudaBase):

    def setUp(self):
        super().setUp()
        User = get_user_model()
        u = User.objects.create_user("tablerista", password="x")
        u.groups.add(Group.objects.get_or_create(name="tableros")[0])
        self.client.force_login(u)

    def test_la_api_de_deuda_devuelve_las_barras_y_la_poblacion(self):
        r = self.client.get(reverse("xsys_tablero_deuda_api"),
                            {"desde": "2026-01-01", "hasta": "2026-03-31"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["importe"], [1000.0, 1500.0, 2900.0])
        self.assertEqual(d["poblacion"]["socios"], 3)

    def test_las_categorias_se_aceptan_separadas_por_coma(self):
        r = self.client.get(reverse("xsys_tablero_deuda_api"),
                            {"desde": "2026-01-01", "hasta": "2026-03-31",
                             "categorias": "1001,1003"})
        self.assertEqual(r.json()["poblacion"]["socios"], 3)

    def test_una_categoria_inexistente_no_rompe_y_devuelve_vacio(self):
        r = self.client.get(reverse("xsys_tablero_deuda_api"),
                            {"desde": "2026-01-01", "hasta": "2026-03-31",
                             "categorias": "9999"})
        self.assertEqual(r.json()["totales"]["importe"], 0.0)

    def test_basura_en_los_parametros_no_rompe(self):
        r = self.client.get(reverse("xsys_tablero_deuda_api"),
                            {"categorias": "hola,1003", "cuotas_min": "muchas",
                             "rango": "el_siglo_pasado"})
        self.assertEqual(r.status_code, 200)

    def test_el_excel_se_baja_como_adjunto_xlsx(self):
        r = self.client.get(reverse("xsys_tablero_deuda_excel_api"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml", r["Content-Type"])
        self.assertIn("attachment; filename=\"deuda_socios_", r["Content-Disposition"])
        self.assertGreater(len(r.content), 4000)

    def test_sin_foto_el_excel_avisa_en_vez_de_bajar_un_archivo_vacio(self):
        XsysDeudaFoto.objects.all().delete()
        r = self.client.get(reverse("xsys_tablero_deuda_excel_api"))
        self.assertEqual(r.status_code, 409)
        self.assertIn("Actualizar", r.json()["detail"])


class LimpiezaTests(TestCase):

    def test_se_conservan_las_ultimas_buenas_y_se_borran_las_viejas(self):
        viejas = []
        for i in range(8):
            f = XsysDeudaFoto.objects.create(ok=True)
            XsysDeudaFoto.objects.filter(pk=f.pk).update(
                calculado_at=timezone.now() - dt.timedelta(days=30 - i))
            viejas.append(f.pk)
        tableros._limpiar_fotos_viejas(conservar=5)
        self.assertEqual(XsysDeudaFoto.objects.count(), 5)
        # Las cinco que quedan son las más recientes.
        self.assertEqual(set(XsysDeudaFoto.objects.values_list("pk", flat=True)),
                         set(viejas[-5:]))

    def test_una_fallida_reciente_se_conserva_para_poder_ver_el_error(self):
        XsysDeudaFoto.objects.create(ok=True)
        XsysDeudaFoto.objects.create(ok=False, error="se cayó la VPN")
        tableros._limpiar_fotos_viejas(conservar=5)
        self.assertEqual(XsysDeudaFoto.objects.count(), 2)

    def test_borrar_la_foto_se_lleva_su_detalle(self):
        foto = XsysDeudaFoto.objects.create(ok=True)
        XsysDeudaSocio.objects.create(foto=foto, id_cliente=1, cuotas=1,
                                      importe=Decimal("10"))
        XsysDeudaMes.objects.create(foto=foto, mes=dt.date(2026, 1, 1),
                                    socios=1, cupones=1, importe=Decimal("10"))
        foto.delete()
        self.assertEqual(XsysDeudaSocio.objects.count(), 0)
        self.assertEqual(XsysDeudaMes.objects.count(), 0)


class DetalleActividadTests(DeudaBase):
    """El modal que se abre al tocar un deporte del desglose."""

    def setUp(self):
        super().setUp()
        socios = {s.id_cliente: s for s in XsysDeudaSocio.objects.filter(foto=self.foto)}
        self.hoy = timezone.localdate()

        def hace(meses):
            m = self.hoy.month - meses
            a = self.hoy.year + (m - 1) // 12
            return dt.date(a, (m - 1) % 12 + 1, 1)

        self.hace = hace
        # PEREZ debe hockey de hace 2 años Y del mes pasado: con eso se ve que el
        # recorte por período deja afuera sólo la parte vieja.
        XsysDeudaSocioMes.objects.create(
            socio=socios[1], mes=hace(24), rubro="actividades",
            actividad="HOCKEY", cuotas=4, importe=Decimal("600"))
        XsysDeudaSocioMes.objects.create(
            socio=socios[1], mes=hace(1), rubro="actividades",
            actividad="HOCKEY", cuotas=1, importe=Decimal("150"))
        XsysDeudaSocioMes.objects.create(
            socio=socios[1], mes=hace(2), rubro="cuota_social",
            actividad="", cuotas=3, importe=Decimal("2400"))
        # LOPEZ: vóley del mes pasado.
        XsysDeudaSocioMes.objects.create(
            socio=socios[3], mes=hace(1), rubro="actividades",
            actividad="VOLEY", cuotas=1, importe=Decimal("300"))
        # El de baja también debe: no tiene que aparecer nunca.
        XsysDeudaSocioMes.objects.create(
            socio=socios[5], mes=hace(2), rubro="actividades",
            actividad="HOCKEY", cuotas=9, importe=Decimal("9000"))

    def test_lista_solo_a_los_socios_activos(self):
        d = tableros.detalle_actividad("HOCKEY", desde="2000-01-01")
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["socios"][0]["nombre"], "PEREZ, X")

    def test_muestra_lo_que_debe_de_esa_actividad_y_no_su_deuda_total(self):
        # PEREZ debe 3000 en total pero sólo 750 de hockey (600 + 150).
        d = tableros.detalle_actividad("HOCKEY", desde="2000-01-01")
        self.assertEqual(d["importe"], 750.0)
        self.assertEqual(d["socios"][0]["cuotas"], 5)

    def test_respeta_el_periodo_igual_que_las_barras(self):
        # El punto de todo esto: con el filtro en 3 meses, la deuda de hace dos
        # años no entra. Si entrara, el modal contradiría la barra que se tocó.
        d = tableros.detalle_actividad("HOCKEY", clave_rango="3m")
        self.assertEqual(d["importe"], 150.0)
        self.assertEqual(d["socios"][0]["cuotas"], 1)

    def test_un_periodo_sin_deuda_devuelve_vacio(self):
        d = tableros.detalle_actividad("HOCKEY", clave_rango="hoy")
        self.assertEqual(d["total"], 0)

    def test_el_desde_es_el_mas_viejo_DENTRO_del_periodo(self):
        # Con 3 meses, PEREZ "debe desde" el mes pasado, no desde hace dos años.
        d = tableros.detalle_actividad("HOCKEY", clave_rango="3m")
        self.assertEqual(d["socios"][0]["desde"], self.hace(1).isoformat())

    def test_calcula_la_antiguedad_en_meses(self):
        d = tableros.detalle_actividad("HOCKEY", desde="2000-01-01")
        self.assertGreaterEqual(d["socios"][0]["meses"], 23)
        self.assertEqual(d["socios"][0]["antiguedad"], "Más de 1 año")

    def test_el_reciente_cae_en_el_tramo_corto(self):
        d = tableros.detalle_actividad("VOLEY", desde="2000-01-01")
        self.assertEqual(d["socios"][0]["antiguedad"], "1 a 3 meses")

    def test_el_resumen_de_antiguedad_cierra_con_el_total(self):
        d = tableros.detalle_actividad("HOCKEY", desde="2000-01-01")
        self.assertEqual(sum(t["socios"] for t in d["antiguedad"]), d["total"])
        self.assertAlmostEqual(sum(t["importe"] for t in d["antiguedad"]),
                               d["importe"], places=2)

    def test_una_actividad_inexistente_devuelve_vacio_sin_romper(self):
        d = tableros.detalle_actividad("CURLING", desde="2000-01-01")
        self.assertEqual(d["total"], 0)
        self.assertEqual(d["socios"], [])
        self.assertEqual(d["antiguedad"], [])

    def test_por_rubro_filtra_por_rubro(self):
        d = tableros.detalle_actividad(rubro="cuota_social", desde="2000-01-01")
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["importe"], 2400.0)

    def test_por_rubro_actividades_no_cuenta_a_quien_no_debe_actividades(self):
        d = tableros.detalle_actividad(rubro="actividades", desde="2000-01-01")
        nombres = [s["nombre"] for s in d["socios"]]
        self.assertIn("LOPEZ, X", nombres)
        self.assertNotIn("GOMEZ, X", nombres)

    def test_el_filtro_de_categoria_tambien_aplica(self):
        d = tableros.detalle_actividad("HOCKEY", categorias_ids=[1001],
                                       desde="2000-01-01")
        self.assertEqual(d["total"], 0)

    def test_se_puede_ordenar_por_cada_campo(self):
        for campo in tableros.ORDENES_DETALLE:
            d = tableros.detalle_actividad(rubro="actividades", orden=campo,
                                           desde="2000-01-01")
            self.assertEqual(d["orden"], campo)
            self.assertEqual(d["total"], 2)

    def test_un_orden_desconocido_cae_en_el_defecto(self):
        d = tableros.detalle_actividad(rubro="actividades", orden="por_simpatia",
                                       desde="2000-01-01")
        self.assertEqual(d["orden"], tableros.ORDEN_DETALLE_DEFECTO)

    def test_el_orden_ascendente_invierte_de_verdad(self):
        asc = tableros.detalle_actividad(rubro="actividades", orden="importe",
                                         desc=False, desde="2000-01-01")
        des = tableros.detalle_actividad(rubro="actividades", orden="importe",
                                         desc=True, desde="2000-01-01")
        self.assertEqual([x["nombre"] for x in asc["socios"]],
                         list(reversed([x["nombre"] for x in des["socios"]])))

    def test_se_ordena_el_conjunto_entero_y_no_la_pagina(self):
        # Con limit=1 y orden ascendente sale el MENOR de todos, no el menor de
        # la primera página. Es la razón de ordenar en el servidor.
        d = tableros.detalle_actividad(rubro="actividades", orden="importe",
                                       desc=False, limit=1, desde="2000-01-01")
        self.assertEqual(d["socios"][0]["nombre"], "LOPEZ, X")   # 300 < 750

    def test_pagina_pero_los_totales_son_del_conjunto(self):
        d = tableros.detalle_actividad(rubro="actividades", limit=1,
                                       desde="2000-01-01")
        self.assertEqual(len(d["socios"]), 1)
        self.assertEqual(d["total"], 2)          # el total NO es el de la página
        self.assertEqual(d["importe"], 1050.0)

    def test_sin_foto_avisa_en_vez_de_romper(self):
        XsysDeudaFoto.objects.all().delete()
        d = tableros.detalle_actividad("HOCKEY", desde="2000-01-01")
        self.assertTrue(d["sin_foto"])
        self.assertEqual(d["socios"], [])

    def test_borrar_la_foto_se_lleva_el_detalle_fino(self):
        self.foto.delete()
        self.assertEqual(XsysDeudaSocioMes.objects.count(), 0)

    def test_el_excel_del_modal_se_baja_como_adjunto(self):
        User = get_user_model()
        u = User.objects.create_user("bajamodal", password="x")
        u.groups.add(Group.objects.get_or_create(name="tableros")[0])
        self.client.force_login(u)
        r = self.client.get(reverse("xsys_tablero_deuda_detalle_excel_api"),
                            {"actividad": "HOCKEY", "desde": "2000-01-01"},
                            HTTP_HOST="localhost")
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml", r["Content-Type"])
        self.assertIn("deuda_hockey_", r["Content-Disposition"])
        self.assertEqual(r.content[:2], b"PK")

    def test_el_excel_del_modal_baja_todo_y_no_solo_la_pagina(self):
        import io as _io

        from openpyxl import load_workbook
        User = get_user_model()
        u = User.objects.create_user("bajatodo", password="x")
        u.groups.add(Group.objects.get_or_create(name="tableros")[0])
        self.client.force_login(u)
        r = self.client.get(reverse("xsys_tablero_deuda_detalle_excel_api"),
                            {"rubro": "actividades", "desde": "2000-01-01"},
                            HTTP_HOST="localhost")
        wb = load_workbook(_io.BytesIO(r.content))
        # 2 socios + encabezado + fila de total
        self.assertEqual(wb["Detalle"].max_row, 4)

    def test_el_excel_del_modal_avisa_si_no_hay_nada(self):
        User = get_user_model()
        u = User.objects.create_user("bajavacio", password="x")
        u.groups.add(Group.objects.get_or_create(name="tableros")[0])
        self.client.force_login(u)
        r = self.client.get(reverse("xsys_tablero_deuda_detalle_excel_api"),
                            {"actividad": "CURLING"}, HTTP_HOST="localhost")
        self.assertEqual(r.status_code, 409)

    def test_la_api_responde_y_exige_el_rol(self):
        User = get_user_model()
        pelado = User.objects.create_user("mirón", password="x")
        self.client.force_login(pelado)
        self.assertEqual(self.client.get(
            reverse("xsys_tablero_deuda_detalle_api")).status_code, 403)

        u = User.objects.create_user("tablerista2", password="x")
        u.groups.add(Group.objects.get_or_create(name="tableros")[0])
        self.client.force_login(u)
        r = self.client.get(reverse("xsys_tablero_deuda_detalle_api"),
                            {"actividad": "HOCKEY", "desde": "2000-01-01"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["total"], 1)


class FrescuraTests(DeudaBase):
    """La pantalla tiene que decir de cuándo es cada número."""

    def setUp(self):
        super().setUp()
        User = get_user_model()
        u = User.objects.create_user("mirador", password="x")
        u.groups.add(Group.objects.get_or_create(name="tableros")[0])
        self.client.force_login(u)

    def _html(self):
        return self.client.get(reverse("xsys_tableros"), HTTP_HOST="localhost"
                               ).content.decode()

    def test_muestra_las_dos_frescuras_por_separado(self):
        # Son distintas: el padrón se consulta en vivo y la deuda sale de una
        # foto que puede ser de anoche. Mezclarlas haría creer que todo el
        # tablero es igual de reciente.
        html = self._html()
        self.assertIn('id="frescura-vivo"', html)
        self.assertIn('id="frescura-deuda"', html)

    def test_la_fecha_de_la_foto_viene_ya_renderizada(self):
        # Sin esperar al JS: al abrir la pantalla ya se ve de cuándo es la deuda.
        html = self._html()
        esperado = timezone.localtime(self.foto.calculado_at).strftime("%d/%m/%Y %H:%M")
        self.assertIn(esperado, html)

    def test_sin_foto_lo_dice_en_vez_de_quedar_vacio(self):
        XsysDeudaFoto.objects.all().delete()
        self.assertIn("sin calcular", self._html())

    def test_hay_boton_de_refrescar(self):
        self.assertIn('id="btn-refrescar"', self._html())

    def test_refrescar_no_recalcula_la_deuda(self):
        # Refrescar vuelve a consultar lo barato; recalcular la foto es el otro
        # botón, que tarda medio minuto y pega contra producción.
        html = self._html()
        self.assertIn('id="d-actualizar"', html)
        self.assertIn("Actualizar datos", html)


class ModalDetalleTests(DeudaBase):
    """El modal no puede dejar la pantalla sin responder al cerrarse.

    El bug: con ``aria-hidden="true"`` escrito a mano en el HTML, el navegador
    lo encuentra puesto sobre el elemento que todavía tiene el foco al cerrar,
    lo bloquea, y el cierre puede quedar a medias dejando el ``.modal-backdrop``
    en el DOM. Ese backdrop es una capa con z-index 1050 sobre TODA la pantalla:
    a partir de ahí ningún clic llega a ningún filtro ni selector, y la
    aplicación parece colgada.
    """

    def setUp(self):
        super().setUp()
        User = get_user_model()
        u = User.objects.create_user("modalero", password="x")
        u.groups.add(Group.objects.get_or_create(name="tableros")[0])
        self.client.force_login(u)
        self.html = self.client.get(reverse("xsys_tableros"),
                                    HTTP_HOST="localhost").content.decode()

    def test_el_modal_no_lleva_aria_hidden_escrito_a_mano(self):
        # Lo pone y lo saca Bootstrap; fijarlo en el HTML es lo que rompía.
        self.assertIn('id="modal-detalle"', self.html)
        i = self.html.index('id="modal-detalle"')
        etiqueta = self.html[i - 60:i + 120]
        self.assertNotIn("aria-hidden", etiqueta)

    def test_saca_el_foco_del_modal_antes_de_ocultarlo(self):
        self.assertIn("hide.bs.modal", self.html)
        self.assertIn("document.activeElement.blur()", self.html)

    def test_limpia_el_backdrop_al_terminar_de_cerrar(self):
        # La red de seguridad: si el cierre no limpió, se limpia igual.
        self.assertIn("hidden.bs.modal", self.html)
        self.assertIn(".modal-backdrop", self.html)
        self.assertIn('classList.remove("modal-open")', self.html)

    def test_las_filas_que_abren_el_modal_se_pueden_usar_sin_mouse(self):
        self.assertIn('tabindex="0" role="button"', self.html)
        self.assertIn("keydown", self.html)


class RecalculoEnSegundoPlanoTests(TestCase):
    """El botón dispara y vuelve; no espera los cuatro minutos del cálculo."""

    def setUp(self):
        User = get_user_model()
        u = User.objects.create_user("recalculador", password="x")
        u.groups.add(Group.objects.get_or_create(name="tableros")[0])
        self.client.force_login(u)

    def test_sin_nada_corriendo_el_estado_lo_dice(self):
        e = tableros.estado_calculo()
        self.assertFalse(e["en_curso"])
        self.assertIsNone(e["ultima"])

    def test_una_foto_recien_creada_cuenta_como_en_curso(self):
        # Así se detecta: arranca con ok=False y sin error, y termina siendo una
        # de las dos cosas. No hace falta un campo de estado.
        XsysDeudaFoto.objects.create(ok=False, error="")
        self.assertTrue(tableros.estado_calculo()["en_curso"])

    def test_una_foto_terminada_bien_no_cuenta_como_en_curso(self):
        XsysDeudaFoto.objects.create(ok=True)
        self.assertFalse(tableros.estado_calculo()["en_curso"])

    def test_una_foto_fallida_no_cuenta_como_en_curso_y_reporta_el_error(self):
        XsysDeudaFoto.objects.create(ok=False, error="se cayó la VPN")
        e = tableros.estado_calculo()
        self.assertFalse(e["en_curso"])
        self.assertEqual(e["error"], "se cayó la VPN")

    def test_un_calculo_colgado_deja_de_bloquear_pasado_el_plazo(self):
        # Si el worker que lo lanzó se murió, la foto queda en ok=False para
        # siempre. Sin este corte, el botón no se podría usar nunca más.
        vieja = XsysDeudaFoto.objects.create(ok=False, error="")
        XsysDeudaFoto.objects.filter(pk=vieja.pk).update(
            calculado_at=timezone.now() - dt.timedelta(
                minutes=tableros.MINUTOS_CALCULO_MUERTO + 1))
        self.assertFalse(tableros.estado_calculo()["en_curso"])

    def test_el_estimado_sale_de_corridas_reales(self):
        XsysDeudaFoto.objects.create(ok=True, duracion_ms=222_000)
        self.assertEqual(tableros.estado_calculo()["estimado_s"], 222)

    def test_el_estimado_toma_la_corrida_mas_lenta_y_no_la_ultima(self):
        # Entre dos corridas del mismo día hubo 73 s y 229 s. Prometer 73 y
        # tardar 229 es lo que hace que la gente apriete el botón dos veces.
        XsysDeudaFoto.objects.create(ok=True, duracion_ms=229_000)
        XsysDeudaFoto.objects.create(ok=True, duracion_ms=73_000)
        self.assertEqual(tableros.estado_calculo()["estimado_s"], 229)

    def test_las_fallidas_no_ensucian_el_estimado(self):
        XsysDeudaFoto.objects.create(ok=True, duracion_ms=100_000)
        XsysDeudaFoto.objects.create(ok=False, error="x", duracion_ms=900_000)
        self.assertEqual(tableros.estado_calculo()["estimado_s"], 100)

    def test_sin_corridas_previas_usa_el_estimado_por_defecto(self):
        self.assertEqual(tableros.estado_calculo()["estimado_s"],
                         tableros.SEGUNDOS_ESTIMADOS)

    def test_no_se_puede_arrancar_dos_veces_a_la_vez(self):
        XsysDeudaFoto.objects.create(ok=False, error="")
        r = tableros.lanzar_recalculo()
        self.assertFalse(r["arrancado"])
        self.assertEqual(r["motivo"], "ya_en_curso")

    def test_la_api_contesta_enseguida_y_no_espera_el_calculo(self):
        # Si esperara, la petición moriría por timeout: el cálculo real dura
        # unos cuatro minutos.
        with mock.patch.object(tableros, "recalcular_deuda") as falso:
            r = self.client.post(reverse("xsys_tablero_deuda_recalcular_api"),
                                 HTTP_HOST="localhost")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["arrancado"])
        self.assertIn("estimado_s", r.json())

    def test_la_api_devuelve_409_si_ya_hay_uno_corriendo(self):
        XsysDeudaFoto.objects.create(ok=False, error="")
        r = self.client.post(reverse("xsys_tablero_deuda_recalcular_api"),
                             HTTP_HOST="localhost")
        self.assertEqual(r.status_code, 409)
        self.assertFalse(r.json()["arrancado"])

    def test_la_api_de_estado_responde_y_exige_el_rol(self):
        r = self.client.get(reverse("xsys_tablero_deuda_estado_api"),
                            HTTP_HOST="localhost")
        self.assertEqual(r.status_code, 200)
        self.assertIn("en_curso", r.json())

        User = get_user_model()
        self.client.force_login(User.objects.create_user("colado", password="x"))
        self.assertEqual(self.client.get(
            reverse("xsys_tablero_deuda_estado_api"),
            HTTP_HOST="localhost").status_code, 403)

    def test_la_pantalla_avisa_que_corre_en_segundo_plano(self):
        html = self.client.get(reverse("xsys_tableros"),
                               HTTP_HOST="localhost").content.decode()
        self.assertIn("segundo plano", html)
        self.assertIn('id="d-progreso"', html)
        self.assertIn("/api/xsys/tableros/deuda/estado/", html)


class HorarioProgramadoTests(TestCase):
    """Las horas pedidas por el club: 10, y cada 3 horas hasta las 19."""

    def setUp(self):
        from xsys.management.commands import xsys_deuda_foto as cmd
        self.cmd = cmd
        self.horas = cmd._parsear_horas(cmd.HORAS_DEFECTO)

    def test_el_default_son_las_cuatro_horas_pedidas(self):
        self.assertEqual(self.horas, [10, 13, 16, 19])

    def test_antes_de_las_diez_la_proxima_es_a_las_diez(self):
        ahora = dt.datetime(2026, 9, 8, 7, 30)
        self.assertEqual(self.cmd.proxima_corrida(self.horas, ahora),
                         dt.datetime(2026, 9, 8, 10, 0))

    def test_en_el_medio_toma_la_siguiente(self):
        ahora = dt.datetime(2026, 9, 8, 13, 5)
        self.assertEqual(self.cmd.proxima_corrida(self.horas, ahora),
                         dt.datetime(2026, 9, 8, 16, 0))

    def test_justo_a_la_hora_no_se_repite_sino_que_pasa_a_la_siguiente(self):
        # Si devolviera la misma hora, el proceso correría en bucle todo el
        # minuto de las 13:00.
        ahora = dt.datetime(2026, 9, 8, 13, 0, 0)
        self.assertEqual(self.cmd.proxima_corrida(self.horas, ahora),
                         dt.datetime(2026, 9, 8, 16, 0))

    def test_despues_de_la_ultima_pasa_a_las_diez_del_dia_siguiente(self):
        ahora = dt.datetime(2026, 9, 8, 21, 40)
        self.assertEqual(self.cmd.proxima_corrida(self.horas, ahora),
                         dt.datetime(2026, 9, 9, 10, 0))

    def test_cruza_bien_el_fin_de_mes(self):
        ahora = dt.datetime(2026, 9, 30, 23, 59)
        self.assertEqual(self.cmd.proxima_corrida(self.horas, ahora),
                         dt.datetime(2026, 10, 1, 10, 0))

    def test_las_horas_se_ordenan_y_se_deduplican(self):
        self.assertEqual(self.cmd._parsear_horas("19, 10,13, 10 ,16"),
                         [10, 13, 16, 19])

    def test_una_hora_invalida_se_rechaza(self):
        from django.core.management.base import CommandError
        for crudo in ("25", "-1", "diez", ""):
            with self.assertRaises(CommandError):
                self.cmd._parsear_horas(crudo)

    def test_el_compose_lanza_el_comando_con_esas_horas(self):
        # Que el horario esté bien calculado no sirve si nadie lo dispara.
        with open("docker-compose.yml", encoding="utf-8") as fh:
            compose = fh.read()
        self.assertIn("deuda-foto:", compose)
        self.assertIn("xsys_deuda_foto", compose)
        self.assertIn("10,13,16,19", compose)


class MensajeSinConexionTests(TestCase):
    """Lo que ve el usuario cuando xSys no responde.

    Pasó de verdad el 09/09/2026: se cayó la ruta al servidor 192.168.0.6 y el
    tablero mostraba «No se pudo conectar a xSys: No se pudo conectar a xSys:
    ('HYT00', '[Microsoft][ODBC Driver 18...] Login timeout expired')». Prefijo
    repetido y un código de ODBC que no le dice nada a quien mira el tablero.
    """

    def _falla(self, mensaje):
        def explota(*a, **k):
            raise OSError(mensaje)
        return mock.patch.object(tableros, "connect", explota)

    def test_no_repite_el_prefijo(self):
        with self._falla("No se pudo conectar a xSys: ('HYT00', 'Login timeout expired')"):
            with self.assertRaises(tableros.TableroError) as ctx:
                tableros.padron("1a")
        self.assertEqual(str(ctx.exception).count("xSys:"), 1)

    def test_dice_que_servidor_no_responde_y_no_el_codigo_de_odbc(self):
        with self._falla("('HYT00', '[Microsoft][ODBC Driver 18] Login timeout expired')"):
            with self.assertRaises(tableros.TableroError) as ctx:
                tableros.padron("1a")
        texto = str(ctx.exception)
        self.assertIn("no responde", texto)
        self.assertNotIn("HYT00", texto)

    def test_distingue_un_rechazo_de_credenciales(self):
        with self._falla("('28000', 'Login failed for user geba_acs')"):
            with self.assertRaises(tableros.TableroError) as ctx:
                tableros.padron("1a")
        self.assertIn("rechazó el usuario", str(ctx.exception))

    def test_aclara_que_la_deuda_se_sigue_viendo(self):
        # Es el dato útil: la mitad del tablero sigue sirviendo.
        with self._falla("Login timeout expired"):
            with self.assertRaises(tableros.TableroError) as ctx:
                tableros.categorias()
        self.assertIn("foto guardada", str(ctx.exception))

    def test_la_api_contesta_503_y_no_un_500(self):
        User = get_user_model()
        u = User.objects.create_user("sinred", password="x")
        u.groups.add(Group.objects.get_or_create(name="tableros")[0])
        self.client.force_login(u)
        with self._falla("Login timeout expired"):
            r = self.client.get(reverse("xsys_tablero_padron_api"),
                                HTTP_HOST="localhost")
        self.assertEqual(r.status_code, 503)
        self.assertIn("no responde", r.json()["detail"])


class DeudaSobreviveSinXsysTests(DeudaBase):
    """Con xSys caído, la deuda y su detalle tienen que seguir andando."""

    def setUp(self):
        super().setUp()
        socios = {x.id_cliente: x for x in XsysDeudaSocio.objects.filter(foto=self.foto)}
        for cid, act, imp in ((1, "HOCKEY", "600"), (3, "VOLEY", "300")):
            XsysDeudaSocioMes.objects.create(
                socio=socios[cid], mes=dt.date(2026, 2, 1), rubro="actividades",
                actividad=act, cuotas=1, importe=Decimal(imp))
        User = get_user_model()
        u = User.objects.create_user("aguante", password="x")
        u.groups.add(Group.objects.get_or_create(name="tableros")[0])
        self.client.force_login(u)

    def test_el_grafico_de_deuda_no_toca_xsys(self):
        def explota(*a, **k):
            raise OSError("Login timeout expired")
        with mock.patch.object(tableros, "connect", explota):
            d = tableros.deuda(None, desde="2026-01-01", hasta="2026-03-31")
        self.assertFalse(d["sin_foto"])
        self.assertGreater(d["totales"]["importe"], 0)

    def test_el_modal_tampoco(self):
        def explota(*a, **k):
            raise OSError("Login timeout expired")
        with mock.patch.object(tableros, "connect", explota):
            d = tableros.detalle_actividad(rubro="actividades", desde="2000-01-01")
        self.assertEqual(d["total"], 2)

    def test_la_api_de_deuda_sigue_dando_200(self):
        def explota(*a, **k):
            raise OSError("Login timeout expired")
        with mock.patch.object(tableros, "connect", explota):
            r = self.client.get(reverse("xsys_tablero_deuda_api"),
                                {"desde": "2026-01-01", "hasta": "2026-03-31"},
                                HTTP_HOST="localhost")
        self.assertEqual(r.status_code, 200)

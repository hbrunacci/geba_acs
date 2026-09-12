"""Pruebas del proceso que libera a los bloqueados por deuda de actividades.

Lo que decide este proceso es si alguien entra o no entra al club, así que las
pruebas se concentran en las tres formas de equivocarse que importan:

1. Liberar a quien todavía debe.
2. Dejar bloqueado a quien ya pagó (el bug que este proceso vino a arreglar).
3. Escribir algo cuando nadie lo pidió: el modo por defecto NO aplica.

xSys no está disponible en los tests, así que la conexión va mockeada. Lo que se
prueba es la decisión y el SQL que se manda, no el motor de SQL Server.
"""

from __future__ import annotations

import datetime as dt
from unittest import mock

from django.test import TestCase

from xsys.services import deuda_actividades as da


class _Cursor:
    """Cursor de mentira: responde por orden y guarda los UPDATE que recibe."""

    def __init__(self, bloqueados, deuda_viva, nombres=None):
        self._bloqueados = bloqueados
        self._deuda = deuda_viva
        self._nombres = nombres or {}
        self._actual = []
        self.updates = []
        self.rowcount = 1

    def execute(self, sql, params=()):
        limpio = " ".join(sql.split())
        if limpio.startswith("SELECT Id_Cliente, Cuotas, Bloquea"):
            self._actual = self._bloqueados
        elif "FROM Clientes_CtaCte" in limpio:
            self._actual = self._deuda
        elif "FROM Clientes WHERE Id_Cliente" in limpio:
            self._actual = [(k, v) for k, v in self._nombres.items()]
        elif limpio.startswith("UPDATE"):
            self.updates.append((limpio, params))
            self._actual = []
        else:
            self._actual = []
        return self

    def fetchall(self):
        return self._actual


class _Conn:
    autocommit = True
    timeout = 0

    def __init__(self, cur):
        self._cur = cur
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _mock(bloqueados, deuda_viva, nombres=None):
    cur = _Cursor(bloqueados, deuda_viva, nombres)
    conn = _Conn(cur)
    return cur, conn, mock.patch.object(da, "connect", lambda *a, **k: conn)


# --------------------------------------------------------------------------- #
# La decisión
# --------------------------------------------------------------------------- #

class DecisionTests(TestCase):

    def test_sin_deuda_se_regulariza(self):
        self.assertEqual(da._decidir(True, 0), da.ESTADO_REGULARIZADO)

    def test_sin_deuda_tambien_sale_el_que_solo_estaba_avisado(self):
        self.assertEqual(da._decidir(False, 0), da.ESTADO_REGULARIZADO)

    def test_pago_una_parte_y_baja_de_bloqueo_a_aviso(self):
        # Pagó de 4 a 3: bajo el umbral del club, pasa pero queda marcado.
        self.assertEqual(da._decidir(True, 3), da.ESTADO_A_AVISO)
        self.assertEqual(da._decidir(True, 1), da.ESTADO_A_AVISO)

    def test_con_el_umbral_completo_sigue_bloqueado(self):
        self.assertEqual(da._decidir(True, da.UMBRAL_BLOQUEO), da.ESTADO_SIGUE)
        self.assertEqual(da._decidir(True, 9), da.ESTADO_SIGUE)

    def test_al_que_solo_avisaba_no_se_lo_bloquea_aunque_deba_mas(self):
        # Éste es el límite que el proceso no cruza: frenar a alguien es una
        # decisión del club, no del automatismo.
        self.assertEqual(da._decidir(False, 12), da.ESTADO_SIN_CAMBIO)

    def test_el_umbral_es_el_criterio_que_ya_usaba_el_club(self):
        self.assertEqual(da.UMBRAL_BLOQUEO, 4)


# --------------------------------------------------------------------------- #
# El recorte de lo vencido
# --------------------------------------------------------------------------- #

class VencidoTests(TestCase):

    def test_el_corte_es_el_primero_del_mes_que_viene(self):
        # El club factura por adelantado: el cupón del mes que viene ya existe
        # y figura impago. Bloquear por eso sería frenar a alguien por una
        # cuota que todavía no venció.
        self.assertEqual(da._corte_vencido(dt.date(2026, 9, 9)), dt.date(2026, 10, 1))

    def test_diciembre_pasa_a_enero_del_ano_siguiente(self):
        self.assertEqual(da._corte_vencido(dt.date(2026, 12, 31)), dt.date(2027, 1, 1))

    def test_la_consulta_filtra_por_fecha_y_por_tipo_de_contrato(self):
        sql = da._sql_cuotas_vencidas(3)
        self.assertIn("CC.Fecha < ?", sql)
        self.assertIn("Saldo > 0", sql)
        # El mismo mapeo de actividades que el tablero, no una copia propia.
        for tipo in (9, 12, 25):
            self.assertIn(str(tipo), sql)
        self.assertEqual(sql.count("?"), 4)      # la fecha + los 3 ids


# --------------------------------------------------------------------------- #
# El recorrido completo
# --------------------------------------------------------------------------- #

class RevisarTests(TestCase):

    BLOQUEADOS = [
        (100, 4, 1, "4 cuota(s) al 25/08"),   # pagó todo      -> libera
        (200, 4, 1, ""),                      # pagó parte     -> aviso
        (300, 4, 1, ""),                      # sigue debiendo -> nada
        (400, 2, 0, ""),                      # avisado, pagó  -> libera
    ]
    DEUDA = [(200, 2, 50000.0, None), (300, 5, 90000.0, None)]
    NOMBRES = {100: "PEREZ, ANA", 200: "GOMEZ, LUIS", 300: "DIAZ, SOL",
               400: "RUIZ, LEO"}

    def test_clasifica_cada_caso(self):
        cur, conn, patch = _mock(self.BLOQUEADOS, self.DEUDA, self.NOMBRES)
        with patch:
            inf = da.revisar(aplicar=False)
        self.assertEqual(inf["revisados"], 4)
        self.assertEqual([c["id_cliente"] for c in inf["regularizados"]], [100, 400])
        self.assertEqual([c["id_cliente"] for c in inf["bajados_a_aviso"]], [200])
        self.assertEqual(inf["siguen_bloqueados"], 1)

    def test_por_defecto_no_escribe_nada(self):
        # Lo que está en juego es dejar entrar gente: el default tiene que ser
        # mirar, no hacer.
        cur, conn, patch = _mock(self.BLOQUEADOS, self.DEUDA, self.NOMBRES)
        with patch:
            inf = da.revisar()
        self.assertEqual(cur.updates, [])
        self.assertEqual(inf["aplicados"], 0)
        self.assertFalse(inf["aplicar"])

    def test_con_aplicar_escribe_solo_a_los_que_corresponde(self):
        cur, conn, patch = _mock(self.BLOQUEADOS, self.DEUDA, self.NOMBRES)
        with patch:
            da.revisar(aplicar=True)
        self.assertEqual(len(cur.updates), 3)      # 2 liberados + 1 a aviso
        tocados = [u[1][-1] for u in cur.updates]
        self.assertEqual(sorted(tocados), [100, 200, 400])
        self.assertNotIn(300, tocados)             # el que sigue debiendo, no

    def test_al_liberar_pone_activo_cero_y_fecha_de_baja(self):
        cur, conn, patch = _mock(self.BLOQUEADOS, self.DEUDA, self.NOMBRES)
        with patch:
            da.revisar(aplicar=True)
        sql = [u[0] for u in cur.updates if u[1][-1] == 100][0]
        self.assertIn("SET Activo = 0", sql)
        self.assertIn("Fecha_Baja = GETDATE()", sql)

    def test_al_bajar_a_aviso_no_lo_da_de_baja(self):
        # Sigue debiendo 2: tiene que seguir en la tabla para que el visor lo
        # marque en amarillo, sólo deja de frenarlo.
        cur, conn, patch = _mock(self.BLOQUEADOS, self.DEUDA, self.NOMBRES)
        with patch:
            da.revisar(aplicar=True)
        sql = [u[0] for u in cur.updates if u[1][-1] == 200][0]
        self.assertIn("SET Bloquea = 0", sql)
        self.assertNotIn("Activo = 0", sql)

    def test_cada_update_lleva_su_guarda_en_el_where(self):
        # Para que dos corridas simultáneas, o una corrida y una edición a mano,
        # no se pisen.
        cur, conn, patch = _mock(self.BLOQUEADOS, self.DEUDA, self.NOMBRES)
        with patch:
            da.revisar(aplicar=True)
        for sql, _p in cur.updates:
            self.assertIn("ISNULL(Activo, 1) = 1", sql)

    def test_confirma_la_transaccion_una_sola_vez(self):
        cur, conn, patch = _mock(self.BLOQUEADOS, self.DEUDA, self.NOMBRES)
        with patch:
            da.revisar(aplicar=True)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)

    def test_si_un_update_falla_no_queda_nada_a_medias(self):
        cur, conn, patch = _mock(self.BLOQUEADOS, self.DEUDA, self.NOMBRES)
        original = cur.execute

        def explota(sql, params=()):
            if " ".join(sql.split()).startswith("UPDATE"):
                raise RuntimeError("se cayó la red")
            return original(sql, params)

        with patch:
            with mock.patch.object(cur, "execute", explota):
                with self.assertRaises(da.RevisionError):
                    da.revisar(aplicar=True)
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(conn.commits, 0)

    def test_sin_bloqueados_no_hace_nada_y_no_rompe(self):
        cur, conn, patch = _mock([], [], {})
        with patch:
            inf = da.revisar(aplicar=True)
        self.assertEqual(inf["revisados"], 0)
        self.assertEqual(cur.updates, [])

    def test_la_conexion_caida_se_reporta_como_error_propio(self):
        def explota(*a, **k):
            raise OSError("Login timeout expired")
        with mock.patch.object(da, "connect", explota):
            with self.assertRaises(da.RevisionError):
                da.revisar()


class ObservacionTests(TestCase):

    def test_conserva_lo_que_ya_habia(self):
        salida = da._nota("4 cuota(s) al 25/08/2026", "auto: regularizado 09/09/2026")
        self.assertIn("4 cuota(s)", salida)
        self.assertIn("regularizado", salida)

    def test_no_encadena_marcas_automaticas(self):
        # La segunda corrida reemplaza a la primera en vez de apilarse hasta
        # desbordar el campo.
        primera = da._nota("", "auto: baja a aviso 09/09/2026")
        segunda = da._nota(primera, "auto: regularizado 10/09/2026")
        self.assertEqual(segunda.count("auto:"), 1)
        self.assertIn("regularizado", segunda)

    def test_nunca_desborda_el_campo_de_la_base(self):
        # Observacion es varchar(200) en xSys.
        salida = da._nota("x" * 190, "auto: " + "y" * 120)
        self.assertLessEqual(len(salida), 200)


class ComposeTests(TestCase):
    """Que el proceso exista no sirve si nadie lo dispara."""

    def test_hay_un_servicio_que_lo_corre_periodicamente(self):
        with open("docker-compose.yml", encoding="utf-8") as fh:
            compose = fh.read()
        self.assertIn("deuda-actividades:", compose)
        self.assertIn("xsys_deuda_actividades_revisar", compose)
        self.assertIn("--aplicar", compose)

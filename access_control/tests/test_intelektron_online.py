"""Pruebas del modo online Intelektron: layouts, conversión de tag, respuesta y decisor."""

from ctypes import addressof, c_long, cast, pointer, sizeof
from datetime import datetime
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from access_control.services.intelectron.api3000_wrapper.api3000 import online
from access_control.services.intelectron.api3000_wrapper.api3000.online import (
    EV_POR_AUTORIZAR,
    STATUS_DISABLED,
    STATUS_ENABLED,
    EventoUsuario,
    ITKAnswerUserEvent,
    ITKOnlineUserInfo,
    ITKUserEvent,
    OnlineSession,
    build_answer,
    tag_from_access_id,
    tipo_acceso_from_event,
)
from access_control.services import intelektron_decision as dec


class LayoutTestCase(SimpleTestCase):
    """Offsets tal cual los declara CapaLogica_INREIN (la app de Clever)."""

    def test_user_info_offsets(self):
        self.assertEqual(ITKOnlineUserInfo.status.offset, 10)
        self.assertEqual(ITKOnlineUserInfo.access_ctl.offset, 12)
        self.assertEqual(ITKOnlineUserInfo.user_name.offset, 18)
        self.assertEqual(ITKOnlineUserInfo.user_msg.offset, 274)
        self.assertEqual(ITKOnlineUserInfo.user_id.offset, 530)
        self.assertEqual(ITKOnlineUserInfo.schedule_id.offset, 552)
        self.assertEqual(sizeof(ITKOnlineUserInfo), 556)

    def test_answer_offsets(self):
        self.assertEqual(ITKAnswerUserEvent.user_info.offset, 8)
        self.assertEqual(ITKAnswerUserEvent.print_enable.offset, 564)
        self.assertEqual(ITKAnswerUserEvent.ticket.offset, 568)

    def test_user_event_offsets(self):
        self.assertEqual(ITKUserEvent.source_id.offset, 4)
        self.assertEqual(ITKUserEvent.event_code.offset, 6)
        self.assertEqual(ITKUserEvent.direction.offset, 7)
        self.assertEqual(ITKUserEvent.date_time.offset, 8)


class TagTestCase(SimpleTestCase):
    def test_tag_es_hex_little_endian_mayuscula(self):
        # Credenciales reales del histórico de la placa .67, verificadas en Clientes.
        self.assertEqual(tag_from_access_id(82531707), "7B55EB04")
        self.assertEqual(tag_from_access_id(3378454896), "70215FC9")
        self.assertEqual(tag_from_access_id(1883839558), "461C4970")

    def test_tag_segun_lector(self):
        from access_control.services.intelectron.api3000_wrapper.api3000.online import tag_for_event
        mifare = EventoUsuario(access_id=82531707, source_id=1, event_code=2, direction=200, device_time=None, node=1)
        qr_dni = EventoUsuario(access_id=30123456, source_id=0, event_code=2, direction=200, device_time=None, node=1)
        self.assertEqual(tag_for_event(mifare), "7B55EB04")
        self.assertEqual(tag_for_event(qr_dni), "30123456")
        self.assertEqual(tag_for_event(qr_dni, {0}), "C0A5CB01")

    def test_tipo_acceso(self):
        ev = EventoUsuario(access_id=1, source_id=0, event_code=2, direction=201, device_time=None, node=1)
        self.assertEqual(tipo_acceso_from_event(ev), "S")
        ev.direction = 200
        self.assertEqual(tipo_acceso_from_event(ev), "E")
        ev.direction = 0
        ev.source_id = 1
        self.assertEqual(tipo_acceso_from_event(ev, {1}), "S")
        self.assertEqual(tipo_acceso_from_event(ev, set()), "E")


class AnswerTestCase(SimpleTestCase):
    def test_habilitado_acciona_el_lector_que_leyo(self):
        a = build_answer(access_id=82531707, habilitado=True, source_id=1, mensaje="Bienvenido",
                         cuando=datetime(2026, 10, 28, 12, 0, 5))
        self.assertEqual(a.user_info.status, STATUS_ENABLED)
        self.assertEqual(a.user_info.access_ctl, 2)
        self.assertEqual(a.user_info.access_id, 82531707)
        self.assertEqual(a.user_info.user_msg, b"Bienvenido")
        self.assertEqual((a.date_time.hour, a.date_time.minute, a.date_time.seconds), (12, 0, 5))
        self.assertEqual(a.print_enable, 0)

    def test_denegado_no_mueve_rele(self):
        a = build_answer(access_id=5, habilitado=False, source_id=0, mensaje="Cuota vencida")
        self.assertEqual(a.user_info.status, STATUS_DISABLED)
        self.assertEqual(a.user_info.access_ctl, 0)


class ParseRespuestaTestCase(SimpleTestCase):
    def _cursor(self, sets):
        cur = MagicMock()
        estado = {"i": 0}

        def _desc():
            cols = sets[estado["i"]][0]
            return [(c, None) for c in cols] if cols else None

        type(cur).description = property(lambda self: _desc())
        cur.fetchone.side_effect = lambda: sets[estado["i"]][1]

        def _next():
            if estado["i"] + 1 < len(sets):
                estado["i"] += 1
                return True
            return False

        cur.nextset.side_effect = _next
        return cur

    def test_salta_resultsets_previos(self):
        cur = self._cursor([
            (["otra"], (1,)),
            (["Flag_Habilitado", "Mensaje", "Info", "Foto", "Foto_Bin", "Id_Cliente", "Id_Cliente_Ref", "Tiempo_Mensaje"],
             (1, "Habilit. por Categoria", "", "", None, 855315, 0, 5)),
        ])
        fila = dec.parse_respuesta(cur)
        self.assertEqual(fila["flag_habilitado"], 1)
        self.assertEqual(fila["id_cliente"], 855315)

    def test_sin_tabla_de_respuesta(self):
        cur = self._cursor([(["otra"], (1,))])
        self.assertIsNone(dec.parse_respuesta(cur))


class DecisorTestCase(SimpleTestCase):
    @patch.object(dec.mssql, "connect")
    def test_decide_con_el_sp(self, connect):
        cur = MagicMock()
        cur.description = [("Flag_Habilitado", None), ("Mensaje", None), ("Info", None), ("Foto", None),
                           ("Foto_Bin", None), ("Id_Cliente", None), ("Id_Cliente_Ref", None), ("Tiempo_Mensaje", None)]
        cur.fetchone.return_value = (0, "La Persona no posee UCP al dia", "", "", None, 263866, 0, 5)
        conn = MagicMock()
        conn.cursor.return_value = cur
        connect.return_value = conn

        d = dec.DecisorClever(config={"HOST": "h", "DATABASE": "d", "USER": "u", "PASSWORD": "p"})
        res = d.decidir(id_controlador=74, tipo_acceso="E", tag="7B55EB04",
                        cuando=datetime(2026, 10, 28, 12, 0, 0), graba=False)

        self.assertFalse(res.habilitado)
        self.assertEqual(res.id_cliente, 263866)
        self.assertEqual(res.mensaje, "La Persona no posee UCP al dia")
        sql, params = cur.execute.call_args[0]
        self.assertIn("CP_SCA_RegistrarAcceso", sql)
        self.assertEqual(params, (74, "E", datetime(2026, 10, 28, 12, 0, 0), "7B55EB04", 0))

    @patch.object(dec.mssql, "connect")
    def test_sin_conexion_deniega(self, connect):
        connect.side_effect = RuntimeError("red caída")
        d = dec.DecisorClever(config={"HOST": "h", "DATABASE": "d", "USER": "u", "PASSWORD": "p"})
        res = d.decidir(id_controlador=74, tipo_acceso="E", tag="7B55EB04")
        self.assertFalse(res.habilitado)
        self.assertEqual(res.mensaje, "SIN CONEXION")
        self.assertIn("red caída", res.error)


class OnlineSessionTestCase(SimpleTestCase):
    def _sesion(self, on_user):
        native = MagicMock()
        lib = native.cdll
        lib.itk_open.return_value = 7
        lib.itk_answer_user_event.return_value = 1
        s = OnlineSession(ip="10.0.0.115", port=3001, on_user=on_user, native=native)
        return s, lib

    def test_abrir_pasa_los_callbacks(self):
        s, lib = self._sesion(lambda ev: None)
        self.assertEqual(s.abrir(), 7)
        args = lib.itk_open.call_args[0]
        self.assertEqual(args[3], b"10.0.0.115:3001")
        self.assertTrue(all(isinstance(a, int) and a > 0 for a in args[6:9]))
        self.assertTrue(s.viva)

    def test_evento_por_autorizar_se_responde(self):
        visto = {}

        def on_user(ev):
            visto["ev"] = ev
            return build_answer(access_id=ev.access_id, habilitado=True, source_id=ev.source_id, mensaje="OK")

        s, lib = self._sesion(on_user)
        s.abrir()
        raw = ITKUserEvent(access_id=82531707, source_id=0, event_code=EV_POR_AUTORIZAR, direction=200)
        s._user_cb(None, None, 1, pointer(raw))

        self.assertEqual(visto["ev"].tag, "7B55EB04")
        self.assertEqual(visto["ev"].event_code, EV_POR_AUTORIZAR)
        self.assertEqual(lib.itk_answer_user_event.call_count, 1)
        handle, node, answer_ref = lib.itk_answer_user_event.call_args[0]
        self.assertEqual(handle.value, 7)
        self.assertEqual(node.value, 1)
        answer = cast(answer_ref, online.POINTER(ITKAnswerUserEvent)).contents
        self.assertEqual(answer.user_info.status, STATUS_ENABLED)
        self.assertEqual(s.respuestas, 1)

    def test_si_on_user_explota_se_deniega(self):
        def on_user(ev):
            raise ValueError("boom")

        s, lib = self._sesion(on_user)
        s.abrir()
        raw = ITKUserEvent(access_id=1, source_id=0, event_code=EV_POR_AUTORIZAR, direction=200)
        s._user_cb(None, None, 1, pointer(raw))
        answer = cast(lib.itk_answer_user_event.call_args[0][2], online.POINTER(ITKAnswerUserEvent)).contents
        self.assertEqual(answer.user_info.status, STATUS_DISABLED)

    def test_keepalive_se_configura_al_crear(self):
        native = MagicMock()
        native.cdll.itk_set_keepalive.return_value = 1   # OP_OK
        OnlineSession(ip="10.0.0.117", port=3001, dest_node=1, keepalive=(30, 3), native=native)
        native.cdll.itk_set_keepalive.assert_called_once_with(30, 3, 1)
        native.cdll.itk_set_keepalive.reset_mock()
        OnlineSession(ip="10.0.0.117", port=3001, keepalive=None, native=native)
        native.cdll.itk_set_keepalive.assert_not_called()

    def test_no_identificado_tambien_se_decide(self):
        from access_control.management.commands.intelektron_online import EVENTOS_A_DECIDIR
        self.assertIn(EV_POR_AUTORIZAR, EVENTOS_A_DECIDIR)
        self.assertIn(online.EV_NO_IDENTIFICADO, EVENTOS_A_DECIDIR)

    def test_corte_del_equipo_marca_la_sesion_muerta(self):
        s, lib = self._sesion(lambda ev: None)
        s.abrir()
        s._conn_cb(None, 7, 2)
        self.assertFalse(s.viva)

    def test_accionar_rele_usa_itk_rele_control(self):
        s, lib = self._sesion(lambda ev: None)
        lib.itk_rele_control.return_value = 1
        self.assertFalse(s.accionar_rele(2, 3))          # sin sesión abierta no manda nada
        s.abrir()
        self.assertTrue(s.accionar_rele(2, 3))
        handle, node, rele, accion = lib.itk_rele_control.call_args[0]
        self.assertEqual((handle.value, node.value, rele.value, accion.value), (7, 1, 2, 3))
        lib.itk_rele_control.return_value = 147
        self.assertFalse(s.accionar_rele(5, 3))

    def test_hay_socket_lee_la_tabla_tcp(self):
        import os
        import tempfile
        s, _ = self._sesion(lambda ev: None)
        s.ip, s.port = "10.0.0.117", 3001
        cabecera = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        fila = "   0: 0E0012AC:B048 7500000A:0BB9 {st} 00000000:00000000 00:00000000 00000000     0        0 1 0 0\n"
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "tcp")
            for st, esperado in (("01", True), ("06", False)):
                with open(ruta, "w", encoding="ascii") as fh:
                    fh.write(cabecera + fila.format(st=st))
                with patch.object(online, "PROC_NET_TCP", ruta):
                    self.assertIs(s.hay_socket(), esperado)
            with patch.object(online, "PROC_NET_TCP", os.path.join(d, "no_existe")):
                self.assertIsNone(s.hay_socket())

    def test_watchdog_reconecta_cuando_desaparece_el_socket(self):
        s, lib = self._sesion(lambda ev: None)
        s.abrir()
        with patch.object(s, "hay_socket", return_value=False):
            s.mantener(parar=lambda: s.reconexiones >= 1, tick=0.001, watchdog=0.001, watchdog_fallas=2)
        self.assertEqual((s.caidas_socket, s.reconexiones), (1, 1))
        self.assertEqual(lib.itk_open.call_count, 2)
        self.assertTrue(s.viva)

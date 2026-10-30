"""Personas cargadas sólo en la app: alta, listado y validación en el molinete."""

import threading
from datetime import date, datetime, time
from unittest.mock import MagicMock

from django.contrib.auth.models import Group, User
from django.test import SimpleTestCase, TestCase

from common.roles import GRUPO_CONCESIONARIOS
from concesionarios import acceso, services
from concesionarios.models import (
    ID_LOCAL_BASE,
    Concesionario,
    Documento,
    Empresa,
    HorarioAcceso,
    HorarioFranja,
    PersonaLocal,
    TipoDocumento,
)
from xsys.models import XsysSocio

# Un martes a media mañana.
MARTES = datetime(2026, 10, 27, 10, 0)


class DocumentoDeLecturaTests(SimpleTestCase):
    def test_numero_pelado(self):
        self.assertEqual(acceso.documento_de_lectura("30123456"), 30123456)
        self.assertEqual(acceso.documento_de_lectura("30.123.456"), 30123456)

    def test_pdf417_formato_actual(self):
        tag = "00512345678@PEREZ@JUAN CARLOS@M@30123456@A@01/02/1985@15/03/2020@200"
        self.assertEqual(acceso.documento_de_lectura(tag), 30123456)

    def test_pdf417_formato_viejo(self):
        tag = "@30123456    @A@1@PEREZ@JUAN@ARGENTINA@01/02/1985@M@15/03/2010"
        self.assertEqual(acceso.documento_de_lectura(tag), 30123456)

    def test_qr_y_credencial_no_son_documento(self):
        self.assertIsNone(acceso.documento_de_lectura("9123456789"))
        self.assertIsNone(acceso.documento_de_lectura("7B55EB04"))
        self.assertIsNone(acceso.documento_de_lectura(""))


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Buffet SM")
        cls.persona = PersonaLocal.objects.create(doc_nro=30123456, apellido="PEREZ",
                                                  nombre="JUAN", credencial="7b55eb04")
        cls.conc = Concesionario.objects.create(id_cliente=cls.persona.id_cliente,
                                                empresa=cls.empresa)


class PersonaLocalModeloTests(_Base):
    def test_recibe_legajo_propio_fuera_del_rango_de_xsys(self):
        self.assertEqual(self.persona.id_cliente, ID_LOCAL_BASE + self.persona.pk)
        self.assertEqual(self.persona.credencial, "7B55EB04")

    def test_listado_y_candidatos(self):
        fila = services.listar()[0]
        self.assertTrue(fila["persona"]["local"])
        self.assertEqual(fila["persona"]["nombre_completo"], "PEREZ, JUAN")
        self.assertEqual([f["persona"]["doc_nro"] for f in services.listar(doc="30123456")],
                         [30123456])
        # Sin concesión vuelve a aparecer para poder asignarla de nuevo.
        self.assertEqual(services.candidatos_sin_registrar(busqueda="perez"), [])
        self.conc.delete()
        self.assertEqual([p["id_cliente"] for p in services.candidatos_sin_registrar(busqueda="perez")],
                         [self.persona.id_cliente])


class DecisionTests(_Base):
    def decidir(self, tag="30123456", tipo="E", cuando=MARTES):
        return acceso.decidir(tag=tag, tipo_acceso=tipo, cuando=cuando, id_acceso=14)

    def test_quien_no_es_local_sigue_a_xsys(self):
        self.assertIsNone(self.decidir(tag="20111222"))
        self.assertIsNone(self.decidir(tag="AABBCCDD"))

    def test_habilita_por_dni_y_por_credencial(self):
        for tag in ("30123456", "7B55EB04", "7b55eb04"):
            d = self.decidir(tag=tag)
            self.assertTrue(d.habilitado, tag)
            self.assertEqual(d.id_cliente, self.persona.id_cliente)
            self.assertEqual(d.validador, "concesion")
        self.assertEqual(self.decidir().mensaje, "CONCESIONARIO Buffet SM")

    def test_sin_concesion(self):
        self.conc.delete()
        d = self.decidir()
        self.assertFalse(d.habilitado)
        self.assertEqual(d.mensaje, "SIN CONCESION ASIGNADA")

    def test_baja_y_fechas(self):
        self.conc.fecha_baja = date(2026, 10, 27)
        self.conc.save()
        self.assertEqual(self.decidir().mensaje, "BAJA EN Buffet SM")
        self.conc.fecha_baja, self.conc.fecha_alta = None, date(2026, 11, 1)
        self.conc.save()
        self.assertEqual(self.decidir().mensaje, "HABILITADO DESDE 01/11/2026")

    def test_empresa_inactiva(self):
        self.empresa.activa = False
        self.empresa.save()
        self.assertFalse(self.decidir().habilitado)

    def test_horario(self):
        h = HorarioAcceso.objects.create(nombre="Lun a Vie 9 a 18")
        for dia in range(5):
            HorarioFranja.objects.create(horario=h, dia_semana=dia, hora_desde=time(9), hora_hasta=time(18))
        self.empresa.horario = h
        self.empresa.save()
        self.assertTrue(self.decidir().habilitado)
        d = self.decidir(cuando=datetime(2026, 10, 27, 20, 0))
        self.assertFalse(d.habilitado)
        self.assertEqual(d.mensaje, "FUERA DE HORARIO")
        # La salida no se frena por horario.
        self.assertTrue(self.decidir(tipo="S", cuando=datetime(2026, 10, 27, 20, 0)).habilitado)

    def test_documento_que_habilita_vencido(self):
        art = TipoDocumento.objects.create(codigo="ART", nombre="ART", bloquea_acceso=True)
        Documento.objects.create(id_cliente=self.persona.id_cliente, tipo=art,
                                 fecha_vencimiento=date(2026, 10, 1))
        d = self.decidir()
        self.assertFalse(d.habilitado)
        self.assertEqual(d.mensaje, "ART VENCIDA")
        # Con la nueva cargada, la vieja vencida ya no frena.
        Documento.objects.create(id_cliente=self.persona.id_cliente, tipo=art,
                                 fecha_vencimiento=date(2027, 10, 1))
        self.assertTrue(self.decidir().habilitado)

    def test_documento_que_no_habilita_no_frena(self):
        otro = TipoDocumento.objects.create(codigo="CV", nombre="Curriculum", bloquea_acceso=False)
        Documento.objects.create(id_cliente=self.persona.id_cliente, tipo=otro,
                                 fecha_vencimiento=date(2026, 1, 1))
        self.assertTrue(self.decidir().habilitado)


class MolineteTests(_Base):
    """``intelektron_online`` pregunta a la base local antes que a xSys."""

    def comando(self):
        from access_control.management.commands.intelektron_online import Command

        cmd = Command()
        cmd._lock_decision = threading.Lock()
        cmd._ultimas = {}
        cmd.antirebote_seg = 3
        cmd.local = MagicMock()
        cmd.decisor = None
        return cmd

    def placa(self):
        from access_control.management.commands.intelektron_online import Placa
        return Placa(ip="10.0.0.117", id_controlador=47, id_acceso=14)

    def test_persona_local_no_consulta_xsys(self):
        cmd = self.comando()
        decision, repetida = cmd._decidir(self.placa(), "30123456", "E", MARTES)
        self.assertTrue(decision.habilitado)
        self.assertFalse(repetida)
        cmd.local.decidir.assert_not_called()

    def test_los_demas_siguen_por_xsys(self):
        cmd = self.comando()
        cmd.local.decidir.return_value = MagicMock(error="", habilitado=True)
        cmd._decidir(self.placa(), "20111222", "E", MARTES)
        cmd.local.decidir.assert_called_once()

    def test_si_la_base_local_falla_sigue_por_xsys(self):
        from unittest.mock import patch

        cmd = self.comando()
        cmd.local.decidir.return_value = MagicMock(error="", habilitado=False)
        with patch.object(acceso, "decidir", side_effect=RuntimeError("base caída")):
            cmd._decidir(self.placa(), "30123456", "E", MARTES)
        cmd.local.decidir.assert_called_once()


class AltaPersonaAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Buffet SM")
        cls.operador = User.objects.create_user("op", password="x")
        cls.operador.groups.add(Group.objects.get_or_create(name=GRUPO_CONCESIONARIOS)[0])
        XsysSocio.objects.create(id_cliente=5001, apellido="GOMEZ", nombre="ANA",
                                 doc_nro=20111222, id_tipo_cli=1015, activo=1)

    def setUp(self):
        self.client.force_login(self.operador)

    def alta(self, **persona):
        datos = {"doc_nro": 30123456, "apellido": "perez", "nombre": "juan"}
        datos.update(persona)
        return self.client.post("/api/concesionarios/alta-persona/",
                                {"persona": datos, "empresa": self.empresa.id, "cargo": "Mozo"},
                                content_type="application/json")

    def test_alta_crea_persona_y_concesion(self):
        r = self.alta()
        self.assertEqual(r.status_code, 201, r.content)
        persona = PersonaLocal.objects.get()
        self.assertEqual(persona.apellido, "PEREZ")
        self.assertEqual(persona.creado_por, "op")
        conc = Concesionario.objects.get(id=r.json()["id"])
        self.assertEqual(conc.id_cliente, persona.id_cliente)
        self.assertEqual(conc.cargo, "Mozo")
        detalle = self.client.get(f"/api/concesionarios/{conc.id}/").json()
        self.assertTrue(detalle["persona"]["local"])

    def test_dni_que_ya_esta_en_xsys_se_rechaza(self):
        r = self.alta(doc_nro=20111222)
        self.assertEqual(r.status_code, 400)
        self.assertIn("legajo 5001", r.content.decode())
        self.assertFalse(PersonaLocal.objects.exists())

    def test_sin_empresa_no_crea_nada(self):
        r = self.client.post("/api/concesionarios/alta-persona/",
                             {"persona": {"doc_nro": 30123456, "apellido": "PEREZ"}},
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(PersonaLocal.objects.exists())

    def test_editar_datos(self):
        r = self.alta()
        cid = r.json()["id_cliente"]
        r = self.client.put(f"/api/concesionarios/persona-local/{cid}/", {"credencial": "aa11bb22"},
                            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(PersonaLocal.objects.get().credencial, "AA11BB22")

    def test_sin_rol_no_entra(self):
        self.client.force_login(User.objects.create_user("pelado", password="x"))
        self.assertEqual(self.alta().status_code, 403)


class VisorTests(_Base):
    def test_el_visor_muestra_nombre_y_dni(self):
        from access_control.models.models import ExternalAccessLogEntry
        from xsys import api_views as A

        ev = ExternalAccessLogEntry(external_id=1, tipo="E", id_cliente=self.persona.id_cliente,
                                    resultado="S", id_controlador=47, observacion="CONCESIONARIO Buffet SM")
        ctx = A._contexto_eventos([ev], [])
        p = A._evento_payload(ev, *ctx["xsys"])
        self.assertEqual(p["nombre"], "PEREZ, JUAN")
        self.assertEqual(p["doc_nro"], 30123456)
        self.assertEqual(p["estado"], "ok")
        self.assertIsNone(p["foto_url"])

    def test_foto_publica_de_persona_local(self):
        from concesionarios import fotos
        from concesionarios.tests.test_ingresos import _jpeg

        url = f"/api/xsys/socios/{self.persona.id_cliente}/foto/?thumb=1"
        self.assertEqual(self.client.get(url).status_code, 404)
        fotos.guardar(self.persona.id_cliente, _jpeg(), content_type="image/jpeg", usuario="op")
        r = self.client.get(url, HTTP_HOST="10.0.0.20")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(url, HTTP_HOST="10.0.0.20",
                                         HTTP_IF_NONE_MATCH=r["ETag"]).status_code, 304)

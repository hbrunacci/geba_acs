"""La pantalla: listado con filtros, alta, adjuntos y permisos."""

import json
from datetime import date, time, timedelta

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from common.roles import GRUPO_ADMIN, GRUPO_CONCESIONARIOS, GRUPO_RESPONSABLES
from concesionarios.models import (
    Concesionario,
    Documento,
    Empresa,
    HorarioAcceso,
    HorarioFranja,
    TipoDocumento,
)
from concesionarios.services import candidatos_sin_registrar, listar
from xsys.models import XsysSocio


def _socio(id_cliente, apellido, nombre, doc, tipo=1015):
    return XsysSocio.objects.create(
        id_cliente=id_cliente, apellido=apellido, nombre=nombre, doc_nro=doc,
        id_tipo_cli=tipo, categoria="CONCESIONARIO" if tipo == 1015 else "OTRO",
        activo=1)


class ListadoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.hoy = timezone.localdate()
        cls.buffet = Empresa.objects.create(nombre="Buffet SM")
        cls.limpieza = Empresa.objects.create(nombre="Limpieza JN")
        cls.art = TipoDocumento.objects.create(
            codigo="t_art", nombre="ART", dias_aviso=30, bloquea_acceso=True)

        _socio(1001, "PEREZ", "JUAN", 20111222)
        _socio(1002, "GOMEZ", "ANA", 30222333)
        _socio(1003, "LOPEZ", "MARIO", 25333444)
        Concesionario.objects.create(id_cliente=1001, empresa=cls.buffet, cargo="Mozo")
        Concesionario.objects.create(id_cliente=1002, empresa=cls.limpieza)
        Concesionario.objects.create(id_cliente=1003, empresa=cls.buffet, activo=False)

        Documento.objects.create(id_cliente=1001, tipo=cls.art,
                                 fecha_vencimiento=cls.hoy - timedelta(days=10))
        Documento.objects.create(id_cliente=1002, tipo=cls.art,
                                 fecha_vencimiento=cls.hoy + timedelta(days=5))

    def test_trae_persona_empresa_y_vencimiento(self):
        filas = listar()
        self.assertEqual(len(filas), 3)
        juan = next(f for f in filas if f["persona"]["id_cliente"] == 1001)
        self.assertEqual(juan["persona"]["nombre_completo"], "PEREZ, JUAN")
        self.assertEqual(juan["persona"]["doc_nro"], 20111222)
        self.assertEqual(juan["empresa"]["nombre"], "Buffet SM")
        self.assertEqual(juan["documentos"]["proximo"]["estado"], Documento.VENCIDO)
        self.assertTrue(juan["documentos"]["bloqueado"])

    def test_ordena_lo_urgente_primero(self):
        filas = listar()
        self.assertEqual(filas[0]["persona"]["id_cliente"], 1001)   # vencido
        self.assertEqual(filas[1]["persona"]["id_cliente"], 1002)   # por vencer
        self.assertIsNone(filas[2]["documentos"]["proximo"])        # sin documentos

    def test_filtra_por_empresa(self):
        filas = listar(empresa_id=self.limpieza.id)
        self.assertEqual([f["persona"]["id_cliente"] for f in filas], [1002])

    def test_filtra_por_dni_aunque_venga_con_puntos(self):
        filas = listar(doc="30.222.333")
        self.assertEqual([f["persona"]["id_cliente"] for f in filas], [1002])

    def test_filtra_por_apellido_sin_importar_mayusculas(self):
        self.assertEqual([f["persona"]["id_cliente"] for f in listar(apellido="lop")], [1003])

    def test_filtra_tambien_por_nombre(self):
        self.assertEqual([f["persona"]["id_cliente"] for f in listar(apellido="ana")], [1002])

    def test_busca_tambien_por_razon_social(self):
        """Muchos concesionarios están cargados como razón social, sin apellido."""
        XsysSocio.objects.create(
            id_cliente=1010, apellido="", nombre="", razon_social="RCA KIOSKO PB - SURACE",
            doc_nro=50111222, id_tipo_cli=1015, categoria="CONCESIONARIO", activo=1)
        Concesionario.objects.create(id_cliente=1010, empresa=self.buffet)
        filas = listar(apellido="kiosko")
        self.assertEqual([f["persona"]["id_cliente"] for f in filas], [1010])
        self.assertEqual(filas[0]["persona"]["nombre_completo"], "RCA KIOSKO PB - SURACE")

    def test_candidatos_tambien_por_razon_social(self):
        XsysSocio.objects.create(
            id_cliente=1011, apellido="", nombre="", razon_social="PARRILLA EL FOGON",
            doc_nro=50222333, id_tipo_cli=1015, categoria="CONCESIONARIO", activo=1)
        ids = [c["id_cliente"] for c in candidatos_sin_registrar(busqueda="fogon")]
        self.assertEqual(ids, [1011])

    def test_solo_activos(self):
        ids = [f["persona"]["id_cliente"] for f in listar(solo_activos=True)]
        self.assertNotIn(1003, ids)

    def test_solo_los_que_tienen_algo_por_resolver(self):
        ids = [f["persona"]["id_cliente"] for f in listar(con_problemas=True)]
        self.assertEqual(sorted(ids), [1001, 1002])

    def test_un_dni_que_no_es_de_nadie_no_trae_a_todos(self):
        self.assertEqual(listar(doc="99999999"), [])

    def test_si_el_socio_no_esta_en_el_espejo_la_fila_igual_aparece(self):
        """Perder al concesionario del listado sería peor que mostrarlo incompleto."""
        Concesionario.objects.create(id_cliente=7777, empresa=self.buffet)
        fila = next(f for f in listar() if f["persona"]["id_cliente"] == 7777)
        self.assertFalse(fila["persona"]["en_el_espejo"])
        self.assertEqual(fila["persona"]["nombre_completo"], "Legajo 7777")

    def test_candidatos_excluye_a_los_ya_cargados(self):
        _socio(1004, "NUEVO", "PEDRO", 40111222)
        ids = [c["id_cliente"] for c in candidatos_sin_registrar()]
        self.assertIn(1004, ids)
        self.assertNotIn(1001, ids)

    def test_candidatos_solo_mira_la_categoria_concesionario(self):
        _socio(1005, "SOCIO", "COMUN", 41222333, tipo=1002)
        self.assertNotIn(1005, [c["id_cliente"] for c in candidatos_sin_registrar()])


class PermisosTests(TestCase):
    """El módulo lo ven el superadmin, el staff y el grupo ``concesionarios``.

    Nadie más: tener el grupo ``Administrador`` de la app NO alcanza, porque el
    club lo pidió expresamente así.
    """

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Buffet")
        cls.pelado = User.objects.create_user("pelado", password="x")
        cls.operador = User.objects.create_user("operador", password="x")
        grupo, _ = Group.objects.get_or_create(name=GRUPO_CONCESIONARIOS)
        cls.operador.groups.add(grupo)
        cls.staff = User.objects.create_user("staff", password="x", is_staff=True)
        cls.jefe = User.objects.create_superuser("jefe", "j@x.com", "x")
        cls.admin_app = User.objects.create_user("admin_app", password="x")
        cls.admin_app.groups.add(Group.objects.get_or_create(name=GRUPO_ADMIN)[0])
        cls.responsable = User.objects.create_user("responsable", password="x")
        cls.responsable.groups.add(Group.objects.get_or_create(name=GRUPO_RESPONSABLES)[0])

    def _puede(self, user) -> tuple[int, int]:
        self.client.force_login(user)
        return (self.client.get("/concesionarios/").status_code,
                self.client.get("/api/concesionarios/").status_code)

    def test_sin_rol_no_entra_ni_a_la_pantalla_ni_a_la_api(self):
        self.assertEqual(self._puede(self.pelado), (403, 403))

    def test_anonimo_no_entra(self):
        self.assertEqual(self.client.get("/api/concesionarios/").status_code, 403)

    def test_el_grupo_concesionarios_alcanza(self):
        self.assertEqual(self._puede(self.operador), (200, 200))

    def test_el_staff_entra(self):
        self.assertEqual(self._puede(self.staff), (200, 200))

    def test_el_superusuario_entra(self):
        self.assertEqual(self._puede(self.jefe), (200, 200))

    def test_el_grupo_Administrador_por_si_solo_no_alcanza(self):
        self.assertEqual(self._puede(self.admin_app), (403, 403))

    def test_el_grupo_responsables_por_si_solo_no_alcanza(self):
        """``responsables`` todavía no habilita nada; que no habilite esto."""
        self.assertEqual(self._puede(self.responsable), (403, 403))

    def test_el_sidebar_solo_lo_muestra_a_quien_puede(self):
        self.client.force_login(self.operador)
        self.assertIn("Concesionarios</summary>",
                      self.client.get("/concesionarios/").content.decode())
        self.client.force_login(self.pelado)
        self.assertNotIn("Concesionarios</summary>",
                         self.client.get("/").content.decode())


class ApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("jefe", "j@x.com", "x")
        self.client.force_login(self.user)
        self.empresa = Empresa.objects.create(nombre="Buffet")
        self.tipo = TipoDocumento.objects.create(codigo="t_art", nombre="ART", bloquea_acceso=True)
        _socio(2001, "DIAZ", "LUCIA", 33444555)

    def _post(self, url, datos):
        return self.client.post(url, json.dumps(datos), content_type="application/json")

    def test_alta_de_concesionario(self):
        r = self._post("/api/concesionarios/alta/",
                       {"id_cliente": 2001, "empresa": self.empresa.id, "cargo": "Cajera"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Concesionario.objects.get(id_cliente=2001).cargo, "Cajera")

    def test_no_se_puede_cargar_dos_veces_la_misma_persona(self):
        self._post("/api/concesionarios/alta/", {"id_cliente": 2001, "empresa": self.empresa.id})
        r = self._post("/api/concesionarios/alta/", {"id_cliente": 2001, "empresa": self.empresa.id})
        self.assertEqual(r.status_code, 400)

    def test_la_baja_no_puede_ser_anterior_al_alta(self):
        r = self._post("/api/concesionarios/alta/", {
            "id_cliente": 2001, "empresa": self.empresa.id,
            "fecha_alta": "2026-05-01", "fecha_baja": "2026-04-01"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("fecha_baja", r.json())

    def test_detalle_trae_persona_y_documentos(self):
        c = Concesionario.objects.create(id_cliente=2001, empresa=self.empresa)
        Documento.objects.create(id_cliente=2001, tipo=self.tipo,
                                 fecha_vencimiento=date(2026, 12, 31))
        d = self.client.get(f"/api/concesionarios/{c.id}/").json()
        self.assertEqual(d["persona"]["nombre_completo"], "DIAZ, LUCIA")
        self.assertEqual(len(d["documentos"]), 1)

    def test_horario_por_atajo_de_dias(self):
        r = self._post("/api/concesionarios/horarios/", {
            "nombre": "Lun a Vie 9 18", "dias": [0, 1, 2, 3, 4],
            "hora_desde": "09:00", "hora_hasta": "18:00"})
        self.assertEqual(r.status_code, 201)
        h = HorarioAcceso.objects.get(nombre="Lun a Vie 9 18")
        self.assertEqual(h.franjas.count(), 5)
        self.assertEqual(h.resumen, "Lun a Vie 09:00–18:00")

    def test_editar_un_horario_reemplaza_sus_franjas(self):
        h = HorarioAcceso.objects.create(nombre="X")
        HorarioFranja.objects.create(horario=h, dia_semana=0, hora_desde=time(8), hora_hasta=time(9))
        r = self.client.put(f"/api/concesionarios/horarios/{h.id}/",
                            json.dumps({"dias": [5, 6], "hora_desde": "10:00", "hora_hasta": "14:00"}),
                            content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sorted(h.franjas.values_list("dia_semana", flat=True)), [5, 6])

    def test_rechaza_una_franja_de_ancho_cero(self):
        r = self._post("/api/concesionarios/horarios/", {
            "nombre": "Nula", "dias": [0], "hora_desde": "09:00", "hora_hasta": "09:00"})
        self.assertEqual(r.status_code, 400)

    def test_no_se_borra_una_empresa_con_gente(self):
        Concesionario.objects.create(id_cliente=2001, empresa=self.empresa)
        r = self.client.delete(f"/api/concesionarios/empresas/{self.empresa.id}/")
        self.assertEqual(r.status_code, 409)
        self.assertTrue(Empresa.objects.filter(pk=self.empresa.pk).exists())

    def test_el_listado_resume_los_estados(self):
        Concesionario.objects.create(id_cliente=2001, empresa=self.empresa)
        Documento.objects.create(id_cliente=2001, tipo=self.tipo,
                                 fecha_vencimiento=timezone.localdate() - timedelta(days=1))
        d = self.client.get("/api/concesionarios/").json()
        self.assertEqual(d["resumen"]["con_vencidos"], 1)
        self.assertEqual(d["resumen"]["bloqueados"], 1)


@override_settings(MEDIA_ROOT="/tmp/conc_test_media")
class AdjuntosTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("jefe", "j@x.com", "x")
        self.client.force_login(self.user)
        self.tipo = TipoDocumento.objects.create(codigo="t_art", nombre="ART")

    def _subir(self, nombre, contenido=b"%PDF-1.4 test"):
        return self.client.post("/api/concesionarios/documentos/", {
            "id_cliente": 3001, "tipo": self.tipo.id, "fecha_vencimiento": "2027-01-31",
            "archivo": SimpleUploadedFile(nombre, contenido, content_type="application/pdf"),
        })

    def test_sube_y_se_puede_descargar_con_permiso(self):
        r = self._subir("art.pdf")
        self.assertEqual(r.status_code, 201, r.content)
        doc_id = r.json()["id"]
        self.assertEqual(r.json()["archivo_url"], f"/api/concesionarios/documentos/{doc_id}/archivo/")
        self.assertEqual(Documento.objects.get(pk=doc_id).archivo_nombre, "art.pdf")
        descarga = self.client.get(f"/api/concesionarios/documentos/{doc_id}/archivo/")
        self.assertEqual(descarga.status_code, 200)

    def test_el_archivo_no_se_sirve_a_cualquiera(self):
        doc_id = self._subir("art.pdf").json()["id"]
        self.client.logout()
        self.assertEqual(
            self.client.get(f"/api/concesionarios/documentos/{doc_id}/archivo/").status_code, 403)

    def test_rechaza_una_extension_que_no_es_un_papel(self):
        r = self.client.post("/api/concesionarios/documentos/", {
            "id_cliente": 3001, "tipo": self.tipo.id, "fecha_vencimiento": "2027-01-31",
            "archivo": SimpleUploadedFile("virus.exe", b"MZ", content_type="application/exe"),
        })
        self.assertEqual(r.status_code, 400)
        self.assertIn("archivo", r.json())

    def test_el_tipo_que_exige_vencimiento_lo_exige(self):
        r = self.client.post("/api/concesionarios/documentos/",
                             {"id_cliente": 3001, "tipo": self.tipo.id})
        self.assertEqual(r.status_code, 400)
        self.assertIn("fecha_vencimiento", r.json())

    def test_borrar_el_documento_se_lleva_el_archivo(self):
        doc_id = self._subir("art.pdf").json()["id"]
        ruta = Documento.objects.get(pk=doc_id).archivo.path
        import os
        self.assertTrue(os.path.exists(ruta))
        self.assertEqual(
            self.client.delete(f"/api/concesionarios/documentos/{doc_id}/").status_code, 204)
        self.assertFalse(os.path.exists(ruta))

    def test_el_nombre_del_archivo_no_arma_la_ruta(self):
        """Llega del navegador: no se le confía el path."""
        doc_id = self._subir("../../escape.pdf").json()["id"]
        guardado = Documento.objects.get(pk=doc_id).archivo.name
        self.assertTrue(guardado.startswith("documentos/3001/"))
        self.assertNotIn("..", guardado)

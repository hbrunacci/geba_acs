"""La pantalla de fichas de socios: listado, ficha editable, grupo y cuenta corriente.

Lo que toca xSys (``ficha`` y ``cuenta_corriente``) se prueba con la conexión
mockeada: acá interesa la lógica —qué se puede escribir, qué se valida, qué exige
confirmación— y no que el ODBC conteste. Lo que sale del espejo local
(``grupo_familiar``, el listado y los contratos) se prueba contra la base de test.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from xsys.models import XsysContrato, XsysSocio, XsysSocioEdicion
from xsys.services import ficha, grupo_familiar

TITULAR = 569449
CONYUGE = 541521
HIJA = 901146


def _socio(id_cliente, apellido, nombre, **kw):
    datos = {
        "apellido": apellido,
        "nombre": nombre,
        "activo": 1,
        "id_cliente_ref": 0,
        "categoria": "ACTIVO MAYOR",
        "id_tipo_cli": 1003,
        "doc_nro": 10000000 + id_cliente,
        "id_cliente_externo": str(id_cliente),
    }
    datos.update(kw)
    return XsysSocio.objects.create(id_cliente=id_cliente, **datos)


def _familia():
    _socio(TITULAR, "FERRO", "MIGUEL ANGEL", categoria="VITALICIO + 71", id_tipo_cli=1010)
    _socio(CONYUGE, "ORTIZ", "SUSANA", id_cliente_ref=TITULAR,
           categoria="VITALICIO + 71", id_tipo_cli=1010)
    _socio(HIJA, "ABRAMOVITSCH FERRO", "IRENA PAZ", id_cliente_ref=TITULAR,
           categoria="CADETE", id_tipo_cli=1001,
           fecha_nac=timezone.make_aware(datetime.datetime(2011, 4, 5)))


class GrupoFamiliarTests(TestCase):
    def setUp(self):
        _familia()

    def test_el_titular_ve_a_todo_el_grupo(self):
        g = grupo_familiar.de(TITULAR)
        self.assertTrue(g["tiene_grupo"])
        self.assertTrue(g["es_titular"])
        self.assertEqual(g["cantidad"], 3)
        self.assertEqual([m["rol"] for m in g["miembros"]],
                         ["titular", "integrante", "integrante"])

    def test_el_integrante_ve_el_mismo_grupo(self):
        g = grupo_familiar.de(HIJA)
        self.assertFalse(g["es_titular"])
        self.assertEqual(g["id_titular"], TITULAR)
        self.assertEqual(g["cantidad"], 3)

    def test_marca_cual_es_el_socio_consultado(self):
        g = grupo_familiar.de(HIJA)
        consultados = [m["id_cliente"] for m in g["miembros"] if m["es_consultado"]]
        self.assertEqual(consultados, [HIJA])

    def test_socio_solo_no_tiene_grupo(self):
        _socio(700001, "SOLO", "JUAN")
        g = grupo_familiar.de(700001)
        self.assertFalse(g["tiene_grupo"])
        self.assertEqual(g["cantidad"], 1)

    def test_incluye_a_los_integrantes_de_baja_pero_los_marca(self):
        _socio(700002, "FERRO", "HIJO MAYOR", id_cliente_ref=TITULAR, activo=0)
        g = grupo_familiar.de(TITULAR)
        self.assertEqual(g["cantidad"], 4)
        self.assertEqual(g["activos"], 3)
        self.assertFalse([m for m in g["miembros"] if m["id_cliente"] == 700002][0]["activo"])

    def test_titular_ausente_del_espejo_no_rompe(self):
        _socio(700003, "HUERFANO", "ANA", id_cliente_ref=888888)
        g = grupo_familiar.de(700003)
        self.assertTrue(g["titular_desconocido"])

    def test_socio_inexistente(self):
        g = grupo_familiar.de(12345)
        self.assertFalse(g["tiene_grupo"])
        self.assertEqual(g["miembros"], [])


class PermisosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pelado = User.objects.create_user("pelado", password="x")
        cls.con_rol = User.objects.create_user("mostrador", password="x")
        cls.con_rol.groups.add(Group.objects.get_or_create(name="socios")[0])

    def test_sin_rol_no_entra_a_la_pantalla(self):
        self.client.login(username="pelado", password="x")
        self.assertEqual(self.client.get("/xsys/fichas/").status_code, 403)

    def test_sin_rol_no_entra_a_las_apis(self):
        self.client.login(username="pelado", password="x")
        for url in ("/api/xsys/socios/listado/",
                    f"/api/xsys/socios/{TITULAR}/grupo-familiar/",
                    f"/api/xsys/socios/{TITULAR}/contratos/"):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_anonimo_no_entra(self):
        self.assertEqual(self.client.get("/api/xsys/socios/listado/").status_code, 403)

    def test_con_el_grupo_socios_entra(self):
        self.client.login(username="mostrador", password="x")
        self.assertEqual(self.client.get("/xsys/fichas/").status_code, 200)
        self.assertEqual(self.client.get("/api/xsys/socios/listado/").status_code, 200)


class ListadoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        u = User.objects.create_user("op", password="x")
        u.groups.add(Group.objects.get_or_create(name="socios")[0])

    def setUp(self):
        self.client.login(username="op", password="x")
        _familia()
        _socio(700010, "PEREZ", "ANA", activo=0)
        _socio(700011, "GOMEZ", "LUIS", credencial_nro="ABC123")

    def _get(self, qs=""):
        return self.client.get("/api/xsys/socios/listado/" + qs).json()

    def test_lista_todos_por_defecto_solo_activos_si_se_pide(self):
        self.assertEqual(self._get("?activo=")["total"], 5)
        self.assertEqual(self._get("?activo=1")["total"], 4)
        self.assertEqual(self._get("?activo=0")["total"], 1)

    def test_busca_por_apellido_documento_y_credencial(self):
        self.assertEqual(self._get("?q=ortiz&activo=")["total"], 1)
        self.assertEqual(self._get("?q=ABC123&activo=")["total"], 1)
        doc = XsysSocio.objects.get(pk=HIJA).doc_nro
        self.assertEqual(self._get(f"?q={doc}&activo=")["total"], 1)

    def test_busca_por_numero_de_socio(self):
        self.assertEqual(self._get(f"?q={TITULAR}&activo=")["total"], 1)

    def test_filtra_por_categoria(self):
        self.assertEqual(self._get("?categoria=1001&activo=")["total"], 1)

    def test_filtro_de_grupo(self):
        self.assertEqual(self._get("?grupo=titular&activo=")["total"], 1)
        self.assertEqual(self._get("?grupo=integrante&activo=")["total"], 2)
        self.assertEqual(self._get("?grupo=sin_grupo&activo=")["total"], 2)

    def test_marca_titulares_e_integrantes(self):
        por_id = {r["id_cliente"]: r for r in self._get("?activo=")["resultados"]}
        self.assertTrue(por_id[TITULAR]["es_titular"])
        self.assertFalse(por_id[TITULAR]["es_integrante"])
        self.assertTrue(por_id[HIJA]["es_integrante"])
        self.assertEqual(por_id[HIJA]["id_cliente_ref"], TITULAR)

    def test_pagina(self):
        d = self._get("?activo=&limit=2")
        self.assertEqual(len(d["resultados"]), 2)
        self.assertEqual(d["total"], 5)
        siguiente = self._get("?activo=&limit=2&offset=2")
        ids = {r["id_cliente"] for r in d["resultados"]}
        self.assertFalse(ids & {r["id_cliente"] for r in siguiente["resultados"]})

    def test_cuota_vencida_no_marca_al_dia_a_los_de_baja(self):
        fila = [r for r in self._get("?activo=0")["resultados"]][0]
        self.assertFalse(fila["cuota_al_dia"])

    def test_filtro_de_cuota(self):
        XsysSocio.objects.filter(pk=TITULAR).update(ult_cuota_paga=timezone.now())
        self.assertEqual(self._get("?cuota=al_dia&activo=")["total"], 1)
        self.assertEqual(self._get("?cuota=vencida&activo=")["total"], 3)


class ContratosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        u = User.objects.create_user("op", password="x")
        u.groups.add(Group.objects.get_or_create(name="socios")[0])

    def setUp(self):
        self.client.login(username="op", password="x")
        _familia()
        XsysContrato.objects.create(id_contrato=1, id_cliente=TITULAR, activo=1,
                                    descripcion="CUOTA SOCIAL", deuda=0)
        XsysContrato.objects.create(id_contrato=2, id_cliente=TITULAR, activo=1,
                                    descripcion="COMODIDADES", deuda=13700,
                                    producto_desc="COCHERA",
                                    ultimo_pago_fecha=datetime.date(2026, 9, 1),
                                    ultimo_pago_importe=21300)
        XsysContrato.objects.create(id_contrato=3, id_cliente=TITULAR, activo=0,
                                    descripcion="VIEJO")

    def test_solo_activos_por_defecto(self):
        d = self.client.get(f"/api/xsys/socios/{TITULAR}/contratos/").json()
        self.assertEqual([c["descripcion"] for c in d["contratos"]],
                         ["COMODIDADES", "CUOTA SOCIAL"])

    def test_con_todos_trae_los_de_baja(self):
        d = self.client.get(f"/api/xsys/socios/{TITULAR}/contratos/?todos=1").json()
        self.assertEqual(len(d["contratos"]), 3)

    def test_trae_los_datos_clave(self):
        d = self.client.get(f"/api/xsys/socios/{TITULAR}/contratos/").json()
        com = [c for c in d["contratos"] if c["descripcion"] == "COMODIDADES"][0]
        self.assertEqual(com["deuda"], 13700)
        self.assertEqual(com["actividad"], "COCHERA")
        self.assertEqual(com["ultimo_pago_importe"], 21300)

    def test_informa_hasta_cuando_lo_habilita_la_cuota(self):
        XsysSocio.objects.filter(pk=TITULAR).update(ult_cuota_paga=timezone.now())
        d = self.client.get(f"/api/xsys/socios/{TITULAR}/contratos/").json()
        self.assertTrue(d["cuota_al_dia"])
        self.assertIsNotNone(d["cuota_limite"])


class DefinicionDeFichaTests(TestCase):
    """La forma de la ficha, sin tocar xSys."""

    def setUp(self):
        ficha.invalidar_cache()
        self.addCleanup(ficha.invalidar_cache)

    def _catalogos(self):
        return {
            "tipo_doc": [{"valor": "1", "texto": "DNI"}],
            "categoria": [{"valor": "1001", "texto": "CADETE"},
                          {"valor": "1010", "texto": "VITALICIO + 71"}],
            "estado_cliente": [{"valor": "0", "texto": "Activo POR CAJA"}],
            "motivo_est": [{"valor": "0", "texto": "NO DEFINIDO"}],
            "cond_vta": [{"valor": "NO", "texto": "NORMAL"}],
            "sexo": [{"valor": "M", "texto": "Masculino"}],
            "estado_civil": [{"valor": "S", "texto": "Soltero/a"}],
            "tipo_persona": [{"valor": "F", "texto": "Física"}],
        }

    def test_los_campos_de_sistema_no_son_editables(self):
        for col in ("Id_Cliente", "Id_Cliente_Externo", "Ult_Cuota_Paga", "Fecha_Modif"):
            self.assertFalse(ficha.POR_COL[col].editable, col)
            self.assertNotIn(col, ficha.EDITABLES)

    def test_los_campos_peligrosos_llevan_advertencia(self):
        for col in ("Id_Tipo_Cli", "Activo", "Id_Cliente_Ref", "Credencial_Nro"):
            self.assertTrue(ficha.POR_COL[col].riesgo, col)

    def test_no_expone_columnas_contables_del_erp(self):
        for col in ("Cred_CtaCte", "Id_Alias_Cta_Cont_Venta", "Coef_Comi_Vta", "Clave_Web"):
            self.assertNotIn(col, ficha.POR_COL)

    def test_toda_opcion_tiene_su_catalogo(self):
        for c in ficha.CAMPOS:
            if c.tipo == "opcion":
                self.assertTrue(c.catalogo, c.col)
                self.assertTrue(
                    c.catalogo in ficha.OPCIONES_FIJAS or c.catalogo in ficha.CATALOGOS_SQL, c.col)

    def test_definicion_agrupa_y_adjunta_opciones(self):
        with patch.object(ficha, "catalogos", self._catalogos):
            grupos = ficha.definicion()
        nombres = [g["grupo"] for g in grupos]
        self.assertIn("Identificación", nombres)
        self.assertIn("Condición de socio", nombres)
        campos = {c["col"]: c for g in grupos for c in g["campos"]}
        self.assertEqual(len(campos["Id_Tipo_Cli"]["opciones"]), 2)
        self.assertIsNone(campos["Apellido"]["opciones"])


class NormalizarTests(TestCase):
    """Conversión y validación de cada tipo de campo."""

    def setUp(self):
        ficha.invalidar_cache()
        self.addCleanup(ficha.invalidar_cache)
        self.cats = {"categoria": [{"valor": "1001", "texto": "CADETE"}],
                     "sexo": [{"valor": "M", "texto": "Masculino"}]}

    def _norm(self, col, valor):
        with patch.object(ficha, "catalogos", lambda **kw: self.cats):
            return ficha._normalizar(ficha.POR_COL[col], valor)

    def test_vacio_es_null(self):
        self.assertIsNone(self._norm("Email", ""))
        self.assertIsNone(self._norm("Email", "   "))
        self.assertIsNone(self._norm("Doc_Nro", ""))

    def test_entero(self):
        self.assertEqual(self._norm("Doc_Nro", "31.850.936"), 31850936)
        with self.assertRaises(ficha.FichaError):
            self._norm("Doc_Nro", "AB")

    def test_fecha(self):
        self.assertEqual(self._norm("Fecha_Nac", "2011-04-05"),
                         datetime.datetime(2011, 4, 5))
        with self.assertRaises(ficha.FichaError):
            self._norm("Fecha_Nac", "05/04/2011")

    def test_bool(self):
        self.assertEqual(self._norm("Activo", "1"), 1)
        self.assertEqual(self._norm("Activo", "0"), 0)
        self.assertEqual(self._norm("Activo", True), 1)
        with self.assertRaises(ficha.FichaError):
            self._norm("Activo", "quizas")

    def test_opcion_numerica_valida_contra_el_catalogo(self):
        self.assertEqual(self._norm("Id_Tipo_Cli", "1001"), 1001)
        with self.assertRaises(ficha.FichaError):
            self._norm("Id_Tipo_Cli", "9999")

    def test_opcion_de_texto(self):
        self.assertEqual(self._norm("Sexo", "M"), "M")
        with self.assertRaises(ficha.FichaError):
            self._norm("Sexo", "Z")

    def test_respeta_el_largo_maximo(self):
        with self.assertRaises(ficha.FichaError):
            self._norm("Tel_Movil", "9" * 40)


class _CursorFalso:
    """Cursor mínimo que devuelve lo que se le programa por orden de consulta."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.ejecutadas = []
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.ejecutadas.append((" ".join(sql.split()), params))
        return self

    def fetchone(self):
        return self.respuestas.pop(0) if self.respuestas else None

    def fetchall(self):
        return self.respuestas.pop(0) if self.respuestas else []


class GuardarTests(TestCase):
    """La escritura sobre xSys, con la conexión mockeada."""

    def setUp(self):
        ficha.invalidar_cache()
        self.addCleanup(ficha.invalidar_cache)
        self.cats = {"categoria": [{"valor": "1001", "texto": "CADETE"}]}
        self.parche_cat = patch.object(ficha, "catalogos", lambda **kw: self.cats)
        self.parche_cat.start()
        self.addCleanup(self.parche_cat.stop)
        # El refresco del espejo pega a xSys de verdad; acá no interesa.
        self.parche_espejo = patch.object(ficha, "_refrescar_espejo")
        self.parche_espejo.start()
        self.addCleanup(self.parche_espejo.stop)

    def _conn(self, cursor):
        conn = MagicMock()
        conn.cursor.return_value = cursor
        return conn

    def _crudo(self, **kw):
        base = {c.col: None for c in ficha.CAMPOS}
        base.update({"Id_Cliente": TITULAR, "Apellido": "FERRO", "Doc_Nro": 7621683, "Activo": 1})
        base.update(kw)
        return base

    def _guardar(self, cambios, crudo=None, respuestas=(), **kw):
        cur = _CursorFalso(respuestas)
        with patch.object(ficha, "connect", lambda *a, **k: self._conn(cur)), \
             patch.object(ficha, "_leer_crudo", lambda c, i: crudo or self._crudo()), \
             patch.object(ficha, "leer", lambda i, c=None: {"Apellido": "FERRO"}):
            return ficha.guardar(TITULAR, cambios, usuario="op", **kw), cur

    def test_rechaza_campos_que_no_son_editables(self):
        with self.assertRaises(ficha.FichaError) as e:
            ficha.guardar(TITULAR, {"Ult_Cuota_Paga": "2020-01-01"})
        self.assertIn("no editables", str(e.exception))

    def test_rechaza_columnas_inventadas(self):
        with self.assertRaises(ficha.FichaError):
            ficha.guardar(TITULAR, {"DROP_TABLE": "1"})

    def test_sin_cambios_no_escribe(self):
        res, cur = self._guardar({"Apellido": "FERRO"})
        self.assertEqual(res["aplicados"], [])
        self.assertFalse([s for s, _ in cur.ejecutadas if s.startswith("UPDATE")])

    def test_guarda_solo_los_campos_que_cambiaron(self):
        res, cur = self._guardar({"Apellido": "FERRO", "Nombre": "MIGUEL"})
        update = [s for s, _ in cur.ejecutadas if s.startswith("UPDATE")]
        self.assertEqual(len(update), 1)
        self.assertIn("[Nombre] = ?", update[0])
        self.assertNotIn("[Apellido] = ?", update[0])
        self.assertEqual([a["campo"] for a in res["aplicados"]], ["Nombre"])

    def test_siempre_actualiza_fecha_modif(self):
        _, cur = self._guardar({"Nombre": "MIGUEL"})
        self.assertIn("[Fecha_Modif] = GETDATE()",
                      [s for s, _ in cur.ejecutadas if s.startswith("UPDATE")][0])

    def test_deja_auditoria_por_campo(self):
        self._guardar({"Nombre": "MIGUEL", "Email": "a@b.com"})
        filas = XsysSocioEdicion.objects.filter(id_cliente=TITULAR)
        self.assertEqual(filas.count(), 2)
        nombre = filas.get(campo="Nombre")
        self.assertEqual(nombre.valor_nuevo, "MIGUEL")
        self.assertEqual(nombre.usuario, "op")
        self.assertEqual(nombre.etiqueta, "Nombre")

    def test_el_documento_repetido_pide_confirmacion_y_no_escribe(self):
        otros = [(999, "OTRO SOCIO", 1)]
        with self.assertRaises(ficha.FichaConfirmacion) as e:
            self._guardar({"Doc_Nro": "31850936"}, respuestas=[otros])
        self.assertIn("ya está cargado en otras", str(e.exception))
        self.assertIn("999 OTRO SOCIO (activa)", str(e.exception))
        self.assertFalse(XsysSocioEdicion.objects.exists())

    def test_confirmado_si_escribe(self):
        res, cur = self._guardar({"Doc_Nro": "31850936"}, respuestas=[[]], confirmado=True)
        self.assertEqual([a["campo"] for a in res["aplicados"]], ["Doc_Nro"])

    def test_la_baja_pide_confirmacion(self):
        with self.assertRaises(ficha.FichaConfirmacion):
            self._guardar({"Activo": "0"})

    def test_documento_sin_repetidos_no_pide_confirmacion(self):
        res, _ = self._guardar({"Doc_Nro": "31850936"}, respuestas=[[]])
        self.assertEqual([a["campo"] for a in res["aplicados"]], ["Doc_Nro"])

    def test_no_puede_ser_titular_de_si_mismo(self):
        with self.assertRaises(ficha.FichaError) as e:
            self._guardar({"Id_Cliente_Ref": str(TITULAR)})
        self.assertIn("sí mismo", str(e.exception))

    def test_titular_inexistente(self):
        with self.assertRaises(ficha.FichaError) as e:
            self._guardar({"Id_Cliente_Ref": "888"}, respuestas=[None])
        self.assertIn("No existe", str(e.exception))

    def test_el_titular_no_puede_ser_a_su_vez_integrante(self):
        with self.assertRaises(ficha.FichaError) as e:
            self._guardar({"Id_Cliente_Ref": "888"}, respuestas=[(TITULAR, 1, "OTRO")])
        self.assertIn("cabeza de grupo", str(e.exception))

    def test_no_deja_colgados_a_los_propios_integrantes(self):
        with self.assertRaises(ficha.FichaError) as e:
            self._guardar({"Id_Cliente_Ref": "888"}, respuestas=[(0, 1, "TITULAR OK"), (2,)])
        self.assertIn("integrantes a cargo", str(e.exception))

    def test_el_error_del_trigger_llega_al_operador(self):
        cur = _CursorFalso([])

        def explota(sql, params=None):
            cur.ejecutadas.append((sql, params))
            if sql.strip().startswith("UPDATE"):
                raise RuntimeError("[Microsoft][ODBC Driver 18][SQL Server]El socio ya existe.")
            return cur

        cur.execute = explota
        with patch.object(ficha, "connect", lambda *a, **k: self._conn(cur)), \
             patch.object(ficha, "_leer_crudo", lambda c, i: self._crudo()), \
             patch.object(ficha, "leer", lambda i, c=None: {}):
            with self.assertRaises(ficha.FichaError) as e:
                ficha.guardar(TITULAR, {"Nombre": "MIGUEL"})
        self.assertIn("El socio ya existe.", str(e.exception))
        self.assertFalse(XsysSocioEdicion.objects.exists())


class FichaAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        u = User.objects.create_user("op", password="x")
        u.groups.add(Group.objects.get_or_create(name="socios")[0])

    def setUp(self):
        self.client.login(username="op", password="x")
        self.url = f"/api/xsys/socios/{TITULAR}/ficha/"

    def test_404_si_no_esta_en_xsys(self):
        with patch.object(ficha, "leer", lambda *a, **k: None):
            self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_devuelve_definicion_valores_e_historial(self):
        XsysSocioEdicion.objects.create(id_cliente=TITULAR, campo="Nombre",
                                        etiqueta="Nombre", valor_nuevo="X", usuario="op")
        with patch.object(ficha, "leer", lambda *a, **k: {"Apellido": "FERRO"}), \
             patch.object(ficha, "definicion", lambda: [{"grupo": "G", "campos": []}]):
            d = self.client.get(self.url).json()
        self.assertEqual(d["valores"]["Apellido"], "FERRO")
        self.assertEqual(len(d["ediciones"]), 1)

    def test_patch_sin_cambios_da_400(self):
        r = self.client.patch(self.url, data="{}", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_patch_devuelve_409_cuando_hay_que_confirmar(self):
        with patch.object(ficha, "guardar", side_effect=ficha.FichaConfirmacion(["ojo"])):
            r = self.client.patch(self.url, data='{"cambios": {"Doc_Nro": "1"}}',
                                  content_type="application/json")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["confirmar"], ["ojo"])

    def test_patch_propaga_el_error_de_validacion(self):
        with patch.object(ficha, "guardar", side_effect=ficha.FichaError("mal el dato")):
            r = self.client.patch(self.url, data='{"cambios": {"Doc_Nro": "x"}}',
                                  content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"], "mal el dato")

    def test_patch_pasa_usuario_y_confirmacion_al_servicio(self):
        with patch.object(ficha, "guardar", return_value={"aplicados": [], "avisos": [],
                                                          "ficha": {}}) as g:
            self.client.patch(self.url,
                              data='{"cambios": {"Nombre": "X"}, "confirmar": true}',
                              content_type="application/json")
        self.assertEqual(g.call_args.kwargs["usuario"], "op")
        self.assertTrue(g.call_args.kwargs["confirmado"])


class PantallaTests(TestCase):
    """Que el HTML y el JS hablen el mismo idioma: todo getElementById existe."""

    @classmethod
    def setUpTestData(cls):
        u = User.objects.create_user("op", password="x")
        u.groups.add(Group.objects.get_or_create(name="socios")[0])

    def setUp(self):
        self.client.login(username="op", password="x")

    def _html(self):
        return self.client.get("/xsys/fichas/").content.decode()

    def test_cada_id_que_busca_el_js_esta_en_el_html(self):
        import re

        html = self._html()
        ids = set(re.findall(r'id="([^"]+)"', html))
        pedidos = set(re.findall(r'\$\("([^"]+)"\)', html))
        self.assertTrue(pedidos, "el JS no usa el helper $()")
        self.assertEqual(pedidos - ids, set())

    def test_no_quedan_comentarios_django_sin_renderizar(self):
        self.assertNotIn("{#", self._html())

    def test_las_urls_de_api_que_llama_el_js_existen(self):
        import re

        from django.urls import resolve

        html = self._html()
        for ruta in set(re.findall(r'"(/api/xsys/socios/[^"?]*)"', html)):
            concreta = ruta.replace('" + id + "', str(TITULAR)).replace('" + socioActual + "',
                                                                        str(TITULAR))
            if "+" in concreta:
                continue
            resolve(concreta)

    def test_manda_el_token_csrf(self):
        self.assertIn("X-CSRFToken", self._html())

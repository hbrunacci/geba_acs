"""El chequeo que avisa antes de que la persona quede parada en el molinete.

Pasó dos veces en cuatro días: fichas cargadas a mano sin documento, y el error
se descubrió cuando alguien no pudo entrar. Acá se comprueba que el chequeo los
encuentre el día que se carga la ficha, que no invente casos, y —lo que más
importa para que la lista siga sirviendo— que se resuelva solo cuando le cargan
el dato que faltaba.
"""

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from access_control.models import BioStarUser, SocioAviso
from xsys.models import XsysContrato, XsysSocio
from xsys.services import chequeo_identificacion as chk

CID = 900613


def _socio(id_cliente=CID, *, doc=0, cred="", activo=1, tipo=1127, apellido="FAINBERG"):
    return XsysSocio.objects.create(
        id_cliente=id_cliente, doc_nro=doc, apellido=apellido, nombre="MARTINA",
        credencial_nro=cred, activo=activo, id_tipo_cli=tipo, categoria="ALUMNO IGSM",
        fecha_alta=timezone.now(), ult_cuota_paga=timezone.now())


def _contrato(id_cliente=CID, descripcion="INSTITUTO", activo=1, id_contrato=1):
    return XsysContrato.objects.create(
        id_contrato=id_contrato, id_cliente=id_cliente, id_tipo_con=55,
        descripcion=descripcion, activo=activo)


class DeteccionTests(TestCase):
    def test_lo_encuentra(self):
        _socio()
        _contrato()
        filas = chk.sin_identificacion()
        self.assertEqual([f["id_cliente"] for f in filas], [CID])
        self.assertEqual(filas[0]["contratos"], ["INSTITUTO"])

    def test_con_documento_no(self):
        _socio(doc=30111222)
        _contrato()
        self.assertEqual(chk.sin_identificacion(), [])

    def test_con_credencial_no(self):
        """La tarjeta también lo identifica: no está sin forma de entrar."""
        _socio(cred="BC34B47D")
        _contrato()
        self.assertEqual(chk.sin_identificacion(), [])

    def test_una_credencial_de_espacios_no_cuenta(self):
        _socio(cred="   ")
        _contrato()
        self.assertEqual(len(chk.sin_identificacion()), 1)

    def test_enrolado_en_el_facial_no(self):
        """Entra por la cara: no necesita ni documento ni tarjeta."""
        _socio()
        _contrato()
        BioStarUser.objects.create(user_id=str(CID), name="FAINBERG", is_active=True)
        self.assertEqual(chk.sin_identificacion(), [])

    def test_un_facial_dado_de_baja_no_lo_salva(self):
        _socio()
        _contrato()
        BioStarUser.objects.create(user_id=str(CID), name="FAINBERG", is_active=False)
        self.assertEqual(len(chk.sin_identificacion()), 1)

    def test_sin_contrato_vigente_no_se_avisa(self):
        """Sin nada que lo habilite, que no tenga documento no le impide entrar."""
        _socio()
        self.assertEqual(chk.sin_identificacion(), [])

    def test_un_contrato_vencido_no_alcanza(self):
        _socio()
        _contrato(activo=0)
        self.assertEqual(chk.sin_identificacion(), [])

    def test_una_ficha_dada_de_baja_no_se_avisa(self):
        _socio(activo=0)
        _contrato()
        self.assertEqual(chk.sin_identificacion(), [])

    def test_las_empresas_y_colegios_quedan_afuera(self):
        """Las fichas de concesiones y colegios son la entidad, no una persona:
        no tienen documento porque no son alguien que cruce un molinete."""
        for i, tipo in enumerate(sorted(chk.CATEGORIAS_NO_PERSONA)):
            _socio(id_cliente=800000 + i, tipo=tipo, apellido="STADIO S.A.")
            _contrato(id_cliente=800000 + i, id_contrato=100 + i)
        self.assertEqual(chk.sin_identificacion(), [])

    def test_los_ultimos_cargados_van_primero(self):
        """Son los que todavía se pueden corregir antes de que alguien rebote."""
        viejo = _socio(id_cliente=700001, apellido="VIEJO")
        viejo.fecha_alta = timezone.now() - timezone.timedelta(days=900)
        viejo.save()
        _contrato(id_cliente=700001, id_contrato=7)
        _socio()
        _contrato()
        self.assertEqual([f["id_cliente"] for f in chk.sin_identificacion()], [CID, 700001])

    def test_trae_con_qué_contactarlo(self):
        s = _socio()
        s.email = "martinafainberg@yahoo.com"
        s.save()
        _contrato()
        f = chk.sin_identificacion()[0]
        self.assertEqual(f["email"], "martinafainberg@yahoo.com")
        self.assertEqual(f["categoria"], "ALUMNO IGSM")


class AvisosTests(TestCase):
    def setUp(self):
        _socio()
        _contrato()

    def test_deja_el_aviso(self):
        self.assertEqual(chk.revisar()["avisos_nuevos"], 1)
        a = SocioAviso.objects.get(id_cliente=CID)
        self.assertEqual(a.tipo, SocioAviso.TIPO_SIN_IDENTIFICACION)
        self.assertEqual(a.creado_por, "sistema")
        self.assertFalse(a.resuelto)

    def test_el_aviso_dice_qué_hacer(self):
        chk.revisar()
        texto = SocioAviso.objects.get(id_cliente=CID).texto
        self.assertIn("INSTITUTO", texto)
        self.assertIn(str(CID), texto)

    def test_correrlo_de_nuevo_no_duplica(self):
        chk.revisar()
        self.assertEqual(chk.revisar()["avisos_nuevos"], 0)
        self.assertEqual(SocioAviso.objects.count(), 1)

    def test_al_cargarle_el_documento_el_aviso_se_resuelve_solo(self):
        """Sin esto la pantalla se llena de gente ya resuelta y deja de servir."""
        chk.revisar()
        XsysSocio.objects.filter(id_cliente=CID).update(doc_nro=41915049)
        self.assertEqual(chk.revisar()["avisos_resueltos"], 1)
        a = SocioAviso.objects.get(id_cliente=CID)
        self.assertTrue(a.resuelto)
        self.assertEqual(a.resuelto_por, "sistema")

    def test_no_resuelve_avisos_de_otro_tipo(self):
        """Un aviso que dejó una persona no lo cierra el chequeo."""
        SocioAviso.objects.create(id_cliente=CID, tipo=SocioAviso.TIPO_PASE_POR_SOCIOS,
                                  texto="x", creado_por="alguien")
        XsysSocio.objects.filter(id_cliente=CID).update(doc_nro=1)
        chk.revisar()
        self.assertFalse(SocioAviso.objects.get(id_cliente=CID).resuelto)

    def test_dry_run_no_escribe(self):
        stats = chk.revisar(dry_run=True)
        self.assertEqual(stats["avisos_nuevos"], 1)
        self.assertEqual(SocioAviso.objects.count(), 0)

    def test_uno_reabierto_a_mano_no_se_vuelve_a_duplicar(self):
        chk.revisar()
        SocioAviso.objects.update(resuelto=False)
        self.assertEqual(chk.revisar()["avisos_nuevos"], 0)


class CableadoTests(TestCase):
    """Que corra solo. Un chequeo que hay que acordarse de correr no es un chequeo."""

    def test_la_sincronizacion_lo_llama(self):
        import inspect

        from xsys.services.sync import XsysSyncService

        codigo = inspect.getsource(XsysSyncService.incremental)
        self.assertIn("chequeo_identificacion", codigo)
        self.assertIn("revisar_best_effort", codigo)

    def test_si_falla_no_rompe_el_sync(self):
        with patch.object(chk, "revisar", side_effect=RuntimeError("boom")):
            stats = chk.revisar_best_effort()
        self.assertEqual(stats["detectados"], 0)

    def test_el_comando_corre(self):
        from io import StringIO

        from django.core.management import call_command

        _socio()
        _contrato()
        salida = StringIO()
        call_command("xsys_chequeo_identificacion", stdout=salida)
        self.assertIn("FAINBERG", salida.getvalue())
        self.assertEqual(SocioAviso.objects.count(), 1)

    def test_el_comando_en_dry_run_no_escribe(self):
        from io import StringIO

        from django.core.management import call_command

        _socio()
        _contrato()
        call_command("xsys_chequeo_identificacion", "--dry-run", stdout=StringIO())
        self.assertEqual(SocioAviso.objects.count(), 0)

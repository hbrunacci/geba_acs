"""Deuda de cuotas de actividades: 2 y 3 avisan, 4 frenan.

De la planilla del 25/08/2026: 47 socios con 2 o 3 cuotas impagas, que **pasan**
y el visor marca en amarillo, y 116 con 4 cuotas, que **no pasan**. El corte lo
decide xSys (``CD_Clientes_Deuda_Actividades`` + motivo 118); acá se comprueba
que el espejo, el visor y la lista blanca digan lo mismo, porque si sólo lo
supiera el molinete la gente entraría igual por el facial.
"""

from django.test import TestCase
from django.utils import timezone

from access_control.models.models import ExternalAccessLogEntry
from xsys import api_views as A
from xsys.models import XsysDeudaActividades, XsysSocio
from xsys.services.sync import XsysSyncService

CID_AVISA = 900101   # 2 o 3 cuotas: pasa, pero se marca
CID_BLOQUEA = 900102  # 4 cuotas: no pasa
CID_LIMPIO = 900103


def _socio(id_cliente, apellido="PEREZ"):
    return XsysSocio.objects.create(
        id_cliente=id_cliente, apellido=apellido, nombre="JUAN", doc_nro=id_cliente,
        categoria="CADETE", id_tipo_cli=1001, activo=1, ult_cuota_paga=timezone.now())


def _evento(id_cliente, resultado="S"):
    return ExternalAccessLogEntry(
        external_id=id_cliente, id_cliente=id_cliente, fecha=timezone.now(),
        resultado=resultado, id_acceso=14, id_controlador=59,
        observacion="Habilit. por Produc. Comprado CUOTA SOCIAL")


class EspejoTests(TestCase):
    class _Cur:
        def __init__(self, existe, filas):
            self._existe, self._filas = existe, filas

        def execute(self, sql, *a):
            self._ultimo = "OBJECT_ID" if "OBJECT_ID" in sql else "SELECT"
            return self

        def fetchone(self):
            return (1 if self._existe else None,)

        def fetchall(self):
            return self._filas

    def test_si_la_tabla_no_existe_en_xsys_no_es_un_error(self):
        self.assertEqual(XsysSyncService({}).sync_deuda_actividades(self._Cur(False, [])), 0)

    def test_espeja_las_filas(self):
        filas = [(CID_AVISA, 2, 105900, "ATLETISMO", 0, 1, "planilla_20260825", "2 cuotas", None),
                 (CID_BLOQUEA, 4, 206400, "BASQUET", 1, 1, "planilla_20260825", "4 cuotas", None)]
        self.assertEqual(XsysSyncService({}).sync_deuda_actividades(self._Cur(True, filas)), 2)
        self.assertFalse(XsysDeudaActividades.objects.get(id_cliente=CID_AVISA).bloquea)
        self.assertTrue(XsysDeudaActividades.objects.get(id_cliente=CID_BLOQUEA).bloquea)

    def test_borra_al_que_xsys_ya_no_lista(self):
        """Regularizado y sacado de la tabla: acá también deja de figurar."""
        XsysDeudaActividades.objects.create(id_cliente=999999, cuotas=4, bloquea=True)
        filas = [(CID_AVISA, 2, 1, "X", 0, 1, "x", "", None)]
        XsysSyncService({}).sync_deuda_actividades(self._Cur(True, filas))
        self.assertEqual(list(XsysDeudaActividades.objects.values_list("id_cliente", flat=True)),
                         [CID_AVISA])


class VisorTests(TestCase):
    """Los de 2 y 3 cuotas pasan; el operador tiene que verlo en amarillo."""

    def setUp(self):
        self.socios = {CID_AVISA: _socio(CID_AVISA), CID_BLOQUEA: _socio(CID_BLOQUEA, "GOMEZ"),
                       CID_LIMPIO: _socio(CID_LIMPIO, "LOPEZ")}
        XsysDeudaActividades.objects.create(id_cliente=CID_AVISA, cuotas=3, bloquea=False, activo=True)
        XsysDeudaActividades.objects.create(id_cliente=CID_BLOQUEA, cuotas=4, bloquea=True, activo=True)

    def _payload(self, cid, resultado="S"):
        deuda = A._deuda_actividades([cid])
        return A._evento_payload(_evento(cid, resultado), self.socios, set(), {}, {}, {},
                                 {}, set(), {}, {cid: "producto"}, set(), {}, deuda)

    def test_el_que_debe_2_o_3_pasa_pero_queda_marcado(self):
        p = self._payload(CID_AVISA)
        self.assertTrue(p["permitido"])
        self.assertEqual(p["estado"], "anomalia")          # amarillo
        self.assertIn("Deuda de Actividades", p["mensaje"])
        self.assertIn("3 cuota", p["mensaje"])
        self.assertEqual(p["deuda_actividades"], 3)

    def test_el_que_no_debe_pasa_limpio(self):
        p = self._payload(CID_LIMPIO)
        self.assertEqual(p["estado"], "ok")
        self.assertIsNone(p["deuda_actividades"])

    def test_un_rechazo_conserva_su_motivo(self):
        """Si no pasó, manda el motivo real: el aviso no lo tapa."""
        p = self._payload(CID_AVISA, resultado="N")
        self.assertEqual(p["estado"], "no")
        self.assertNotIn("Deuda de Actividades", p["mensaje"])

    def test_una_deuda_regularizada_no_marca(self):
        XsysDeudaActividades.objects.filter(id_cliente=CID_AVISA).update(activo=False)
        self.assertEqual(self._payload(CID_AVISA)["estado"], "ok")

    def test_el_helper_solo_trae_las_vigentes(self):
        XsysDeudaActividades.objects.filter(id_cliente=CID_BLOQUEA).update(activo=False)
        self.assertEqual(set(A._deuda_actividades([CID_AVISA, CID_BLOQUEA])), {CID_AVISA})

    def test_sin_ids_no_consulta(self):
        self.assertEqual(A._deuda_actividades([]), {})


class ValidadorLocalTests(TestCase):
    """El motivo 118 tiene que existir en las dos reimplementaciones locales.

    Si estuviera sólo en el SP, la lista blanca seguiría habilitando a los de 4
    cuotas y entrarían por el facial, que valida contra ella y no contra xSys.
    """

    def test_el_motivo_esta_en_el_validador_de_a_uno(self):
        from access_control.services import MSSQLAccessCheckService
        self.assertEqual(MSSQLAccessCheckService.MOTIVOS["deuda_actividades"],
                         (118, "Deuda de Actividades"))

    def test_el_motivo_esta_en_el_calculo_masivo(self):
        from xsys.services.whitelist_bulk import MOTIVO_KEYS
        self.assertIn("deuda_actividades", MOTIVO_KEYS)

    def test_las_dos_reimplementaciones_comparten_las_claves(self):
        from access_control.services import MSSQLAccessCheckService
        from xsys.services.whitelist_bulk import MOTIVO_KEYS
        self.assertEqual(set(MOTIVO_KEYS), set(MSSQLAccessCheckService.MOTIVOS))

    def test_el_rechazo_va_antes_que_cualquier_habilitacion(self):
        """Orden: si primero habilitara por producto, la deuda no frenaría nada."""
        import inspect

        from xsys.services import whitelist_bulk
        codigo = inspect.getsource(whitelist_bulk.compute_habilitacion_bulk)
        self.assertLess(codigo.index('res(False, "deuda_actividades")'),
                        codigo.index('res(True, "master")'))

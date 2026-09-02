"""Horarios y documentación: las dos reglas que después deciden si alguien entra."""

from datetime import date, datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from concesionarios.models import (
    Concesionario,
    Documento,
    Empresa,
    HorarioAcceso,
    HorarioFranja,
    TipoDocumento,
)
from concesionarios.services import resumen_documental


def _momento(dia_iso: str, hora: str) -> datetime:
    """Un datetime con zona local, para no pelearse con USE_TZ en las pruebas."""
    naive = datetime.strptime(f"{dia_iso} {hora}", "%Y-%m-%d %H:%M")
    return timezone.make_aware(naive, timezone.get_current_timezone())


class HorarioTests(TestCase):
    def setUp(self):
        self.h = HorarioAcceso.objects.create(nombre="Lun a Vie 9 18")
        for dia in range(5):
            HorarioFranja.objects.create(horario=self.h, dia_semana=dia,
                                         hora_desde=time(9), hora_hasta=time(18))

    def test_dentro_de_la_franja(self):
        # 2026-09-02 es miércoles.
        self.assertTrue(self.h.permite(_momento("2026-09-02", "10:00")))

    def test_el_limite_de_apertura_entra_y_el_de_cierre_no(self):
        """Las 9:00 en punto entran; las 18:00 ya no: la franja es [desde, hasta)."""
        self.assertTrue(self.h.permite(_momento("2026-09-02", "09:00")))
        self.assertFalse(self.h.permite(_momento("2026-09-02", "18:00")))

    def test_fuera_de_hora(self):
        self.assertFalse(self.h.permite(_momento("2026-09-02", "08:59")))
        self.assertFalse(self.h.permite(_momento("2026-09-02", "20:00")))

    def test_el_fin_de_semana_no_esta(self):
        self.assertFalse(self.h.permite(_momento("2026-09-05", "10:00")))  # sábado

    def test_un_horario_desactivado_no_habilita(self):
        self.h.activo = False
        self.h.save()
        self.assertFalse(self.h.permite(_momento("2026-09-02", "10:00")))

    def test_un_horario_sin_franjas_no_habilita_nada(self):
        """A medio cargar no es 'siempre': sería abrir la puerta por un descuido."""
        vacio = HorarioAcceso.objects.create(nombre="Vacío")
        self.assertFalse(vacio.permite(_momento("2026-09-02", "10:00")))

    def test_resumen_agrupa_los_dias_corridos(self):
        self.assertEqual(self.h.resumen, "Lun a Vie 09:00–18:00")

    def test_resumen_enumera_los_dias_sueltos(self):
        h = HorarioAcceso.objects.create(nombre="Sueltos")
        for dia in (0, 2, 4):
            HorarioFranja.objects.create(horario=h, dia_semana=dia,
                                         hora_desde=time(8), hora_hasta=time(12))
        self.assertEqual(h.resumen, "Lun, Mié, Vie 08:00–12:00")


class FranjaNocturnaTests(TestCase):
    """22:00–06:00 del lunes = lunes a las 22 hasta el martes a las 6."""

    def setUp(self):
        self.h = HorarioAcceso.objects.create(nombre="Nocturno lunes")
        HorarioFranja.objects.create(horario=self.h, dia_semana=0,
                                     hora_desde=time(22), hora_hasta=time(6))

    def test_la_noche_del_dia_de_la_franja(self):
        self.assertTrue(self.h.permite(_momento("2026-08-31", "23:30")))  # lunes

    def test_la_madrugada_del_dia_siguiente(self):
        self.assertTrue(self.h.permite(_momento("2026-09-01", "05:00")))  # martes

    def test_no_la_madrugada_del_mismo_dia(self):
        self.assertFalse(self.h.permite(_momento("2026-08-31", "05:00")))

    def test_no_despues_de_que_termina(self):
        self.assertFalse(self.h.permite(_momento("2026-09-01", "06:00")))

    def test_no_acepta_una_franja_de_ancho_cero(self):
        f = HorarioFranja(horario=self.h, dia_semana=1, hora_desde=time(9), hora_hasta=time(9))
        with self.assertRaises(ValidationError):
            f.full_clean()


class HorarioDelConcesionarioTests(TestCase):
    def setUp(self):
        self.empresa_horario = HorarioAcceso.objects.create(nombre="Empresa 8 a 20")
        HorarioFranja.objects.create(horario=self.empresa_horario, dia_semana=2,
                                     hora_desde=time(8), hora_hasta=time(20))
        self.propio = HorarioAcceso.objects.create(nombre="Propio 6 a 9")
        HorarioFranja.objects.create(horario=self.propio, dia_semana=2,
                                     hora_desde=time(6), hora_hasta=time(9))
        self.empresa = Empresa.objects.create(nombre="Buffet", horario=self.empresa_horario)

    def test_sin_horario_propio_rige_el_de_la_empresa(self):
        c = Concesionario.objects.create(id_cliente=1, empresa=self.empresa)
        self.assertEqual(c.horario_vigente, self.empresa_horario)
        self.assertTrue(c.permite_horario(_momento("2026-09-02", "19:00")))

    def test_el_horario_propio_le_gana_al_de_la_empresa(self):
        c = Concesionario.objects.create(id_cliente=2, empresa=self.empresa, horario=self.propio)
        self.assertEqual(c.horario_vigente, self.propio)
        self.assertTrue(c.permite_horario(_momento("2026-09-02", "07:00")))
        self.assertFalse(c.permite_horario(_momento("2026-09-02", "19:00")))

    def test_sin_ningun_horario_no_hay_restriccion(self):
        """No tener horario cargado no es 'no puede entrar': es que no se restringe."""
        empresa = Empresa.objects.create(nombre="Sin horario")
        c = Concesionario.objects.create(id_cliente=3, empresa=empresa)
        self.assertIsNone(c.horario_vigente)
        self.assertTrue(c.permite_horario(_momento("2026-09-06", "03:00")))


class DocumentoTests(TestCase):
    def setUp(self):
        self.hoy = date(2026, 9, 1)
        self.art = TipoDocumento.objects.create(
            codigo="t_art", nombre="ART", dias_aviso=30, bloquea_acceso=True)
        self.folleto = TipoDocumento.objects.create(
            codigo="t_nota", nombre="Nota", requiere_vencimiento=False)

    def _doc(self, dias, tipo=None, id_cliente=100):
        return Documento.objects.create(
            id_cliente=id_cliente, tipo=tipo or self.art,
            fecha_vencimiento=self.hoy + timedelta(days=dias))

    def test_estados_segun_el_vencimiento(self):
        self.assertEqual(self._doc(-1).estado(self.hoy), Documento.VENCIDO)
        self.assertEqual(self._doc(0).estado(self.hoy), Documento.POR_VENCER)
        self.assertEqual(self._doc(30).estado(self.hoy), Documento.POR_VENCER)
        self.assertEqual(self._doc(31).estado(self.hoy), Documento.VIGENTE)

    def test_sin_fecha_no_vence(self):
        doc = Documento.objects.create(id_cliente=100, tipo=self.folleto)
        self.assertEqual(doc.estado(self.hoy), Documento.SIN_VENCIMIENTO)
        self.assertIsNone(doc.dias_para_vencer(self.hoy))

    def test_el_aviso_sale_del_tipo(self):
        corto = TipoDocumento.objects.create(codigo="t_corto", nombre="Corto", dias_aviso=5)
        self.assertEqual(self._doc(10, corto).estado(self.hoy), Documento.VIGENTE)
        self.assertEqual(self._doc(4, corto).estado(self.hoy), Documento.POR_VENCER)

    def test_no_acepta_vencimiento_anterior_a_la_emision(self):
        doc = Documento(id_cliente=1, tipo=self.art,
                        fecha_emision=date(2026, 5, 1), fecha_vencimiento=date(2026, 4, 1))
        with self.assertRaises(ValidationError):
            doc.full_clean()

    def test_exige_vencimiento_cuando_el_tipo_lo_pide(self):
        with self.assertRaises(ValidationError):
            Documento(id_cliente=1, tipo=self.art).full_clean()


class ResumenDocumentalTests(TestCase):
    """Lo que alimenta la columna 'vencimiento más próximo' del listado."""

    def setUp(self):
        self.hoy = date(2026, 9, 1)
        self.art = TipoDocumento.objects.create(
            codigo="t_art", nombre="ART", dias_aviso=30, bloquea_acceso=True)
        self.seguro = TipoDocumento.objects.create(
            codigo="t_seg", nombre="Seguro", dias_aviso=30, bloquea_acceso=False)

    def _doc(self, cid, dias, tipo=None):
        return Documento.objects.create(id_cliente=cid, tipo=tipo or self.seguro,
                                        fecha_vencimiento=self.hoy + timedelta(days=dias))

    def test_lo_vencido_gana_sobre_lo_que_esta_por_vencer(self):
        self._doc(1, -2)
        self._doc(1, 3)
        fila = resumen_documental([1], self.hoy)[1]
        self.assertEqual(fila["estado"], Documento.VENCIDO)
        self.assertEqual(fila["proximo"]["dias"], -2)

    def test_entre_dos_vencidos_gana_el_mas_viejo(self):
        self._doc(1, -2)
        self._doc(1, -40)
        self.assertEqual(resumen_documental([1], self.hoy)[1]["proximo"]["dias"], -40)

    def test_entre_dos_por_vencer_gana_el_mas_cercano(self):
        self._doc(1, 20)
        self._doc(1, 5)
        self.assertEqual(resumen_documental([1], self.hoy)[1]["proximo"]["dias"], 5)

    def test_lo_vigente_no_aparece_como_proximo(self):
        self._doc(1, 200)
        fila = resumen_documental([1], self.hoy)[1]
        self.assertIsNone(fila["proximo"])
        self.assertEqual(fila["estado"], Documento.VIGENTE)
        self.assertEqual(fila["total"], 1)

    def test_solo_bloquea_el_tipo_que_habilita_el_ingreso(self):
        self._doc(1, -1, self.seguro)
        self.assertFalse(resumen_documental([1], self.hoy)[1]["bloqueado"])
        self._doc(1, -1, self.art)
        self.assertTrue(resumen_documental([1], self.hoy)[1]["bloqueado"])

    def test_un_documento_vencido_de_otro_no_ensucia_la_fila(self):
        self._doc(1, -5)
        self._doc(2, 100)
        resumen = resumen_documental([1, 2], self.hoy)
        self.assertEqual(resumen[1]["vencidos"], 1)
        self.assertEqual(resumen[2]["vencidos"], 0)
        self.assertIsNone(resumen[2]["proximo"])

    def test_sin_documentos_devuelve_la_fila_igual(self):
        fila = resumen_documental([99], self.hoy)[99]
        self.assertEqual(fila["total"], 0)
        self.assertIsNone(fila["estado"])

    def test_sin_ids_no_consulta(self):
        self.assertEqual(resumen_documental([]), {})

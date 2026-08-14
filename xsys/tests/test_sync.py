from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from xsys.models import SyncState, XsysNovedad, XsysSocio, XsysSocioFoto, XsysWhitelist
from xsys.services.sync import SOCIO_COLUMNS, XsysSyncService


def _socio_row(id_cliente=944426, apellido="SIMOUR ", cred="BCB30514  "):
    # 16 valores en el orden de SOCIO_COLUMNS
    values = {
        "Id_Cliente": id_cliente,
        "Doc_Nro": 31850936,
        "Apellido": apellido,
        "Nombre": "GERMAN",
        "Razon_Social": "SIMOUR GERMAN",
        "Sexo": "M",
        "Fecha_Nac": datetime(1985, 5, 10, 0, 0, 0),
        "Email": "  g@x.com ",
        "Activo": 1,
        "Tipo_Persona": "F",
        "Credencial_Nro": cred,
        "Ult_Cuota_Paga": datetime(2026, 8, 1, 0, 0, 0),
        "Id_Estado_Cliente": 1,
        "Id_Cliente_Externo": "EXT1",
        "Fecha_Alta": datetime(2020, 1, 1, 0, 0, 0),
        "Fecha_Baja": None,
        "Id_Cliente_Ref": None,
    }
    return tuple(values[col] for col, _ in SOCIO_COLUMNS)


class FakeCursor:
    def __init__(self, novedades=None, socios=None, fotos=None, maxes=None):
        self.novedades = novedades or []
        self.socios = socios or []
        self.fotos = fotos or []
        self.maxes = maxes or {}
        self._last = ""
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        self._last = sql
        return self

    def fetchall(self):
        if "CD_Clientes_Novedades" in self._last:
            return self.novedades
        if "FROM Clientes C" in self._last and "Id_Cliente IN" in self._last:
            return self.socios
        if "FROM Clientes_Fotos" in self._last:
            return self.fotos
        return []

    def fetchmany(self, n):
        return []

    def fetchone(self):
        for kw, val in self.maxes.items():
            if kw in self._last:
                return (val,)
        return (None,)


class SocioMappingTests(TestCase):
    def test_row_to_socio_kwargs_normaliza(self):
        svc = XsysSyncService()
        kwargs = svc._row_to_socio_kwargs(_socio_row())
        self.assertEqual(kwargs["apellido"], "SIMOUR")       # trim
        self.assertEqual(kwargs["credencial_nro"], "BCB30514")
        self.assertEqual(kwargs["email"], "g@x.com")
        self.assertTrue(timezone.is_aware(kwargs["fecha_nac"]))  # naive -> aware

    def test_upsert_socios_crea_y_actualiza(self):
        svc = XsysSyncService()
        self.assertEqual(svc._upsert_socios([_socio_row(apellido="A")]), 1)
        self.assertEqual(XsysSocio.objects.get(pk=944426).apellido, "A")
        svc._upsert_socios([_socio_row(apellido="B")])  # mismo id -> update
        self.assertEqual(XsysSocio.objects.count(), 1)
        self.assertEqual(XsysSocio.objects.get(pk=944426).apellido, "B")


class FotoUpsertTests(TestCase):
    def test_upsert_foto_detecta_cambios(self):
        svc = XsysSyncService()
        self.assertTrue(svc._upsert_foto(1, 1, None, b"abc"))   # nueva
        self.assertFalse(svc._upsert_foto(1, 1, None, b"abc"))  # igual -> skip
        self.assertTrue(svc._upsert_foto(1, 1, None, b"xyz"))   # cambió
        foto = XsysSocioFoto.objects.get(id_cliente=1, nro=1)
        self.assertEqual(bytes(foto.imagen), b"xyz")
        self.assertTrue(foto.sha256)


class IncrementalTests(TestCase):
    @contextmanager
    def _fake_cursor_cm(self, cursor):
        yield cursor

    def test_incremental_avanza_highwater_y_es_idempotente(self):
        svc = XsysSyncService()
        cursor = FakeCursor(
            novedades=[(1001, 944426, datetime(2026, 7, 18), "P", "M", "cambio")],
            socios=[_socio_row()],
            fotos=[],
        )
        with patch("xsys.services.sync.xsys_cursor", return_value=self._fake_cursor_cm(cursor)), \
             patch.object(XsysSyncService, "recompute_whitelist", return_value=1) as rc, \
             patch.object(XsysSyncService, "sync_movements", return_value=0):
            stats = svc.incremental()

        self.assertEqual(stats["novedades"], 1)
        self.assertEqual(stats["socios"], 1)
        self.assertEqual(SyncState.get("novedades").last_id, 1001)
        self.assertTrue(XsysSocio.objects.filter(pk=944426).exists())
        self.assertTrue(XsysNovedad.objects.filter(pk=1001).exists())
        rc.assert_called_once()

        # Segunda corrida sin novedades nuevas -> no-op
        cursor2 = FakeCursor(novedades=[])
        with patch("xsys.services.sync.xsys_cursor", return_value=self._fake_cursor_cm(cursor2)), \
             patch.object(XsysSyncService, "recompute_whitelist", return_value=0), \
             patch.object(XsysSyncService, "sync_movements", return_value=0):
            stats2 = svc.incremental()
        self.assertEqual(stats2["novedades"], 0)
        self.assertEqual(SyncState.get("novedades").last_id, 1001)

    def test_sync_movements_lee_por_highwater_y_persiste(self):
        from access_control.models.models import ExternalAccessLogEntry

        svc = XsysSyncService()
        SyncState.advance("cd_es", last_id=8000)

        class MovCursor(FakeCursor):
            def __init__(self):
                super().__init__()
                self._served = False

            def fetchmany(self, n):
                if "FROM CD_ES" in self._last and not self._served:
                    self._served = True
                    return [(
                        8534484, "E", "F", "311363", 909405,
                        datetime(2026, 7, 18, 9, 19, 36), "S", 68, 22,
                        "  obs  ", "reg", 0, "0", None, None,
                    )]
                return []

        cursor = MovCursor()
        total = svc.sync_movements(cursor)
        self.assertEqual(total, 1)
        sql = cursor.executed[0][0]
        self.assertIn("Id_ES > ?", sql)
        self.assertEqual(cursor.executed[0][1], (8000,))
        entry = ExternalAccessLogEntry.objects.get(external_id=8534484)
        self.assertEqual(entry.id_acceso, 22)
        self.assertEqual(entry.observacion, "obs")  # trim
        self.assertEqual(SyncState.get("cd_es").last_id, 8534484)

    def test_read_novedades_usa_highwater(self):
        svc = XsysSyncService()
        cursor = FakeCursor(novedades=[])
        svc.read_novedades(cursor, 500)
        sql, params = cursor.executed[-1]
        self.assertIn("Id_Novedad > ?", sql)
        self.assertNotIn("Estado", sql.split("WHERE")[1])  # no filtra por Estado
        self.assertEqual(params, (500,))

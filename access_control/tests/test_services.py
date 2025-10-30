import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from access_control.models import ExternalAccessLogEntry
from access_control.services import (
    ExternalAccessLogError,
    ExternalAccessLogService,
    ExternalAccessLogSynchronizer,
)


class ExternalAccessLogServiceTestCase(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.config = {
            "ENABLED": True,
            "HOST": "192.168.0.6",
            "PORT": 1433,
            "DATABASE": "xsys_geba",
            "USER": "sa",
            "PASSWORD": "secret",
            "TABLE": "CD_ES",
            "DRIVER": "{ODBC Driver 18 for SQL Server}",
            "DEFAULT_LIMIT": 5,
        }

    def _install_pyodbc_stub(self, connect_mock: MagicMock) -> None:
        from access_control import services

        original = getattr(services, "pyodbc", None)
        services.pyodbc = SimpleNamespace(connect=connect_mock)
        self.addCleanup(lambda: setattr(services, "pyodbc", original))

    def test_requires_enabled_flag(self):
        config = self.config | {"ENABLED": False}
        with self.assertRaises(ExternalAccessLogError):
            ExternalAccessLogService(config)

    def test_requires_pyodbc(self):
        from access_control import services

        original = getattr(services, "pyodbc", None)
        services.pyodbc = None
        self.addCleanup(lambda: setattr(services, "pyodbc", original))

        with self.assertRaises(ExternalAccessLogError):
            ExternalAccessLogService(self.config)

    def test_missing_config_parameter(self):
        config = self.config.copy()
        del config["HOST"]
        from access_control import services

        original = getattr(services, "pyodbc", None)
        services.pyodbc = SimpleNamespace(connect=MagicMock())
        self.addCleanup(lambda: setattr(services, "pyodbc", original))

        with self.assertRaises(ExternalAccessLogError):
            ExternalAccessLogService(config)

    def test_fetch_latest_builds_query_and_serializes(self):
        connect_mock = MagicMock()
        cursor_mock = MagicMock()
        sample_datetime = datetime(2016, 7, 15, 16, 11, 16, 800000)
        cursor_mock.fetchall.return_value = [
            (
                1,
                "E",
                "A",
                "B4C7BD56",
                0,
                sample_datetime,
                "E",
                1,
                1,
                "Observacion",
                "REG",
                None,
                None,
                None,
                None,
            )
        ]
        connect_mock.return_value.cursor.return_value = cursor_mock

        self._install_pyodbc_stub(connect_mock)

        service = ExternalAccessLogService(self.config)
        results = service.fetch_latest()

        self.assertEqual(len(results), 1)
        entry = results[0]
        self.assertEqual(entry["id_es"], 1)
        self.assertEqual(entry["fecha"], sample_datetime.isoformat())
        expected_query = (
            "SELECT TOP 5 Id_ES, Tipo, Origen, Id_Tarjeta, Id_Cliente, Fecha, Resultado, "
            "Id_Controlador, Id_Acceso, Observacion, tipo_reg, Id_CD_Motivo, "
            "Flag_Permite_Paso, Fecha_Paso_Permitido, Id_Controlador_Paso_Permitido "
            "FROM CD_ES ORDER BY Fecha DESC"
        )
        cursor_mock.execute.assert_called_once_with(expected_query)
        connect_mock.return_value.close.assert_called_once()

    def test_fetch_latest_with_custom_limit(self):
        connect_mock = MagicMock()
        cursor_mock = MagicMock()
        cursor_mock.fetchall.return_value = []
        connect_mock.return_value.cursor.return_value = cursor_mock

        self._install_pyodbc_stub(connect_mock)

        service = ExternalAccessLogService(self.config)
        service.fetch_latest(limit=2)

        expected_query = (
            "SELECT TOP 2 Id_ES, Tipo, Origen, Id_Tarjeta, Id_Cliente, Fecha, Resultado, "
            "Id_Controlador, Id_Acceso, Observacion, tipo_reg, Id_CD_Motivo, "
            "Flag_Permite_Paso, Fecha_Paso_Permitido, Id_Controlador_Paso_Permitido "
            "FROM CD_ES ORDER BY Fecha DESC"
        )
        cursor_mock.execute.assert_called_once_with(expected_query)

    def test_fetch_latest_with_invalid_limit(self):
        from access_control import services

        original = getattr(services, "pyodbc", None)
        services.pyodbc = SimpleNamespace(connect=MagicMock())
        self.addCleanup(lambda: setattr(services, "pyodbc", original))

        service = ExternalAccessLogService(self.config)
        with self.assertRaises(ExternalAccessLogError):
            service.fetch_latest(limit=0)

    def test_fetch_latest_handles_connection_error(self):
        connect_mock = MagicMock(side_effect=RuntimeError("no driver"))
        self._install_pyodbc_stub(connect_mock)

        service = ExternalAccessLogService(self.config)
        with self.assertRaises(ExternalAccessLogError) as exc:
            service.fetch_latest()
        self.assertIn("No se pudo establecer la conexión", str(exc.exception))

    def test_fetch_latest_handles_query_error(self):
        connect_mock = MagicMock()
        cursor_mock = MagicMock()
        cursor_mock.execute.side_effect = RuntimeError("bad query")
        connect_mock.return_value.cursor.return_value = cursor_mock

        self._install_pyodbc_stub(connect_mock)

        service = ExternalAccessLogService(self.config)
        with self.assertRaises(ExternalAccessLogError) as exc:
            service.fetch_latest()
        self.assertIn("Error al ejecutar la consulta", str(exc.exception))
        connect_mock.return_value.close.assert_called()


class ExternalAccessLogSynchronizerTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = MagicMock()
        self.synchronizer = ExternalAccessLogSynchronizer(
            self.service, limit=5, poll_interval=0.01
        )

    def test_sync_once_fetches_entries(self):
        self.service.fetch_latest.return_value = []

        result = asyncio.run(self.synchronizer.sync_once())

        self.service.fetch_latest.assert_called_once_with(limit=5)
        self.assertEqual(result, 0)

    def test_persist_entries_creates_records(self):
        now = timezone.now()
        synced = self.synchronizer._persist_entries(
            [
                {
                    "id_es": 1,
                    "tipo": "E",
                    "origen": "A",
                    "id_tarjeta": "B4C7BD56",
                    "id_cliente": 100738,
                    "fecha": now.isoformat(),
                    "resultado": "S",
                    "id_controlador": 1,
                    "id_acceso": 1,
                    "observacion": "Ingreso sincronizado",
                    "tipo_registro": "REG",
                }
            ]
        )

        self.assertEqual(synced, 1)
        entry = ExternalAccessLogEntry.objects.get(external_id=1)
        self.assertEqual(entry.observacion, "Ingreso sincronizado")
        self.assertEqual(entry.tipo, "E")

    def test_persist_entries_updates_existing_entries(self):
        existing = ExternalAccessLogEntry.objects.create(
            external_id=2,
            tipo="E",
            origen="A",
            id_tarjeta="CARD",
            fecha=timezone.now() - timezone.timedelta(hours=1),
            resultado="S",
        )
        synced = self.synchronizer._persist_entries(
            [
                {
                    "id_es": 2,
                    "tipo": "E",
                    "origen": "A",
                    "id_tarjeta": "CARD",
                    "fecha": timezone.now().isoformat(),
                    "resultado": "N",
                    "observacion": "Actualizado",
                }
            ]
        )

        self.assertEqual(synced, 1)
        entry = ExternalAccessLogEntry.objects.get(pk=existing.pk)
        self.assertEqual(entry.observacion, "Actualizado")
        self.assertEqual(entry.resultado, "N")

    def test_persist_entries_ignores_entries_without_identifier(self):
        synced = self.synchronizer._persist_entries(
            [
                {
                    "tipo": "E",
                    "fecha": timezone.now().isoformat(),
                }
            ]
        )

        self.assertEqual(synced, 0)
        self.assertEqual(ExternalAccessLogEntry.objects.count(), 0)

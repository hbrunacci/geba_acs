from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from access_control.services import AccessCheckError, MSSQLAccessCheckService


class _FakeCursor:
    """Cursor de prueba que responde según palabras clave presentes en el SQL ejecutado,
    en vez de depender del orden exacto de llamadas (más resistente a refactors)."""

    def __init__(self, responses: dict[str, tuple | None]) -> None:
        self._responses = responses
        self.executed: list[tuple[str, tuple]] = []
        self._last_sql = ""

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        self._last_sql = sql
        return self

    def fetchone(self):
        for keyword, row in self._responses.items():
            if keyword in self._last_sql:
                return row
        return None


class MSSQLAccessCheckServiceTestCase(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.config = {
            "ENABLED": True,
            "HOST": "192.168.0.6",
            "PORT": 49331,
            "DATABASE": "xsys_geba",
            "USER": "sa",
            "PASSWORD": "secret",
            "DRIVER": "{ODBC Driver 18 for SQL Server}",
        }

    def _install_pyodbc_stub(self, cursor: _FakeCursor) -> None:
        from access_control.services import services as svc_module

        connect_mock = MagicMock()
        connect_mock.return_value.cursor.return_value = cursor
        original = getattr(svc_module, "pyodbc", None)
        svc_module.pyodbc = SimpleNamespace(connect=connect_mock)
        self.addCleanup(lambda: setattr(svc_module, "pyodbc", original))

    def _base_responses(self, **overrides) -> dict[str, tuple | None]:
        responses = {
            "FROM CD_Accesos WHERE": (18, "San Martin Auto", 1, 0, 0),  # Activo=1, UCP flag=0, Evento=0
            "SELECT Id_Cliente FROM Clientes WHERE Id_Cliente": (831446,),
            "SELECT TOP 1 Id_Cliente FROM Clientes WHERE Doc_Nro": (831446,),
            "Credencial_Nro": (831446,),
            "Razon_Social": ("MILLARENGO ORIANA", 1, 1002, 0),  # Activo=1, Id_Cliente_Ref=0
            "CF_SCA_ValidarVencimientosPersona": ("",),
            "CF_SCA_ValidarMaster": (0,),
            "CF_SCA_ValidarUltCuotaPaga": (0,),
            "CF_SCA_ValidarContratosTipos": (0,),
            "CF_SCA_ValidarTipo": (0,),
            "Cbtes_Items CI": None,  # sin producto habilitante
        }
        responses.update(overrides)
        return responses

    def test_requires_enabled_flag(self):
        config = self.config | {"ENABLED": False}
        with self.assertRaises(AccessCheckError):
            MSSQLAccessCheckService(config)

    def test_requires_pyodbc(self):
        from access_control.services import services as svc_module

        original = getattr(svc_module, "pyodbc", None)
        svc_module.pyodbc = None
        self.addCleanup(lambda: setattr(svc_module, "pyodbc", original))

        with self.assertRaises(AccessCheckError):
            MSSQLAccessCheckService(self.config)

    def test_missing_config_parameter(self):
        config = self.config.copy()
        del config["HOST"]
        with self.assertRaises(AccessCheckError):
            MSSQLAccessCheckService(config)

    def test_requires_identifier_or_door(self):
        cursor = _FakeCursor(self._base_responses())
        self._install_pyodbc_stub(cursor)
        service = MSSQLAccessCheckService(self.config)
        with self.assertRaises(AccessCheckError):
            service.check_access(identifier_type="id_cliente", identifier_value="831446")

    def test_client_not_found(self):
        cursor = _FakeCursor(
            self._base_responses(**{"SELECT Id_Cliente FROM Clientes WHERE Id_Cliente": None})
        )
        self._install_pyodbc_stub(cursor)
        service = MSSQLAccessCheckService(self.config)
        result = service.check_access(identifier_type="id_cliente", identifier_value="999999", id_acceso=18)
        self.assertFalse(result["found"])

    def test_rejects_inactive_person(self):
        cursor = _FakeCursor(
            self._base_responses(**{"Razon_Social": ("MILLARENGO ORIANA", 0, 1002, 0)})
        )
        self._install_pyodbc_stub(cursor)
        service = MSSQLAccessCheckService(self.config)
        result = service.check_access(identifier_type="id_cliente", identifier_value="831446", id_acceso=18)
        self.assertFalse(result["can_enter"])
        self.assertEqual(result["motivo_code"], 104)

    def test_rejects_vencimiento(self):
        cursor = _FakeCursor(
            self._base_responses(**{
                "CF_SCA_ValidarVencimientosPersona": ("01",),
                "Clientes_Venc_Tipos": ("CUOTA SOCIAL",),
            })
        )
        self._install_pyodbc_stub(cursor)
        service = MSSQLAccessCheckService(self.config)
        result = service.check_access(identifier_type="id_cliente", identifier_value="831446", id_acceso=18)
        self.assertFalse(result["can_enter"])
        self.assertEqual(result["motivo_code"], 105)

    def test_enables_by_master(self):
        cursor = _FakeCursor(self._base_responses(**{"CF_SCA_ValidarMaster": (1,)}))
        self._install_pyodbc_stub(cursor)
        service = MSSQLAccessCheckService(self.config)
        result = service.check_access(identifier_type="id_cliente", identifier_value="831446", id_acceso=18)
        self.assertTrue(result["can_enter"])
        self.assertEqual(result["motivo_code"], 202)

    def test_enables_by_producto_comprado(self):
        cursor = _FakeCursor(
            self._base_responses(**{"Cbtes_Items CI": ("CS", "CUOTA SOCIAL")})
        )
        self._install_pyodbc_stub(cursor)
        service = MSSQLAccessCheckService(self.config)
        result = service.check_access(identifier_type="id_cliente", identifier_value="831446", id_acceso=22)
        self.assertTrue(result["can_enter"])
        self.assertEqual(result["motivo_code"], 206)
        self.assertEqual(result["detalle"], "CUOTA SOCIAL")

    def test_no_habilitacion_reproduces_112(self):
        cursor = _FakeCursor(self._base_responses())
        self._install_pyodbc_stub(cursor)
        service = MSSQLAccessCheckService(self.config)
        result = service.check_access(identifier_type="id_cliente", identifier_value="831446", id_acceso=18)
        self.assertFalse(result["can_enter"])
        self.assertEqual(result["motivo_code"], 112)

    def test_rejects_event_access_as_unsupported(self):
        cursor = _FakeCursor(
            self._base_responses(**{"FROM CD_Accesos WHERE": (30, "Evento X", 1, 0, 1)})
        )
        self._install_pyodbc_stub(cursor)
        service = MSSQLAccessCheckService(self.config)
        with self.assertRaises(AccessCheckError):
            service.check_access(identifier_type="id_cliente", identifier_value="831446", id_acceso=30)

    def test_invalid_identifier_type(self):
        cursor = _FakeCursor(self._base_responses())
        self._install_pyodbc_stub(cursor)
        service = MSSQLAccessCheckService(self.config)
        with self.assertRaises(AccessCheckError):
            service.check_access(identifier_type="bogus", identifier_value="x", id_acceso=18)

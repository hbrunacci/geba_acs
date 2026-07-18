from types import SimpleNamespace

from django.test import SimpleTestCase

from xsys.services import mssql
from xsys.services.mssql import XsysConnectionError, xsys_connection_string


BASE_CONFIG = {
    "ENABLED": True,
    "HOST": "192.168.0.6",
    "PORT": 49331,
    "DATABASE": "xsys_geba",
    "USER": "sa",
    "PASSWORD": "secret",
    "DRIVER": "{ODBC Driver 18 for SQL Server}",
    "ENCRYPT": "no",
    "TRUST_SERVER_CERTIFICATE": "yes",
    "LOGIN_TIMEOUT": 15,
}


class XsysConnectionStringTests(SimpleTestCase):
    def test_incluye_parametros_load_bearing(self):
        cs = xsys_connection_string(BASE_CONFIG)
        self.assertIn("SERVER=192.168.0.6,49331", cs)
        self.assertIn("DATABASE=xsys_geba", cs)
        # Sin estos dos el handshake TLS contra el 49331 falla.
        self.assertIn("Encrypt=no", cs)
        self.assertIn("TrustServerCertificate=yes", cs)
        self.assertIn("LoginTimeout=15", cs)

    def test_server_sin_puerto(self):
        cfg = dict(BASE_CONFIG)
        cfg["PORT"] = None
        cs = xsys_connection_string(cfg)
        self.assertIn("SERVER=192.168.0.6;", cs)


class XsysConnectValidationTests(SimpleTestCase):
    def test_deshabilitado_lanza_error(self):
        cfg = dict(BASE_CONFIG, ENABLED=False)
        with self.assertRaises(XsysConnectionError):
            mssql.connect(cfg)

    def test_sin_pyodbc_lanza_error(self):
        original = mssql.pyodbc
        mssql.pyodbc = None
        self.addCleanup(lambda: setattr(mssql, "pyodbc", original))
        with self.assertRaises(XsysConnectionError):
            mssql.connect(BASE_CONFIG)

    def test_faltan_parametros(self):
        original = mssql.pyodbc
        mssql.pyodbc = SimpleNamespace(connect=lambda *a, **k: None)
        self.addCleanup(lambda: setattr(mssql, "pyodbc", original))
        cfg = dict(BASE_CONFIG)
        cfg["HOST"] = ""
        with self.assertRaises(XsysConnectionError):
            mssql.connect(cfg)

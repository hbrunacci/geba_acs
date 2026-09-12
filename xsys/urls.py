from django.urls import path

from xsys.views import (
    xsys_diagnostico,
    xsys_molinetes_config,
    xsys_puerta_monitor,
    xsys_socio_console,
    xsys_socios_fichas,
    xsys_tableros,
)

urlpatterns = [
    path("xsys/tableros/", xsys_tableros, name="xsys_tableros"),
    path("xsys/fichas/", xsys_socios_fichas, name="xsys_socios_fichas"),
    path("xsys/socios/", xsys_socio_console, name="xsys_socio_console"),
    path("xsys/diagnostico/", xsys_diagnostico, name="xsys_diagnostico"),
    path("xsys/puerta/", xsys_puerta_monitor, name="xsys_puerta_monitor"),
    path("xsys/molinetes/", xsys_molinetes_config, name="xsys_molinetes_config"),
]

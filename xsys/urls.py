from django.urls import path

from xsys.views import (
    xsys_diagnostico,
    xsys_molinetes_config,
    xsys_puerta_monitor,
    xsys_socio_console,
)

urlpatterns = [
    path("xsys/socios/", xsys_socio_console, name="xsys_socio_console"),
    path("xsys/diagnostico/", xsys_diagnostico, name="xsys_diagnostico"),
    path("xsys/puerta/", xsys_puerta_monitor, name="xsys_puerta_monitor"),
    path("xsys/molinetes/", xsys_molinetes_config, name="xsys_molinetes_config"),
]

from django.urls import path

from xsys.views import xsys_puerta_monitor, xsys_socio_console

urlpatterns = [
    path("xsys/socios/", xsys_socio_console, name="xsys_socio_console"),
    path("xsys/puerta/", xsys_puerta_monitor, name="xsys_puerta_monitor"),
]

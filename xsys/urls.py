from django.urls import path

from xsys.views import xsys_socio_console

urlpatterns = [
    path("xsys/socios/", xsys_socio_console, name="xsys_socio_console"),
]

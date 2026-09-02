from django.urls import path

from concesionarios.views import (
    concesionarios_empresas,
    concesionarios_horarios,
    concesionarios_ingresos,
    concesionarios_listado,
)

urlpatterns = [
    path("concesionarios/", concesionarios_listado, name="concesionarios_listado"),
    path("concesionarios/ingresos/", concesionarios_ingresos, name="concesionarios_ingresos"),
    path("concesionarios/empresas/", concesionarios_empresas, name="concesionarios_empresas"),
    path("concesionarios/horarios/", concesionarios_horarios, name="concesionarios_horarios"),
]

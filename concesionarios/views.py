from django.shortcuts import render

from common.roles import concesionarios_requerido


@concesionarios_requerido
def concesionarios_listado(request):
    """Listado de concesionarios: persona, empresa y documento más urgente."""
    return render(request, "concesionarios/listado.html")


@concesionarios_requerido
def concesionarios_ingresos(request):
    """Quiénes entraron al club, con foto, empresa, hora y resultado."""
    return render(request, "concesionarios/ingresos.html")


@concesionarios_requerido
def concesionarios_empresas(request):
    """Empresas concesionarias y tipos de documento."""
    return render(request, "concesionarios/empresas.html")


@concesionarios_requerido
def concesionarios_horarios(request):
    """Horarios de ingreso: una recurrencia con nombre y sus franjas."""
    return render(request, "concesionarios/horarios.html")

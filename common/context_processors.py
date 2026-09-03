"""Contexto compartido con todos los templates (hoy: permisos del sidebar)."""

from __future__ import annotations

from common.roles import es_admin, puede_concesionarios, puede_config_puertas, puede_socios


def nav_roles(request):
    """Expone al sidebar qué ítems puede ver el usuario según su rol.

    - ``nav_is_admin``: ve TODAS las opciones administrativas.
    - ``nav_can_puertas``: ve la config de molinetes por puerta y el visor.
    - ``nav_can_concesionarios``: ve la administración de concesionarios.
    - ``nav_can_socios``: ve los avisos a socios (grupo ``socios`` o puertas).
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav_is_admin": False, "nav_can_puertas": False,
                "nav_can_concesionarios": False, "nav_can_socios": False}
    return {
        "nav_is_admin": es_admin(user),
        "nav_can_puertas": puede_config_puertas(user),
        "nav_can_concesionarios": puede_concesionarios(user),
        "nav_can_socios": puede_socios(user),
    }

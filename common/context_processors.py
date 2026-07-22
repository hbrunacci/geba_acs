"""Context processors de la app común."""

from __future__ import annotations

from common.roles import es_admin, puede_config_puertas


def nav_roles(request):
    """Expone al sidebar qué ítems puede ver el usuario según su rol.

    - ``nav_is_admin``: ve TODAS las opciones administrativas.
    - ``nav_can_puertas``: ve la config de molinetes por puerta y el visor.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav_is_admin": False, "nav_can_puertas": False}
    return {
        "nav_is_admin": es_admin(user),
        "nav_can_puertas": puede_config_puertas(user),
    }

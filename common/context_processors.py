"""Contexto compartido con todos los templates: el menú lateral y los permisos."""

from __future__ import annotations

from common.nav import construir as construir_menu
from common.roles import (
    es_admin,
    puede_concesionarios,
    puede_config_puertas,
    puede_socios,
    puede_tableros,
)


def nav_roles(request):
    """Arma el menú lateral y expone los permisos que aún usan los templates.

    ``nav_menu`` es lo que dibuja el sidebar: la estructura de ``common.nav``
    ya filtrada por rol y con el ítem actual marcado.

    Las banderas ``nav_can_*`` siguen porque hay templates que muestran u ocultan
    cosas fuera del menú (botones dentro de una pantalla, por ejemplo) y no
    tienen por qué recorrer el menú para saber si el usuario es admin:

    - ``nav_is_admin``: ve TODAS las opciones administrativas.
    - ``nav_can_puertas``: ve la config de molinetes por puerta y el visor.
    - ``nav_can_concesionarios``: ve la administración de concesionarios.
    - ``nav_can_socios``: ve las fichas y los avisos a socios.
    - ``nav_can_tableros``: ve los tableros de gestión.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav_menu": [], "nav_is_admin": False, "nav_can_puertas": False,
                "nav_can_concesionarios": False, "nav_can_socios": False,
                "nav_can_tableros": False}

    # ``resolver_match`` es None en las páginas de error, que igual heredan de
    # base.html: sin este guard, un 404 de un usuario logueado rompe con
    # AttributeError y tapa el error original.
    match = getattr(request, "resolver_match", None)
    url_actual = getattr(match, "url_name", "") or ""

    return {
        "nav_menu": construir_menu(user, url_actual),
        "nav_is_admin": es_admin(user),
        "nav_can_puertas": puede_config_puertas(user),
        "nav_can_concesionarios": puede_concesionarios(user),
        "nav_can_socios": puede_socios(user),
        "nav_can_tableros": puede_tableros(user),
    }

"""Roles de la aplicación basados en grupos de Django.

Además del superusuario (que siempre puede todo), la app maneja dos roles por
grupo:

- ``Administrador``: acceso total a todas las pantallas del sistema.
- ``Configuración de Puertas``: solo la configuración de molinetes/controladores
  por puerta y el visor (monitor) de puerta.

Se ofrecen helpers para chequear el rol, decoradores para vistas función y una
``permission_class`` de DRF para las APIs de configuración.
"""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

GRUPO_ADMIN = "Administrador"
GRUPO_PUERTAS = "Configuración de Puertas"
# La administración de concesionarios (empresas, documentación con vencimiento y
# horarios de ingreso) es trabajo de mesa de entradas, no de sistemas: va en su
# propio grupo para poder delegarla sin dar acceso a todo el panel.
GRUPO_CONCESIONARIOS = "Concesionarios"


def es_admin(user) -> bool:
    """El usuario es superusuario o pertenece al grupo Administrador."""
    return bool(
        getattr(user, "is_authenticated", False)
        and (user.is_superuser or user.groups.filter(name=GRUPO_ADMIN).exists())
    )


def puede_config_puertas(user) -> bool:
    """El usuario es admin o pertenece al grupo Configuración de Puertas."""
    return bool(
        getattr(user, "is_authenticated", False)
        and (es_admin(user) or user.groups.filter(name=GRUPO_PUERTAS).exists())
    )


def puede_concesionarios(user) -> bool:
    """El usuario es admin o pertenece al grupo Concesionarios."""
    return bool(
        getattr(user, "is_authenticated", False)
        and (es_admin(user) or user.groups.filter(name=GRUPO_CONCESIONARIOS).exists())
    )


def _rol_requerido(check):
    """Construye un decorador de vista: login + chequeo de rol (403 si no cumple)."""

    def decorator(view):
        @wraps(view)
        def _inner(request, *args, **kwargs):
            if not check(request.user):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return login_required(_inner)

    return decorator


admin_requerido = _rol_requerido(es_admin)
puertas_requerido = _rol_requerido(puede_config_puertas)
concesionarios_requerido = _rol_requerido(puede_concesionarios)


class PuedeConfigPuertas(BasePermission):
    """Permiso DRF: admin o grupo Configuración de Puertas."""

    message = "Requiere el rol de configuración de puertas."

    def has_permission(self, request, view):
        return puede_config_puertas(request.user)


class PuedeConcesionarios(BasePermission):
    """Permiso DRF: admin o grupo Concesionarios."""

    message = "Requiere el rol de administración de concesionarios."

    def has_permission(self, request, view):
        return puede_concesionarios(request.user)

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
from django.http import Http404
from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission

GRUPO_ADMIN = "Administrador"
GRUPO_PUERTAS = "Configuración de Puertas"
# La administración de concesionarios (empresas, documentación con vencimiento y
# horarios de ingreso) es trabajo de mesa de entradas, no de sistemas: va en su
# propio grupo para poder delegarla sin dar acceso a todo el panel.
GRUPO_CONCESIONARIOS = "concesionarios"
# Grupo pedido por el club para identificar responsables. Todavía no gobierna
# ninguna pantalla: existe para poder asignarlo y usarlo cuando se defina qué
# habilita. Si en algún momento decide algo, la regla va acá y no suelta por ahí.
GRUPO_RESPONSABLES = "responsables"
# La oficina de Socios: ve los avisos dejados sobre los socios para llamarlos y
# resolverlos. Es trabajo de mostrador, no de sistemas.
GRUPO_SOCIOS = "socios"
# Los tableros muestran el padrón entero y la deuda de la masa societaria. Es
# información de gestión —Tesorería, Comisión Directiva— y no de operación, así
# que va en su propio grupo: se entrega el número sin entregar el sistema. Igual
# que ``concesionarios``, NO alcanza con ser Administrador de la app.
GRUPO_TABLEROS = "tableros"
# Elecciones: un grupo por sector de la pantalla, porque el club pidió poder dar
# la participación a quien la sigue en la mesa sin mostrarle, por ejemplo, qué
# actividades tienen los que ya votaron. El orden es el de las pestañas.
# ``pantalla`` es la vista para el televisor: resumen, horarios y mesas juntos.
SECCIONES_ELECCIONES = ("participacion", "mesas", "horarios", "actividades", "pantalla")
GRUPOS_ELECCIONES = {s: f"elecciones_{s}" for s in SECCIONES_ELECCIONES}


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
    """Superusuario, staff, o miembro del grupo ``concesionarios``.

    A diferencia de los otros roles, éste NO mira el grupo ``Administrador``:
    el club pidió expresamente que el módulo lo vean sólo el superadmin, el
    staff y quien tenga el grupo. Ser "Administrador" de la app no alcanza.
    """
    return bool(
        getattr(user, "is_authenticated", False)
        and (user.is_superuser
             or user.is_staff
             or user.groups.filter(name=GRUPO_CONCESIONARIOS).exists())
    )


def puede_socios(user) -> bool:
    """Quién ve los avisos a socios: el grupo ``socios``, más quien ya los veía.

    La pantalla nació dentro del rol de puertas —los avisos se dejan desde el
    diagnóstico de facial, en el mostrador—, así que el grupo nuevo SUMA: sacarle
    la pantalla a quien la viene usando no era lo que se pidió.
    """
    return bool(
        getattr(user, "is_authenticated", False)
        and (puede_config_puertas(user) or user.groups.filter(name=GRUPO_SOCIOS).exists())
    )


def puede_tableros(user) -> bool:
    """Superusuario, staff, o miembro del grupo ``tableros``.

    Mismo criterio que ``puede_concesionarios`` y por el mismo motivo: se pidió
    un grupo aparte para dar los números a quien los necesita sin darle el resto
    del sistema. Ser "Administrador" NO alcanza; si el club quiere que los
    administradores entren igual, se agrega ``es_admin(user) or`` acá y listo.
    """
    return bool(
        getattr(user, "is_authenticated", False)
        and (user.is_superuser
             or user.is_staff
             or user.groups.filter(name=GRUPO_TABLEROS).exists())
    )


def secciones_elecciones(user) -> list[str]:
    """Sectores de la pantalla de Elecciones que el usuario puede ver.

    Superusuario y staff ven todos; el resto, los de sus grupos
    ``elecciones_<sector>``. Mismo criterio que tableros: ser Administrador de
    la app no alcanza.
    """
    if not getattr(user, "is_authenticated", False):
        return []
    if user.is_superuser or user.is_staff:
        return list(SECCIONES_ELECCIONES)
    grupos = set(user.groups.filter(name__in=GRUPOS_ELECCIONES.values())
                 .values_list("name", flat=True))
    return [s for s in SECCIONES_ELECCIONES if GRUPOS_ELECCIONES[s] in grupos]


def puede_elecciones(user) -> bool:
    """Ve la pantalla de Elecciones si tiene al menos un sector."""
    return bool(secciones_elecciones(user))


def _rol_requerido(check, error=PermissionDenied):
    """Construye un decorador de vista: login + chequeo de rol (403 si no cumple)."""

    def decorator(view):
        @wraps(view)
        def _inner(request, *args, **kwargs):
            if not check(request.user):
                raise error
            return view(request, *args, **kwargs)

        return login_required(_inner)

    return decorator


admin_requerido = _rol_requerido(es_admin)
puertas_requerido = _rol_requerido(puede_config_puertas)
concesionarios_requerido = _rol_requerido(puede_concesionarios)
socios_requerido = _rol_requerido(puede_socios)
tableros_requerido = _rol_requerido(puede_tableros)
elecciones_requerido = _rol_requerido(puede_elecciones, Http404)  # ver PuedeElecciones


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


class PuedeSocios(BasePermission):
    """Permiso DRF: mismo criterio que la pantalla de avisos a socios.

    Cubre las APIs de la ficha del socio, incluida la edición: quien atiende al
    socio en el mostrador es quien le corrige el documento.
    """

    message = "Requiere el rol de socios."

    def has_permission(self, request, view):
        return puede_socios(request.user)


class PuedeTableros(BasePermission):
    """Permiso DRF: mismo criterio que la pantalla de tableros."""

    message = "Requiere el rol de tableros."

    def has_permission(self, request, view):
        return puede_tableros(request.user)


class PuedeElecciones(BasePermission):
    """Permiso DRF por sector de Elecciones.

    La vista declara ``seccion_elecciones``; sin ella alcanza con tener alguno.
    Se chequea en el servidor y no sólo ocultando la pestaña: quien no tiene el
    sector no puede pedir sus datos aunque arme la URL a mano. A un usuario
    logueado sin el sector se le responde 404, no 403: el club no quiere que
    un link guardado revele que ese sector existe.
    """

    def has_permission(self, request, view):
        secciones = secciones_elecciones(request.user)
        seccion = getattr(view, "seccion_elecciones", None)
        if seccion in secciones if seccion else bool(secciones):
            return True
        if getattr(request.user, "is_authenticated", False):
            raise NotFound()
        return False

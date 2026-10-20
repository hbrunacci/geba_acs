"""APIs de la pantalla de Elecciones: una por sector.

Cada sector tiene su propio grupo (``common.roles.GRUPOS_ELECCIONES``) y el
permiso se chequea acá, no sólo ocultando la pestaña en la pantalla.
"""

from __future__ import annotations

from rest_framework.response import Response
from rest_framework.views import APIView

from common.roles import PuedeElecciones
from xsys.services import elecciones
from xsys.services.elecciones import EleccionesError


def _id(request) -> int | None:
    v = str(request.query_params.get("eleccion", "")).strip()
    return int(v) if v.isdigit() else None


class _Base(APIView):
    permission_classes = [PuedeElecciones]
    seccion_elecciones: str | None = None
    servicio = None

    def get(self, request):
        try:
            return Response(type(self).servicio(_id(request)))
        except EleccionesError as exc:
            return Response({"detail": str(exc)}, status=503)


class EleccionesListaAPI(APIView):
    """Las elecciones para el selector. Alcanza con tener algún sector."""

    permission_classes = [PuedeElecciones]

    def get(self, request):
        try:
            return Response({"elecciones": elecciones.listar()})
        except EleccionesError as exc:
            return Response({"detail": str(exc)}, status=503)


class EleccionesParticipacionAPI(_Base):
    seccion_elecciones = "participacion"
    servicio = staticmethod(elecciones.participacion)


class EleccionesMesasAPI(_Base):
    seccion_elecciones = "mesas"
    servicio = staticmethod(elecciones.mesas)


class EleccionesHorariosAPI(_Base):
    seccion_elecciones = "horarios"
    servicio = staticmethod(elecciones.horarios)


class EleccionesActividadesAPI(_Base):
    seccion_elecciones = "actividades"
    servicio = staticmethod(elecciones.actividades)

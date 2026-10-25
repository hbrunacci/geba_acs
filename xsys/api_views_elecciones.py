"""APIs de la pantalla de Elecciones: una por sector.

Cada sector tiene su propio grupo (``common.roles.GRUPOS_ELECCIONES``) y el
permiso se chequea acá, no sólo ocultando la pestaña en la pantalla.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from common.roles import PuedeElecciones
from xsys.services import elecciones
from xsys.services import elecciones_reporte
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


class EleccionesPantallaAPI(_Base):
    seccion_elecciones = "pantalla"
    servicio = staticmethod(elecciones.pantalla)


class EleccionesReportePDFAPI(APIView):
    """GET /api/xsys/elecciones/reporte.pdf?eleccion=<id>[&demo=1]

    Los acreditados por hora y por mesa, con nombre y número de socio, para
    repartir a las listas. Pide el sector "participacion" porque lleva datos
    personales de todo el que ya votó.
    """

    permission_classes = [PuedeElecciones]
    seccion_elecciones = "participacion"

    def get(self, request):
        demo = request.query_params.get("demo") == "1"
        try:
            todas = elecciones.listar()
            id_lista = _id(request)
            eleccion = next((e for e in todas if e["id"] == id_lista), None) if id_lista else todas[0]
            if eleccion is None:
                return Response({"detail": "No existe esa elección."}, status=404)
            if demo:
                import datetime as _dt
                fecha = (_dt.date.fromisoformat(eleccion["fecha"]) if eleccion.get("fecha")
                         else timezone.localdate())
                filas = elecciones_reporte.demo(fecha)
            else:
                filas = elecciones_reporte.acreditados(eleccion["id"])
        except (EleccionesError, IndexError) as exc:
            return Response({"detail": str(exc) or "xSys no tiene elecciones."}, status=503)
        contenido = elecciones_reporte.pdf(eleccion, filas, demo=demo)
        nombre = f"acreditados_{'demo_' if demo else ''}{timezone.localtime():%Y%m%d_%H%M}.pdf"
        r = HttpResponse(contenido, content_type="application/pdf")
        r["Content-Disposition"] = f'attachment; filename="{nombre}"'
        return r

"""APIs de la pantalla de Tableros.

Un endpoint por gráfico en vez de uno que devuelva todo: cada gráfico tiene su
propio filtro y su propio costo, y así cambiar el rango de uno no obliga a
recalcular los otros tres. El de deuda además lee de la foto guardada, con lo
que responde en milisegundos aunque el cálculo detrás tarde segundos.
"""

from __future__ import annotations

from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.views import APIView

from common.roles import PuedeTableros
from xsys.models import XsysDeudaFoto
from xsys.services import tableros
from xsys.services.tableros import TableroError

# Etiquetas de la población, para que el Excel diga en castellano qué contiene.
POBLACIONES = {
    "socios_activos": "Socios activos",
    "socios": "Socios (activos y de baja)",
    "activos": "Todas las personas activas",
    "todos": "Todo el padrón",
}


def _ids(request, nombre: str) -> list[int] | None:
    """Lee un parámetro repetido o separado por comas y lo pasa a enteros."""
    crudo = request.query_params.getlist(nombre) or []
    if len(crudo) == 1 and "," in crudo[0]:
        crudo = crudo[0].split(",")
    out = []
    for v in crudo:
        v = str(v).strip()
        if v.lstrip("-").isdigit():
            out.append(int(v))
    return out or None


def _args_detalle(request) -> dict:
    """Los filtros del modal. Se comparten entre ver y descargar para que el
    Excel contenga exactamente lo que la pantalla está mostrando."""
    return {
        "actividad": request.query_params.get("actividad", "").strip(),
        "rubro": request.query_params.get("rubro", "").strip(),
        "categorias_ids": _ids(request, "categorias"),
        "orden": request.query_params.get("orden", "").strip(),
        "desc": request.query_params.get("desc", "1") != "0",
        # El modal usa el mismo período que los gráficos: si el filtro dice
        # "últimos 3 meses", el detalle habla de esos tres meses.
        "clave_rango": request.query_params.get("rango"),
        "desde": request.query_params.get("desde"),
        "hasta": request.query_params.get("hasta"),
    }


def _entero(request, nombre: str, defecto: int = 0) -> int:
    v = str(request.query_params.get(nombre, "")).strip()
    return int(v) if v.lstrip("-").isdigit() else defecto


class _Base(APIView):
    permission_classes = [PuedeTableros]

    def _rango_args(self, request) -> dict:
        return {
            "clave": request.query_params.get("rango"),
            "desde": request.query_params.get("desde"),
            "hasta": request.query_params.get("hasta"),
        }


class TableroPadronAPI(_Base):
    """Stock de activos y variación neta, socios contra no socios."""

    def get(self, request):
        try:
            return Response(tableros.padron(**self._rango_args(request)))
        except TableroError as exc:
            return Response({"detail": str(exc)}, status=503)


class TableroAltasBajasAPI(_Base):
    """Altas y bajas del rango, con selector socios / no socios / todos."""

    def get(self, request):
        try:
            return Response(tableros.altas_bajas(
                tipo=request.query_params.get("tipo", "todos"),
                **self._rango_args(request)))
        except TableroError as exc:
            return Response({"detail": str(exc)}, status=503)


class TableroCategoriasAPI(_Base):
    """Reparto por categoría, con las menores al umbral en una torta aparte."""

    def get(self, request):
        umbral = request.query_params.get("umbral", "1")
        try:
            umbral = max(0.0, min(float(umbral), 50.0))
        except ValueError:
            umbral = 1.0
        try:
            return Response(tableros.categorias(
                umbral=umbral,
                ids=_ids(request, "categorias"),
                solo=request.query_params.get("solo", "todos")))
        except TableroError as exc:
            return Response({"detail": str(exc)}, status=503)


class TableroDeudaAPI(_Base):
    """Deuda por mes leída de la última foto guardada."""

    def get(self, request):
        return Response(tableros.deuda(
            categorias_ids=_ids(request, "categorias"),
            cuotas_min=_entero(request, "cuotas_min"),
            solo=request.query_params.get("solo", "socios_activos"),
            **self._rango_args(request)))


class TableroDeudaRecalcularAPI(APIView):
    """Dispara el recálculo de la foto de deuda EN SEGUNDO PLANO.

    Va en POST y no en GET porque escribe: dispara una consulta de varios minutos
    contra la base de producción del club y deja ~360.000 filas nuevas. No debe
    poder ejecutarse desde un enlace ni desde un prefetch del navegador.

    Contesta enseguida, sin esperar a que termine. Antes esperaba, y con el
    detalle por mes el cálculo pasó a durar unos cuatro minutos: la petición se
    moría por timeout del proxy o del navegador, el usuario veía un error y el
    cálculo igual seguía corriendo por detrás. La pantalla pregunta el estado
    cada tantos segundos con ``TableroDeudaEstadoAPI``.
    """

    permission_classes = [PuedeTableros]

    def post(self, request):
        estado = tableros.lanzar_recalculo(
            usuario=getattr(request.user, "username", "") or "")
        # 409 cuando ya hay uno corriendo: no es un error del usuario, pero
        # tampoco arrancó nada, y la pantalla tiene que poder distinguirlo.
        return Response(estado, status=200 if estado["arrancado"] else 409)


class TableroDeudaEstadoAPI(_Base):
    """Si hay un cálculo corriendo, hace cuánto y cuánto suele tardar."""

    def get(self, request):
        return Response(tableros.estado_calculo())


class TableroDeudaExcelAPI(APIView):
    """Baja el detalle de deuda a .xlsx con los filtros de la pantalla."""

    permission_classes = [PuedeTableros]

    def get(self, request):
        foto = XsysDeudaFoto.ultima_buena()
        if foto is None:
            return Response(
                {"detail": "Todavía no hay una foto de deuda calculada. "
                           "Usá el botón Actualizar del tablero."},
                status=409)

        try:
            from xsys.services import tableros_excel
        except ImportError:
            # openpyxl se agregó junto con esta pantalla. Si la imagen todavía
            # no se reconstruyó, cae acá: mejor un mensaje que diga qué hacer
            # que un 500 sin explicación. El resto del tablero no depende de él.
            return Response(
                {"detail": "Falta la biblioteca openpyxl en el servidor. "
                           "Reconstruí la imagen (docker compose build web) "
                           "para habilitar la descarga a Excel."},
                status=503)

        ids = _ids(request, "categorias")
        solo = request.query_params.get("solo", "socios_activos")
        cuotas_min = _entero(request, "cuotas_min")

        etiqueta_cat = "Todas"
        if ids:
            nombres = (tableros.socios_con_deuda(foto, ids, 0, solo)
                       .values_list("categoria", flat=True).distinct())
            etiqueta_cat = ", ".join(sorted(set(nombres))) or "Todas"

        contenido = tableros_excel.generar(
            foto,
            categorias_ids=ids,
            cuotas_min=cuotas_min,
            solo=solo,
            etiqueta_poblacion=POBLACIONES.get(solo, solo),
            etiqueta_categorias=etiqueta_cat,
            usuario=getattr(request.user, "username", "") or "")

        respuesta = HttpResponse(
            contenido,
            content_type="application/vnd.openxmlformats-officedocument."
                         "spreadsheetml.sheet")
        respuesta["Content-Disposition"] = (
            f'attachment; filename="{tableros_excel.nombre_archivo(foto)}"')
        return respuesta


class TableroDeudaDetalleAPI(_Base):
    """Quiénes deben una actividad (o un rubro) y desde cuándo.

    Alimenta el modal que se abre al tocar una barra del desglose. Lee de la
    misma foto que el gráfico, así que los totales del modal y los de la barra
    tocada siempre coinciden.
    """

    def get(self, request):
        return Response(tableros.detalle_actividad(
            **_args_detalle(request),
            limit=min(_entero(request, "limit", 200) or 200, 1000),
            offset=_entero(request, "offset")))


class TableroDeudaDetalleExcelAPI(APIView):
    """El mismo listado del modal, en .xlsx.

    Baja el conjunto COMPLETO y no las 200 filas que se ven: la pantalla muestra
    una muestra para poder mirarla, el Excel es para trabajarla. Respeta el orden
    y los filtros que tenía puestos la pantalla, así el archivo se parece a lo
    que la persona estaba viendo.
    """

    permission_classes = [PuedeTableros]

    def get(self, request):
        detalle = tableros.detalle_actividad(**_args_detalle(request),
                                             limit=100000, offset=0)
        if detalle.get("sin_foto"):
            return Response(
                {"detail": "Todavía no hay una foto de deuda calculada."},
                status=409)
        if not detalle.get("total"):
            return Response({"detail": "No hay deuda con esos filtros."}, status=409)

        try:
            from xsys.services import tableros_excel
        except ImportError:
            return Response(
                {"detail": "Falta la biblioteca openpyxl en el servidor."},
                status=503)

        contenido = tableros_excel.generar_detalle(
            detalle, usuario=getattr(request.user, "username", "") or "")
        respuesta = HttpResponse(
            contenido,
            content_type="application/vnd.openxmlformats-officedocument."
                         "spreadsheetml.sheet")
        respuesta["Content-Disposition"] = (
            'attachment; filename="%s"' % tableros_excel.nombre_archivo_detalle(detalle))
        return respuesta

from django.db.models import Count
from django.shortcuts import render

from common.roles import (
    admin_requerido,
    puertas_requerido,
    socios_requerido,
    tableros_requerido,
)
from xsys.models import (
    SyncState,
    XsysDeudaFoto,
    XsysSocio,
    XsysSocioFoto,
    XsysWhitelist,
)
from xsys.services.tableros import RANGOS as RANGOS_TABLERO

STREAM_LABELS = {
    "novedades": "Novedades / socios",
    "cd_es": "Movimientos (CD_ES)",
    "fotos": "Fotos",
    "whitelist": "Lista blanca",
}


def xsys_puerta_monitor(request):
    """Pantalla de monitor de una puerta (kiosco, sin login).

    Todos los datos salen del espejo local vía la API AllowAny; la propia pantalla
    se identifica por su token y elige la puerta con la hamburguesa.

    Se sirve sin caché: son pantallas de kiosco que quedan abiertas días, y sin
    esto un cambio en el template no llega hasta que alguien las refresca a mano.
    """
    # La versión se embebe en el HTML para que la pantalla pueda comparar la SUYA
    # con la del servidor. Comparar sólo respuestas consecutivas no alcanzaba: un
    # kiosco que ya tenía cargada una versión vieja tomaba la nueva como
    # referencia inicial y no se recargaba nunca.
    from xsys.api_views import _version_visor

    response = render(request, "xsys/puerta_monitor.html", {"visor_version": _version_visor()})
    response["Cache-Control"] = "no-store, must-revalidate"
    return response


@puertas_requerido
def xsys_diagnostico(request):
    """"¿Por qué no entra?": el diagnóstico completo de una persona.

    Va con el rol de puertas, no sólo admin, porque el que necesita la respuesta
    es el que tiene la fila esperando.
    """
    return render(request, "xsys/diagnostico.html")


@puertas_requerido
def xsys_molinetes_config(request):
    """Administración de molinetes (columnas) por puerta."""
    return render(request, "xsys/molinetes_config.html")


@socios_requerido
def xsys_socios_fichas(request):
    """Fichas de socios: listado, edición, historial de pasos, cuenta corriente,
    contratos y grupo familiar.

    Las categorías del filtro salen del espejo local y no del catálogo de xSys:
    interesan las que ALGUIEN tiene puesta (58 categorías existen, 30 se usan) y
    así la pantalla abre sin depender de la VPN.
    """
    categorias = [
        {"id": c["id_tipo_cli"], "nombre": c["categoria"], "socios": c["n"]}
        for c in XsysSocio.objects.exclude(id_tipo_cli__isnull=True)
        .values("id_tipo_cli", "categoria")
        .annotate(n=Count("id_cliente"))
        .order_by("categoria")
        if c["categoria"]
    ]
    context = {
        "categorias": categorias,
        "total_socios": XsysSocio.objects.count(),
        "total_activos": XsysSocio.objects.filter(activo=1).count(),
    }
    return render(request, "xsys/socios_fichas.html", context)


@admin_requerido
def xsys_socio_console(request):
    """Consola de búsqueda de socios del espejo xSys (datos + foto + lista blanca)."""
    estados = []
    for state in SyncState.objects.all().order_by("stream"):
        estados.append(
            {
                "stream": state.stream,
                "label": STREAM_LABELS.get(state.stream, state.stream),
                "last_run_finished_at": state.last_run_finished_at,
                "last_run_ok": state.last_run_ok,
                "rows_last_run": state.rows_last_run,
                "last_id": state.last_id,
                "last_error": state.last_error,
            }
        )
    context = {
        "sync_states": estados,
        "total_socios": XsysSocio.objects.count(),
        "total_fotos": XsysSocioFoto.objects.count(),
        "total_habilitados": XsysWhitelist.objects.filter(habilitado=True).count(),
    }
    return render(request, "xsys/socio_console.html", context)


@tableros_requerido
def xsys_tableros(request):
    """Tableros de gestión: padrón, altas y bajas, categorías y deuda.

    La pantalla arranca vacía y pide todo por API. El catálogo de categorías del
    filtro sí viene servido acá, del espejo local: son 30 opciones que no cambian
    entre recargas y así el ``<select>`` está poblado antes del primer dibujo,
    sin un salto visual mientras llega el JSON.

    La fecha de la última foto de deuda también viaja en el HTML, porque es lo
    primero que hay que saber al mirar un número de deuda: si es de esta mañana
    o de hace tres días.
    """
    categorias = [
        {"id": c["id_tipo_cli"], "nombre": c["categoria"], "socios": c["n"]}
        for c in XsysSocio.objects.filter(activo=1)
        .exclude(id_tipo_cli__isnull=True)
        .values("id_tipo_cli", "categoria")
        .annotate(n=Count("id_cliente"))
        .order_by("categoria")
        if c["categoria"]
    ]
    foto = XsysDeudaFoto.ultima_buena()
    return render(request, "xsys/tableros.html", {
        "categorias": categorias,
        "deuda_foto": foto,
        "rangos": RANGOS_TABLERO,
    })

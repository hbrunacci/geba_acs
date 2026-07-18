from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from xsys.models import SyncState, XsysSocio, XsysSocioFoto, XsysWhitelist

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
    """
    return render(request, "xsys/puerta_monitor.html")


@login_required
def xsys_molinetes_config(request):
    """Administración de molinetes (columnas) por puerta."""
    return render(request, "xsys/molinetes_config.html")


@login_required
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

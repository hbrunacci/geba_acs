from django.urls import path

from xsys.api_views import (
    AccesoResolverAPI,
    ControladoresPorAccesoAPI,
    MolineteConfigDetailAPI,
    MolinetesAutoAPI,
    MolinetesConfigAPI,
    PuertaEstadoAPI,
    PuertaSeleccionarAPI,
    PuertasListAPI,
    SocioDetalleAPI,
    SocioFotoAPI,
    SocioLookupAPI,
    SocioSearchAPI,
    SocioWhitelistAPI,
)

urlpatterns = [
    path("xsys/acceso/", AccesoResolverAPI.as_view(), name="xsys_acceso_api"),
    path("xsys/puertas/", PuertasListAPI.as_view(), name="xsys_puertas_api"),
    path("xsys/puerta/estado/", PuertaEstadoAPI.as_view(), name="xsys_puerta_estado_api"),
    path("xsys/puerta/seleccionar/", PuertaSeleccionarAPI.as_view(), name="xsys_puerta_seleccionar_api"),
    # Administración de molinetes (columnas) por puerta — requiere login.
    path("xsys/config/controladores/", ControladoresPorAccesoAPI.as_view(), name="xsys_config_controladores_api"),
    path("xsys/config/molinetes/", MolinetesConfigAPI.as_view(), name="xsys_config_molinetes_api"),
    path("xsys/config/molinetes/auto/", MolinetesAutoAPI.as_view(), name="xsys_config_molinetes_auto_api"),
    path("xsys/config/molinetes/<int:mid>/", MolineteConfigDetailAPI.as_view(), name="xsys_config_molinete_detail_api"),
    path("xsys/socios/lookup/", SocioLookupAPI.as_view(), name="xsys_socio_lookup_api"),
    path("xsys/socios/", SocioSearchAPI.as_view(), name="xsys_socio_search_api"),
    path("xsys/socios/<int:id_cliente>/detalle/", SocioDetalleAPI.as_view(), name="xsys_socio_detalle_api"),
    path("xsys/socios/<int:id_cliente>/whitelist/", SocioWhitelistAPI.as_view(), name="xsys_socio_whitelist_api"),
    path("xsys/socios/<int:id_cliente>/foto/", SocioFotoAPI.as_view(), name="xsys_socio_foto_api"),
]

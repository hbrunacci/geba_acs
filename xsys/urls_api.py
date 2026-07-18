from django.urls import path

from xsys.api_views import (
    AccesoResolverAPI,
    PuertaSeleccionarAPI,
    PuertaUltimoAPI,
    PuertasListAPI,
    SocioFotoAPI,
    SocioLookupAPI,
    SocioSearchAPI,
    SocioWhitelistAPI,
)

urlpatterns = [
    path("xsys/acceso/", AccesoResolverAPI.as_view(), name="xsys_acceso_api"),
    path("xsys/puertas/", PuertasListAPI.as_view(), name="xsys_puertas_api"),
    path("xsys/puerta/ultimo/", PuertaUltimoAPI.as_view(), name="xsys_puerta_ultimo_api"),
    path("xsys/puerta/seleccionar/", PuertaSeleccionarAPI.as_view(), name="xsys_puerta_seleccionar_api"),
    path("xsys/socios/lookup/", SocioLookupAPI.as_view(), name="xsys_socio_lookup_api"),
    path("xsys/socios/", SocioSearchAPI.as_view(), name="xsys_socio_search_api"),
    path("xsys/socios/<int:id_cliente>/whitelist/", SocioWhitelistAPI.as_view(), name="xsys_socio_whitelist_api"),
    path("xsys/socios/<int:id_cliente>/foto/", SocioFotoAPI.as_view(), name="xsys_socio_foto_api"),
]

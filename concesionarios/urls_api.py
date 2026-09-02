from django.urls import path

from concesionarios import api_views as v

urlpatterns = [
    path("concesionarios/", v.ListadoAPI.as_view(), name="api_conc_listado"),
    path("concesionarios/alta/", v.ConcesionariosAPI.as_view(), name="api_conc_alta"),
    path("concesionarios/candidatos/", v.CandidatosAPI.as_view(), name="api_conc_candidatos"),
    path("concesionarios/ingresos/", v.IngresosAPI.as_view(), name="api_conc_ingresos"),
    path("concesionarios/foto/<int:id_cliente>/", v.FotoAPI.as_view(), name="api_conc_foto"),
    path("concesionarios/foto/<int:id_cliente>/enrolar/", v.EnrolarFotoAPI.as_view(),
         name="api_conc_foto_enrolar"),
    path("concesionarios/<int:pk>/", v.ConcesionarioDetalleAPI.as_view(), name="api_conc_detalle"),

    path("concesionarios/empresas/", v.EmpresasAPI.as_view(), name="api_conc_empresas"),
    path("concesionarios/empresas/<int:pk>/", v.EmpresaDetalleAPI.as_view(),
         name="api_conc_empresa_detalle"),

    path("concesionarios/horarios/", v.HorariosAPI.as_view(), name="api_conc_horarios"),
    path("concesionarios/horarios/<int:pk>/", v.HorarioDetalleAPI.as_view(),
         name="api_conc_horario_detalle"),

    path("concesionarios/tipos-documento/", v.TiposDocumentoAPI.as_view(),
         name="api_conc_tipos_doc"),
    path("concesionarios/tipos-documento/<int:pk>/", v.TipoDocumentoDetalleAPI.as_view(),
         name="api_conc_tipo_doc_detalle"),

    path("concesionarios/documentos/", v.DocumentosAPI.as_view(), name="api_conc_documentos"),
    path("concesionarios/documentos/<int:pk>/", v.DocumentoDetalleAPI.as_view(),
         name="api_conc_documento_detalle"),
    path("concesionarios/documentos/<int:pk>/archivo/", v.DocumentoArchivoAPI.as_view(),
         name="api_conc_documento_archivo"),
]

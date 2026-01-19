from django.contrib import admin
from django.urls import include, path
from rest_framework.authtoken import views as drf_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/token/", drf_views.obtain_auth_token, name="api-token"),
    path("api/", include("people.urls")),
    path("api/", include("institutions.urls")),
    path("api/", include("access_control.urls")),
    path("api/", include("access_control.urls_api")),
    path("", include("common.urls")),
]

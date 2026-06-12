from django.urls import path

from .views import DashboardView, LoginView, LogoutView, ServiceWorkerView

app_name = "common"

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("service-worker.js", ServiceWorkerView.as_view(), name="service_worker"),
]

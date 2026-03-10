from django.urls import path

from .views import access_topology_console, events_console

urlpatterns = [
    path("events-console/", events_console, name="events_console"),
    path("topology-console/", access_topology_console, name="access_topology_console"),
]

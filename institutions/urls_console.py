from django.urls import path

from .views import events_console

urlpatterns = [
    path("events-console/", events_console, name="events_console"),
]

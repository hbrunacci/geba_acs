from django.urls import path

from .views import people_configuration_console

urlpatterns = [
    path("people-console/", people_configuration_console, name="people_configuration_console"),
]

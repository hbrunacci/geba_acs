from django.urls import path

from access_control.views import (
    access_reports_console,
    biostar_devices_console,
    biostar_users_console,
    external_access_console,
)


urlpatterns = [
    path(
        "external-access/",
        external_access_console,
        name="external_access_console",
    ),
    path(
        "reports-console/",
        access_reports_console,
        name="access_reports_console",
    ),
    path(
        "biostar/devices/",
        biostar_devices_console,
        name="biostar_devices_console",
    ),
    path(
        "biostar/users/",
        biostar_users_console,
        name="biostar_users_console",
    ),

]

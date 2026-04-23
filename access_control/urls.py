from django.urls import path

from access_control.views import (
    access_reports_console,
    api3000_test_console,
    anses_verification_console,
    biostar_devices_console,
    biostar_users_console,
    external_access_console,
    parking_movements_console,
)


urlpatterns = [

    path(
        "parking-movements/",
        parking_movements_console,
        name="parking_movements_console",
    ),
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
        "anses-verification/",
        anses_verification_console,
        name="anses_verification_console",
    ),
    path(
        "api3000-test/",
        api3000_test_console,
        name="api3000_test_console",
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

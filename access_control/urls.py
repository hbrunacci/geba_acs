from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import ExternalAccessLogView, WhitelistEntryViewSet
from access_control.views import (
    biostar_devices_console,
    biostar_users_console,
    external_access_console,
)


router = DefaultRouter()
router.register(r"whitelist", WhitelistEntryViewSet)

urlpatterns = router.urls + [
    path(
        "external-access/latest/",
        ExternalAccessLogView.as_view(),
        name="external-access-latest",
    ),
    path(
        "external-access/",
        external_access_console,
        name="external_access_console",
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

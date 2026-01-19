from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AccessDeviceViewSet,
    AccessPointViewSet,
    EventViewSet,
    SiteViewSet,
    events_console,
)

router = DefaultRouter()
router.register(r"sites", SiteViewSet)
router.register(r"access-points", AccessPointViewSet)
router.register(r"access-devices", AccessDeviceViewSet)
router.register(r"events", EventViewSet)

urlpatterns = router.urls + [
    path("events-console/", events_console, name="events_console"),
]

from rest_framework.routers import DefaultRouter

from .views import (
    AccessDeviceViewSet,
    AccessDoorViewSet,
    AccessPointViewSet,
    AccessZoneViewSet,
    DoorDeviceViewSet,
    DoorZoneControlViewSet,
    EventViewSet,
    SiteViewSet,
)

router = DefaultRouter()
router.register(r"sites", SiteViewSet)
router.register(r"access-points", AccessPointViewSet)
router.register(r"access-devices", AccessDeviceViewSet)
router.register(r"events", EventViewSet)
router.register(r"access-doors", AccessDoorViewSet)
router.register(r"door-devices", DoorDeviceViewSet)
router.register(r"access-zones", AccessZoneViewSet)
router.register(r"door-zone-controls", DoorZoneControlViewSet)

urlpatterns = router.urls

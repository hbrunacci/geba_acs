from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ExternalAccessLogView, WhitelistEntryViewSet

router = DefaultRouter()
router.register(r"whitelist", WhitelistEntryViewSet)

urlpatterns = router.urls + [
    path(
        "external-access/latest/",
        ExternalAccessLogView.as_view(),
        name="external-access-latest",
    ),
]

from rest_framework.routers import DefaultRouter

from .views import WhitelistEntryViewSet

router = DefaultRouter()
router.register(r"whitelist", WhitelistEntryViewSet)

urlpatterns = router.urls

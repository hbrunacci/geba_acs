from rest_framework.routers import DefaultRouter

from .views import GuestInvitationViewSet, PersonViewSet

router = DefaultRouter()
router.register(r"persons", PersonViewSet)
router.register(r"guest-invitations", GuestInvitationViewSet)

urlpatterns = router.urls

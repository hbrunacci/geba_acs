from rest_framework.routers import DefaultRouter

from .views import (
    DocumentTypeViewSet,
    GuestInvitationViewSet,
    PersonCategoryDocumentRequirementViewSet,
    PersonCategoryViewSet,
    PersonDocumentViewSet,
    PersonViewSet,
)

router = DefaultRouter()
router.register(r"persons", PersonViewSet)
router.register(r"guest-invitations", GuestInvitationViewSet)
router.register(r"person-categories", PersonCategoryViewSet)
router.register(r"document-types", DocumentTypeViewSet)
router.register(r"document-requirements", PersonCategoryDocumentRequirementViewSet)
router.register(r"person-documents", PersonDocumentViewSet)

urlpatterns = router.urls

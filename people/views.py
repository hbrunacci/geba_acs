from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from rest_framework import viewsets

from .models import DocumentType, GuestInvitation, Person, PersonCategory, PersonCategoryDocumentRequirement, PersonDocument
from .serializers import (
    DocumentTypeSerializer,
    GuestInvitationSerializer,
    PersonCategoryDocumentRequirementSerializer,
    PersonCategorySerializer,
    PersonDocumentSerializer,
    PersonSerializer,
)


class PersonViewSet(viewsets.ModelViewSet):
    queryset = Person.objects.all()
    serializer_class = PersonSerializer


class GuestInvitationViewSet(viewsets.ModelViewSet):
    queryset = GuestInvitation.objects.select_related("person", "event", "event__site").all()
    serializer_class = GuestInvitationSerializer


class PersonCategoryViewSet(viewsets.ModelViewSet):
    queryset = PersonCategory.objects.all()
    serializer_class = PersonCategorySerializer


class DocumentTypeViewSet(viewsets.ModelViewSet):
    queryset = DocumentType.objects.all()
    serializer_class = DocumentTypeSerializer


class PersonCategoryDocumentRequirementViewSet(viewsets.ModelViewSet):
    queryset = PersonCategoryDocumentRequirement.objects.select_related(
        "person_category", "document_type"
    ).all()
    serializer_class = PersonCategoryDocumentRequirementSerializer


class PersonDocumentViewSet(viewsets.ModelViewSet):
    queryset = PersonDocument.objects.select_related("person", "document_type").all()
    serializer_class = PersonDocumentSerializer


@login_required
def people_configuration_console(request):
    """Consola para gestionar categorías y documentación de personas."""
    return render(request, "people/people_configuration_console.html")

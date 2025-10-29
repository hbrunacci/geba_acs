from rest_framework import viewsets

from .models import GuestInvitation, Person
from .serializers import GuestInvitationSerializer, PersonSerializer


class PersonViewSet(viewsets.ModelViewSet):
    queryset = Person.objects.all()
    serializer_class = PersonSerializer


class GuestInvitationViewSet(viewsets.ModelViewSet):
    queryset = GuestInvitation.objects.select_related("person", "event", "event__site").all()
    serializer_class = GuestInvitationSerializer

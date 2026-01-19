from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from rest_framework import viewsets

from .models import AccessDevice, AccessPoint, Event, Site
from .serializers import (
    AccessDeviceSerializer,
    AccessPointSerializer,
    EventSerializer,
    SiteSerializer,
)


class SiteViewSet(viewsets.ModelViewSet):
    queryset = Site.objects.all()
    serializer_class = SiteSerializer


class AccessPointViewSet(viewsets.ModelViewSet):
    queryset = AccessPoint.objects.select_related("site").all()
    serializer_class = AccessPointSerializer


class AccessDeviceViewSet(viewsets.ModelViewSet):
    queryset = AccessDevice.objects.select_related("access_point", "access_point__site").all()
    serializer_class = AccessDeviceSerializer


class EventViewSet(viewsets.ModelViewSet):
    queryset = Event.objects.select_related("site").all()
    serializer_class = EventSerializer


@login_required
def events_console(request):
    """Consola web para listar y sincronizar eventos desde la base local."""
    return render(request, "institutions/events_console.html")

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from rest_framework import viewsets

from .models import AccessDevice, AccessDoor, AccessPoint, AccessZone, DoorDevice, DoorZoneControl, Event, Site
from .serializers import (
    AccessDeviceSerializer,
    AccessDoorSerializer,
    AccessPointSerializer,
    AccessZoneSerializer,
    DoorDeviceSerializer,
    DoorZoneControlSerializer,
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


class AccessDoorViewSet(viewsets.ModelViewSet):
    queryset = AccessDoor.objects.select_related("site").all()
    serializer_class = AccessDoorSerializer


class DoorDeviceViewSet(viewsets.ModelViewSet):
    queryset = DoorDevice.objects.select_related("door", "door__site").all()
    serializer_class = DoorDeviceSerializer


class AccessZoneViewSet(viewsets.ModelViewSet):
    queryset = AccessZone.objects.select_related("site", "parent_zone").all()
    serializer_class = AccessZoneSerializer


class DoorZoneControlViewSet(viewsets.ModelViewSet):
    queryset = DoorZoneControl.objects.select_related("door", "zone").all()
    serializer_class = DoorZoneControlSerializer


@login_required
def events_console(request):
    """Consola web para listar y sincronizar eventos desde la base local."""
    return render(request, "institutions/events_console.html")

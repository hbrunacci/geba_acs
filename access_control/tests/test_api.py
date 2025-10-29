from datetime import date, time

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from people.models import GuestType, PersonType


class BaseAPITestCase(APITestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.user = User.objects.create_user("tester", "tester@example.com", "password123")
        self.token = Token.objects.create(user=self.user)

    def authenticate(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")


class PersonAPITestCase(BaseAPITestCase):
    def test_person_crud_requires_authentication(self):
        url = reverse("person-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 401)

        self.authenticate()
        payload = {
            "first_name": "Ana",
            "last_name": "García",
            "dni": "30111222",
            "address": "Calle Falsa 123",
            "phone": "+54 11 5555-1111",
            "email": "ana@example.com",
            "credential_code": "CRD123",
            "facial_enrolled": True,
            "person_type": PersonType.MEMBER,
            "guest_type": None,
            "is_active": True,
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        person_id = response.data["id"]

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        detail_url = reverse("person-detail", args=[person_id])
        response = self.client.patch(detail_url, {"phone": "+54 11 4444-0000"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["phone"], "+54 11 4444-0000")

        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, 204)


class SiteAccessAPITestCase(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.authenticate()

    def test_access_related_crud(self):
        site_url = reverse("site-list")
        site_payload = {"name": "Sede Central", "address": "Av. Siempre Viva 742"}
        site_response = self.client.post(site_url, site_payload, format="json")
        self.assertEqual(site_response.status_code, 201)
        site_id = site_response.data["id"]

        access_point_url = reverse("accesspoint-list")
        access_point_payload = {
            "site": site_id,
            "name": "Entrada Principal",
            "description": "Acceso por la calle principal",
        }
        ap_response = self.client.post(access_point_url, access_point_payload, format="json")
        self.assertEqual(ap_response.status_code, 201)
        access_point_id = ap_response.data["id"]

        device_url = reverse("accessdevice-list")
        device_payload = {
            "access_point": access_point_id,
            "name": "Molinetes 1",
            "device_type": "turnstile",
            "has_credential_reader": True,
            "has_qr_reader": True,
            "has_facial_reader": False,
        }
        device_response = self.client.post(device_url, device_payload, format="json")
        self.assertEqual(device_response.status_code, 201)

        list_response = self.client.get(device_url)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data), 1)


class EventAndWhitelistAPITestCase(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.authenticate()
        site_url = reverse("site-list")
        site_payload = {"name": "Sede Norte", "address": "Ruta 8 km 50"}
        self.site_id = self.client.post(site_url, site_payload, format="json").data["id"]

        person_url = reverse("person-list")
        self.member_id = self.client.post(
            person_url,
            {
                "first_name": "Carlos",
                "last_name": "Pérez",
                "dni": "30111223",
                "address": "Calle 1",
                "phone": "123",
                "email": "carlos@example.com",
                "credential_code": "CRD456",
                "facial_enrolled": False,
                "person_type": PersonType.MEMBER,
                "guest_type": None,
                "is_active": True,
            },
            format="json",
        ).data["id"]
        self.guest_id = self.client.post(
            person_url,
            {
                "first_name": "Lucía",
                "last_name": "Suarez",
                "dni": "40111223",
                "address": "Calle 2",
                "phone": "456",
                "email": "lucia@example.com",
                "credential_code": "CRD789",
                "facial_enrolled": True,
                "person_type": PersonType.GUEST,
                "guest_type": GuestType.EVENT_VISITOR,
                "is_active": True,
            },
            format="json",
        ).data["id"]

        access_point_url = reverse("accesspoint-list")
        self.access_point_id = self.client.post(
            access_point_url,
            {
                "site": self.site_id,
                "name": "Entrada Norte",
                "description": "",
            },
            format="json",
        ).data["id"]

    def test_event_guest_invitation_and_whitelist(self):
        event_url = reverse("event-list")
        event_payload = {
            "name": "Torneo Interno",
            "site": self.site_id,
            "description": "Competencia anual",
            "start_date": date(2024, 1, 1),
            "end_date": date(2024, 1, 2),
            "start_time": time(9, 0),
            "end_time": time(18, 0),
            "allowed_person_types": [PersonType.MEMBER],
            "allowed_guest_types": [GuestType.EVENT_VISITOR],
        }
        event_response = self.client.post(event_url, event_payload, format="json")
        self.assertEqual(event_response.status_code, 201)
        event_id = event_response.data["id"]

        invitation_url = reverse("guestinvitation-list")
        invitation_payload = {
            "person": self.guest_id,
            "event": event_id,
            "guest_type": GuestType.EVENT_VISITOR,
        }
        invitation_response = self.client.post(invitation_url, invitation_payload, format="json")
        self.assertEqual(invitation_response.status_code, 201)

        whitelist_url = reverse("whitelistentry-list")
        whitelist_payload = {
            "person": self.member_id,
            "access_point": self.access_point_id,
            "event": event_id,
            "is_allowed": True,
            "valid_from": date(2023, 12, 31),
            "valid_until": date(2024, 1, 3),
        }
        whitelist_response = self.client.post(whitelist_url, whitelist_payload, format="json")
        self.assertEqual(whitelist_response.status_code, 201)

        guest_whitelist_payload = {
            "person": self.guest_id,
            "access_point": self.access_point_id,
            "event": event_id,
            "is_allowed": True,
        }
        guest_whitelist_response = self.client.post(
            whitelist_url, guest_whitelist_payload, format="json"
        )
        self.assertEqual(guest_whitelist_response.status_code, 201)

        list_response = self.client.get(whitelist_url)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data), 2)

    def test_event_validation_forbidden_category(self):
        event_url = reverse("event-list")
        event_payload = {
            "name": "Reunión Directiva",
            "site": self.site_id,
            "description": "",
            "start_date": date(2024, 2, 1),
            "end_date": date(2024, 2, 1),
            "start_time": time(10, 0),
            "end_time": time(12, 0),
            "allowed_person_types": [PersonType.EMPLOYEE],
            "allowed_guest_types": [],
        }
        event_response = self.client.post(event_url, event_payload, format="json")
        self.assertEqual(event_response.status_code, 201)
        event_id = event_response.data["id"]

        whitelist_url = reverse("whitelistentry-list")
        payload = {
            "person": self.member_id,
            "access_point": self.access_point_id,
            "event": event_id,
            "is_allowed": True,
        }
        response = self.client.post(whitelist_url, payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("event", response.data)

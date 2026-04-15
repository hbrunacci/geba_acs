from datetime import date, datetime, time, timedelta
from unittest.mock import patch
import zipfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.db.utils import OperationalError
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from access_control.models import AnsesVerificationRecord, ExternalAccessLogEntry, ParkingMovement
from access_control.models.models import AccessEvent
from people.models import Cliente, GuestType, PersonType


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
        self.assertEqual(response.status_code, 403)

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


class ExternalAccessLogAPITestCase(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("external-access-latest")
        self.entry_1 = ExternalAccessLogEntry.objects.create(
            external_id=1,
            tipo="E",
            origen="A",
            id_tarjeta="B4C7BD56",
            id_cliente=100738,
            fecha=timezone.now(),
            resultado="S",
            id_controlador=1,
            id_acceso=1,
            observacion="Ingreso habilitado",
            tipo_registro="REG",
        )
        self.entry_2 = ExternalAccessLogEntry.objects.create(
            external_id=2,
            tipo="E",
            origen="A",
            id_tarjeta="B4C7BD56",
            id_cliente=100738,
            fecha=timezone.now() - timezone.timedelta(minutes=5),
            resultado="S",
            id_controlador=1,
            id_acceso=1,
            observacion="Ingreso previo",
            tipo_registro="REG",
        )

    def test_requires_authentication(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_returns_data(self):
        self.authenticate()

        response = self.client.get(self.url, {"limit": 1})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["external_id"], self.entry_1.external_id)
        self.assertEqual(response.data[0]["observacion"], "Ingreso habilitado")

    def test_invalid_limit(self):
        self.authenticate()
        response = self.client.get(self.url, {"limit": "abc"})
        self.assertEqual(response.status_code, 400)

    def test_negative_limit(self):
        self.authenticate()
        response = self.client.get(self.url, {"limit": -1})
        self.assertEqual(response.status_code, 400)

    def test_returns_all_when_no_limit(self):
        self.authenticate()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)


class AccessReportsAPITestCase(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.authenticate()
        self.site_id = self.client.post(
            reverse("site-list"),
            {"name": "Sede Reportes", "address": "Av. 123"},
            format="json",
        ).data["id"]
        self.category_id = self.client.post(
            reverse("personcategory-list"),
            {"code": "prof", "name": "Profesores", "description": "", "is_active": True},
            format="json",
        ).data["id"]
        person_payload = {
            "first_name": "Mario",
            "last_name": "Lopez",
            "dni": "20111222",
            "address": "Calle",
            "phone": "123",
            "email": "mario@example.com",
            "person_type": PersonType.EMPLOYEE,
            "person_category": self.category_id,
            "guest_type": None,
            "is_active": True,
        }
        self.person_id = self.client.post(reverse("person-list"), person_payload, format="json").data["id"]
        AccessEvent.objects.create(
            person_id=self.person_id,
            site_id=self.site_id,
            category_id=self.category_id,
            occurred_at=timezone.now(),
            source="test",
        )

    def test_access_by_category_report(self):
        response = self.client.get(reverse("report_access_by_category"), {"site": self.site_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["site"], self.site_id)
        self.assertTrue(response.data["by_category"])

    def test_access_heatmap_report(self):
        response = self.client.get(reverse("report_access_heatmap"), {"site": self.site_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["site"], self.site_id)
        self.assertTrue(response.data["heatmap"])


class ParkingMovementAPITestCase(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.lookup_url = reverse("parking_client_lookup")
        self.movements_url = reverse("parking_movements_api")
        self.mark_exit_url_name = "parking_movement_mark_exit_api"
        Cliente.objects.create(id_cliente=1, doc_nro=30111222, ult_cuota_paga=timezone.now())

    def test_lookup_requires_dni(self):
        self.authenticate()
        response = self.client.get(self.lookup_url)
        self.assertEqual(response.status_code, 400)

    def test_lookup_returns_ult_cuota_paga(self):
        self.authenticate()
        response = self.client.get(self.lookup_url, {"dni": 30111222})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["found"])
        self.assertEqual(response.data["source"], "local")
        self.assertIn("ult_cuota_paga", response.data)
        self.assertIn("can_enter", response.data)
        self.assertIn("access_until", response.data)

    def test_lookup_marks_member_as_enabled_within_60_days(self):
        self.authenticate()
        Cliente.objects.filter(doc_nro=30111222).update(
            ult_cuota_paga=timezone.make_aware(datetime(2024, 1, 20, 8, 0, 0))
        )

        with patch("access_control.views.timezone.localdate", return_value=date(2024, 2, 25)):
            response = self.client.get(self.lookup_url, {"dni": 30111222})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["can_enter"])
        self.assertEqual(response.data["access_until"], "2024-03-01")

    def test_lookup_marks_member_as_disabled_after_60_days(self):
        self.authenticate()
        Cliente.objects.filter(doc_nro=30111222).update(
            ult_cuota_paga=timezone.make_aware(datetime(2024, 1, 20, 8, 0, 0))
        )

        with patch("access_control.views.timezone.localdate", return_value=date(2024, 3, 2)):
            response = self.client.get(self.lookup_url, {"dni": 30111222})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["can_enter"])
        self.assertEqual(response.data["access_until"], "2024-03-01")

    def test_lookup_falls_back_to_mssql_when_not_found_locally(self):
        self.authenticate()
        Cliente.objects.all().delete()
        mssql_payload = {
            "id_cliente": 999,
            "doc_nro": 30222333,
            "ult_cuota_paga": timezone.now(),
        }
        with patch("access_control.views.MSSQLClientLookupService.fetch_by_dni", return_value=mssql_payload):
            response = self.client.get(self.lookup_url, {"dni": 30222333})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["found"])
        self.assertEqual(response.data["source"], "mssql")
        self.assertEqual(response.data["id_cliente"], 999)

    def test_lookup_returns_not_found_when_mssql_has_no_data(self):
        self.authenticate()
        Cliente.objects.all().delete()
        with patch("access_control.views.MSSQLClientLookupService.fetch_by_dni", return_value=None):
            response = self.client.get(self.lookup_url, {"dni": 30222333})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["found"])

    def test_create_movement_stores_registry(self):
        self.authenticate()
        payload = {"dni": 30111222, "patente": "aa123bb", "movement_type": "entry"}
        response = self.client.post(self.movements_url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        movement = ParkingMovement.objects.get(id=response.data["id"])
        self.assertEqual(movement.patente, "AA123BB")
        self.assertEqual(movement.movement_type, "entry")
        self.assertIsNone(movement.exit_at)
        self.assertIsNone(movement.stay_duration)


    def test_mark_exit_updates_exit_time_and_stay_duration(self):
        self.authenticate()
        movement = ParkingMovement.objects.create(
            dni=30111222,
            patente="AA123BB",
            movement_type=ParkingMovement.MovementType.ENTRY,
        )

        with patch("access_control.views.timezone.now", return_value=movement.created_at + timedelta(minutes=95)):
            response = self.client.post(
                reverse(self.mark_exit_url_name, kwargs={"movement_id": movement.id}),
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        movement.refresh_from_db()
        self.assertIsNotNone(movement.exit_at)
        self.assertEqual(int(movement.stay_duration.total_seconds()), 95 * 60)
        self.assertEqual(response.data["stay_duration_seconds"], 95 * 60)

    def test_mark_exit_rejects_non_entry_movement(self):
        self.authenticate()
        movement = ParkingMovement.objects.create(
            dni=30111222,
            patente="AA123BB",
            movement_type=ParkingMovement.MovementType.EXIT,
        )

        response = self.client.post(
            reverse(self.mark_exit_url_name, kwargs={"movement_id": movement.id}),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_create_movement_validates_type(self):
        self.authenticate()
        payload = {"dni": 30111222, "patente": "AA123BB", "movement_type": "invalid"}
        response = self.client.post(self.movements_url, payload, format="json")
        self.assertEqual(response.status_code, 400)

    def test_list_movements_returns_service_unavailable_when_table_missing(self):
        self.authenticate()
        with patch("access_control.views.ParkingMovement.objects") as mocked_manager:
            mocked_manager.all.return_value.values.side_effect = OperationalError("no such table")
            response = self.client.get(self.movements_url)

        self.assertEqual(response.status_code, 503)
        self.assertIn("migraciones", response.data["detail"])

    def test_create_movement_returns_service_unavailable_when_table_missing(self):
        self.authenticate()
        payload = {"dni": 30111222, "patente": "AA123BB", "movement_type": "entry"}
        with patch("access_control.views.ParkingMovement.objects.create", side_effect=OperationalError("no such table")):
            response = self.client.post(self.movements_url, payload, format="json")

        self.assertEqual(response.status_code, 503)
        self.assertIn("migraciones", response.data["detail"])


class AnsesVerificationAPITestCase(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.authenticate()
        self.candidates_url = reverse("anses_candidates_api")
        self.export_url = reverse("anses_processed_export_api")
        self.verify_url = reverse("anses_verify_api")

    @patch("access_control.api.v1.api_views.AnsesVerificationService")
    def test_candidates_pagination_with_age_range(self, service_cls):
        service = service_cls.return_value
        service.fetch_candidates.return_value = {
            "count": 123,
            "results": [
                {
                    "id_cliente": 10,
                    "doc_nro": 30111222,
                    "nombre": "Ana",
                    "apellido": "Perez",
                    "sexo": "F",
                    "fecha_nac": "1930-01-01",
                    "edad": 96,
                }
            ],
        }
        response = self.client.get(self.candidates_url, {"page": 2, "page_size": 50, "min_age": 95, "max_age": 100})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 123)
        self.assertEqual(response.data["page"], 2)
        service.fetch_candidates.assert_called_once_with(min_age=95, max_age=100, limit=50, offset=50)

    @patch("access_control.api.v1.api_views.AnsesVerificationService")
    def test_verify_clients_persists_consulted_by_user(self, service_cls):
        service_cls.return_value.run_verification.return_value = {"returncode": 0}
        payload = {
            "clients": [
                {
                    "id_cliente": 101,
                    "doc_nro": 30111222,
                    "apellido": "Pérez",
                    "nombre": "Ana",
                    "fecha_nac": "1930-01-01",
                    "edad": 96,
                }
            ],
            "headless": True,
            "no_download": True,
        }

        response = self.client.post(self.verify_url, payload, format="json")

        self.assertEqual(response.status_code, 200)
        record = AnsesVerificationRecord.objects.filter(requested_by=self.user, id_cliente=101, dni=30111222).first()
        self.assertIsNotNone(record)
        self.assertEqual(record.apellido, "Pérez")
        self.assertEqual(record.nombre, "Ana")
        self.assertEqual(record.fecha_nacimiento.isoformat(), "1930-01-01")
        self.assertEqual(record.edad, 96)

    def test_export_processed_records_uses_local_snapshot_when_cliente_is_missing(self):
        AnsesVerificationRecord.objects.create(
            requested_by=self.user,
            id_cliente=7100,
            dni=30222444,
            verification_status=AnsesVerificationRecord.VerificationStatus.GENERATED,
            verification_message="constancia generada.",
            apellido="Gomez",
            nombre="Lidia",
            fecha_nacimiento=datetime(1932, 3, 10).date(),
            edad=94,
        )
        response = self.client.get(self.export_url)

        self.assertEqual(response.status_code, 200)
        workbook = zipfile.ZipFile(BytesIO(response.content))
        sheet_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("<t>7100</t>", sheet_xml)
        self.assertIn("<t>Gomez</t>", sheet_xml)
        self.assertIn("<t>Lidia</t>", sheet_xml)
        self.assertIn("<t>1932-03-10</t>", sheet_xml)
        self.assertIn("<t>94</t>", sheet_xml)

    def test_export_processed_records_as_excel_file(self):
        Cliente.objects.create(
            id_cliente=7001,
            apellido="Perez",
            nombre="Ana",
            fecha_nac=timezone.make_aware(datetime(1930, 1, 1)),
            doc_nro=30222333,
        )
        AnsesVerificationRecord.objects.create(
            requested_by=self.user,
            id_cliente=7001,
            dni=30222333,
            verification_status=AnsesVerificationRecord.VerificationStatus.GENERATED,
            verification_message="constancia generada.",
        )

        response = self.client.get(self.export_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn(".xlsx", response["Content-Disposition"])
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        workbook = zipfile.ZipFile(BytesIO(response.content))
        sheet_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("<t>Numero</t>", sheet_xml)
        self.assertIn("<t>Apellido</t>", sheet_xml)
        self.assertIn("<t>Nombre</t>", sheet_xml)
        self.assertIn("<t>Fecha Nacimiento</t>", sheet_xml)
        self.assertIn("<t>Edad</t>", sheet_xml)
        self.assertIn("<t>Procesado</t>", sheet_xml)
        self.assertIn("<t>7001</t>", sheet_xml)
        self.assertIn("<t>Perez</t>", sheet_xml)
        self.assertIn("<t>Ana</t>", sheet_xml)
        self.assertIn("<t>1930-01-01</t>", sheet_xml)
        self.assertIn("<t>Si</t>", sheet_xml)
        self.assertIn("<t>constancia generada.</t>", sheet_xml)

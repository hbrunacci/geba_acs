from django.test import TestCase

from access_control.models import BiostarAccessEvent
from access_control.services import biostar_events as be


class ClasificarEventoTests(TestCase):
    def test_concedido(self):
        for name in ("IDENTIFY_SUCCESS_FACE", "VERIFY_SUCCESS_ID_FACE", "IDENTIFY_DURESS_FACE_FINGER"):
            self.assertEqual(be.clasificar_evento(name), (True, True), name)

    def test_denegado(self):
        for name in ("IDENTIFY_FAIL_FACE", "VERIFY_FAIL_FACE", "AUTH_FAILED_TIMEOUT", "ACCESS_DENIED_EXPIRED"):
            self.assertEqual(be.clasificar_evento(name), (True, False), name)

    def test_no_es_acceso(self):
        for name in ("UPDATE_SUCCESS", "DELETE_SUCCESS", "ENROLL_FAIL(VISUAL FACE)", "", None):
            self.assertEqual(be.clasificar_evento(name), (False, False), name)

    def test_parse_server_datetime_utc(self):
        dt = be.parse_server_datetime("2026-07-24T14:52:57.00Z")
        self.assertIsNotNone(dt)
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2026, 7, 24, 14, 52))
        self.assertEqual(dt.utcoffset().total_seconds(), 0)  # aware UTC
        self.assertIsNone(be.parse_server_datetime(""))


class _FakeClient:
    """Cliente BioStar de mentira: devuelve una lista fija de eventos."""

    def __init__(self, rows):
        self._rows = rows

    def events_search(self, *, device_id=None, limit=200, descending=True):
        return list(self._rows)


class IngestDeviceEventsTests(TestCase):
    def setUp(self):
        self.types = {
            "4867": "IDENTIFY_SUCCESS_FACE",
            "6147": "AUTH_FAILED_TIMEOUT",
            "8704": "UPDATE_SUCCESS",
        }
        self.rows = [
            {  # acceso concedido con socio
                "id": "399603", "server_datetime": "2026-07-24T14:52:57.00Z",
                "datetime": "2026-07-24T17:53:02.00Z",
                "device_id": {"id": "538150641", "name": "Facial_Ombues_1"},
                "user_id": {"user_id": "916671", "name": "CONDE"},
                "event_type_id": {"code": "4867"},
            },
            {  # denegado sin socio
                "id": "399590", "server_datetime": "2026-07-24T14:50:00.00Z",
                "device_id": {"id": "538150641", "name": "Facial_Ombues_1"},
                "user_id": {"user_id": "0"},
                "event_type_id": {"code": "6147"},
            },
            {  # ruido de sync: NO debe persistirse
                "id": "399588", "server_datetime": "2026-07-24T14:49:00.00Z",
                "device_id": {"id": "538150641", "name": "Facial_Ombues_1"},
                "event_type_id": {"code": "8704"},
            },
        ]

    def test_ingesta_filtra_ruido_y_mapea_campos(self):
        client = _FakeClient(self.rows)
        nuevos = be.ingest_device_events(client, self.types, 538150641, "Facial_Ombues_1")
        self.assertEqual(nuevos, 2)  # el UPDATE_SUCCESS se descarta
        self.assertEqual(BiostarAccessEvent.objects.count(), 2)

        ok = BiostarAccessEvent.objects.get(biostar_id="399603")
        self.assertEqual(ok.device_id, 538150641)
        self.assertEqual(ok.device_name, "Facial_Ombues_1")
        self.assertEqual(ok.id_cliente, 916671)
        self.assertTrue(ok.permitido)
        self.assertEqual(ok.event_name, "IDENTIFY_SUCCESS_FACE")
        self.assertEqual((ok.fecha.hour, ok.fecha.minute), (14, 52))  # server_datetime, no el datetime desfasado

        deneg = BiostarAccessEvent.objects.get(biostar_id="399590")
        self.assertIsNone(deneg.id_cliente)
        self.assertFalse(deneg.permitido)

    def test_ingesta_idempotente(self):
        client = _FakeClient(self.rows)
        be.ingest_device_events(client, self.types, 538150641, "Facial_Ombues_1")
        nuevos2 = be.ingest_device_events(client, self.types, 538150641, "Facial_Ombues_1")
        self.assertEqual(nuevos2, 0)
        self.assertEqual(BiostarAccessEvent.objects.count(), 2)

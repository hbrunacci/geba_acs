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
    """Cliente BioStar de mentira: guarda una lista de eventos crudos y responde
    events_search filtrando por after_id (id > after_id) y ordenando por id."""

    def __init__(self, rows):
        self._rows = list(rows)

    def _id(self, r):
        try:
            return int(r.get("id"))
        except (TypeError, ValueError):
            return 0

    def events_search(self, *, device_id=None, after_id=None, limit=200, order_column="id", descending=False):
        rows = self._rows
        if after_id is not None:
            rows = [r for r in rows if self._id(r) > int(after_id)]
        rows = sorted(rows, key=self._id, reverse=bool(descending))
        return rows[: int(limit)]


def _ev(eid, code, *, user="916671", dev="538150641", name="Facial_Ombues_1", ts="2026-07-24T14:52:57.00Z"):
    e = {
        "id": str(eid), "server_datetime": ts,
        "device_id": {"id": dev, "name": name},
        "event_type_id": {"code": code},
    }
    if user is not None:
        e["user_id"] = {"user_id": user}
    return e


class IngestNewEventsTests(TestCase):
    def setUp(self):
        self.types = {
            "4867": "IDENTIFY_SUCCESS_FACE",
            "6147": "AUTH_FAILED_TIMEOUT",
            "8704": "UPDATE_SUCCESS",
        }
        self.rows = [
            _ev(399603, "4867", user="916671"),                 # acceso concedido
            _ev(399590, "6147", user="0"),                       # denegado sin socio
            _ev(399588, "8704", user=None),                      # ruido de sync -> descartar
        ]

    def test_incremental_filtra_ruido_y_avanza_highwater(self):
        client = _FakeClient(self.rows)
        nuevos, last = be.ingest_new_events(client, self.types, 0)
        self.assertEqual(nuevos, 2)                       # el UPDATE_SUCCESS se descarta
        self.assertEqual(last, 399603)                    # high-water = max id visto (incluye ruido)
        self.assertEqual(BiostarAccessEvent.objects.count(), 2)

        ok = BiostarAccessEvent.objects.get(biostar_id="399603")
        self.assertEqual(ok.device_id, 538150641)
        self.assertEqual(ok.id_cliente, 916671)
        self.assertTrue(ok.permitido)
        self.assertEqual((ok.fecha.hour, ok.fecha.minute), (14, 52))  # server_datetime, no datetime desfasado
        self.assertIsNone(BiostarAccessEvent.objects.get(biostar_id="399590").id_cliente)

    def test_incremental_solo_trae_lo_nuevo(self):
        client = _FakeClient(self.rows)
        # ya procesamos hasta 399590 -> solo debe entrar el 399603
        nuevos, last = be.ingest_new_events(client, self.types, 399590)
        self.assertEqual(nuevos, 1)
        self.assertEqual(last, 399603)
        self.assertEqual(BiostarAccessEvent.objects.count(), 1)
        # segundo ciclo desde el high-water: nada nuevo
        nuevos2, last2 = be.ingest_new_events(client, self.types, last)
        self.assertEqual(nuevos2, 0)
        self.assertEqual(last2, 399603)

    def test_incremental_pagina(self):
        # 5 accesos con ids 1..5; con limit=2 debe paginar y traerlos todos
        rows = [_ev(i, "4867", user=str(900000 + i)) for i in range(1, 6)]
        client = _FakeClient(rows)
        nuevos, last = be.ingest_new_events(client, self.types, 0, limit=2)
        self.assertEqual(nuevos, 5)
        self.assertEqual(last, 5)

    def test_backfill(self):
        client = _FakeClient(self.rows)
        nuevos, mx = be.ingest_backfill(client, self.types, limit=1000)
        self.assertEqual(nuevos, 2)
        self.assertEqual(mx, 399603)

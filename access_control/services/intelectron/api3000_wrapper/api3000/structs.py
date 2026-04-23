from __future__ import annotations

from ctypes import (
    Array,
    Structure,
    c_byte,
    c_char,
    c_int16,
    c_int32,
    c_uint8,
)
from dataclasses import dataclass
from datetime import datetime


class ITKDateTime(Structure):
    """Equivalente ctypes de `ITK_DATE_TIME` del ejemplo VBA."""

    _pack_ = 1
    _fields_ = [
        ("hour", c_uint8),
        ("minute", c_uint8),
        ("seconds", c_uint8),
        ("year", c_uint8),
        ("month", c_uint8),
        ("day", c_uint8),
        ("dayofweek", c_uint8),
    ]

    def to_python_datetime(self, century: int = 2000) -> datetime:
        """Convierte a `datetime` de Python.

        El año en la estructura se guarda en dos dígitos.
        """
        return datetime(
            year=century + int(self.year),
            month=int(self.month),
            day=int(self.day),
            hour=int(self.hour),
            minute=int(self.minute),
            second=int(self.seconds),
        )

    @classmethod
    def from_datetime(cls, value: datetime) -> "ITKDateTime":
        """Construye la estructura a partir de un `datetime`."""
        return cls(
            hour=value.hour,
            minute=value.minute,
            seconds=value.second,
            year=value.year % 100,
            month=value.month,
            day=value.day,
            dayofweek=value.isoweekday() % 7,
        )


class ITKUserInfo(Structure):
    """Equivalente ctypes de `ITK_USER_INFO`."""

    _pack_ = 1
    _fields_ = [
        ("mask_fields", c_int32),
        ("access_id", c_int32),
        ("password", c_int16),
        ("status", c_uint8),
        ("access_ctl", c_int16),
        ("panic_code", c_uint8),
        ("bio_count", c_uint8),
        ("bio_level", c_uint8),
        ("sec_level", c_uint8),
        ("user_name", c_char * 256),
        ("user_msg", c_char * 256),
        ("user_id", c_char * 21),
        ("schedule_id", c_int16),
        ("anti_passback", c_uint8),
    ]

    def set_user_name(self, value: str) -> None:
        self.user_name = _encode_fixed(value, 256)

    def set_user_msg(self, value: str) -> None:
        self.user_msg = _encode_fixed(value, 256)

    def set_user_id(self, value: str) -> None:
        self.user_id = _encode_fixed(value, 21)

    def get_user_name(self) -> str:
        return _decode_fixed(self.user_name)

    def get_user_msg(self) -> str:
        return _decode_fixed(self.user_msg)

    def get_user_id(self) -> str:
        return _decode_fixed(self.user_id)


class RELEInfo(Structure):
    """Equivalente ctypes de `RELE_INFO`."""

    _pack_ = 1
    _fields_ = [
        ("id", c_uint8),
        ("time", c_uint8),
    ]


class ITKAuxInput(Structure):
    """Equivalente ctypes de `ITK_AUX_INPUT`."""

    _pack_ = 1
    _fields_ = [
        ("invert_input", c_uint8),
        ("ev_status", c_uint8),
        ("time_on", c_uint8),
        ("time_off", c_uint8),
        ("ev_host", c_uint8),
        ("ev_historic", c_uint8),
        ("ev_in_condition", c_int32),
        ("ev_rele_condition", c_int32),
        ("schedule_id", c_int16),
        ("rele", RELEInfo * 4),
    ]


class ITKMarkInfo(Structure):
    """Equivalente ctypes de `ITK_MARK_INFO`."""

    _pack_ = 1
    _fields_ = [
        ("mask_fields", c_int32),
        ("type", c_uint8),
        ("access_id", c_int32),
        ("date_time", ITKDateTime),
        ("event_code", c_uint8),
        ("source", c_int16),
        ("direction", c_uint8),
        ("supervisor_id", c_int32),
        ("task_item_id", c_uint8),
        ("job_order", c_char * 10),
    ]

    def get_job_order(self) -> str:
        return _decode_fixed(self.job_order)


class ITKScheduleHeader(Structure):
    """Equivalente ctypes de `ITK_SCHD_HEADER`."""

    _pack_ = 1
    _fields_ = [
        ("id", c_int16),
        ("name", c_char * 21),
        ("hours", c_int32),
        ("segments", c_int32),
        ("start_date", ITKDateTime),
    ]

    def set_name(self, value: str) -> None:
        self.name = _encode_fixed(value, 21)

    def get_name(self) -> str:
        return _decode_fixed(self.name)


class ITKScheduleSegment(Structure):
    """Equivalente ctypes de `ITK_SCHD_SEGMENT`."""

    _pack_ = 1
    _fields_ = [
        ("hours", c_uint8),
        ("minutes", c_uint8),
        ("enabled", c_uint8),
    ]


class ITKScheduleInfo(Structure):
    """Equivalente ctypes de `ITK_SCHD_INFO`."""

    _pack_ = 1
    _fields_ = [
        ("id", c_int16),
        ("name", c_char * 21),
    ]

    def get_name(self) -> str:
        return _decode_fixed(self.name)


class ITKDisplayMessageInfo(Structure):
    """Equivalente ctypes de `ITK_DSP_MSG_INFO`."""

    _pack_ = 1
    _fields_ = [
        ("line", c_int16),
        ("pos", c_int16),
        ("cmd_line", c_uint8),
        ("cmd_pos", c_uint8),
        ("font", c_int16),
        ("data", c_char * 50),
    ]

    def set_data(self, value: str) -> None:
        self.data = _encode_fixed(value, 50)

    def get_data(self) -> str:
        return _decode_fixed(self.data)


class ITKDisplayMessage2Info(Structure):
    """Contenedor de dos líneas para `itk_display_msg`."""

    _pack_ = 1
    _fields_ = [
        ("line1", ITKDisplayMessageInfo),
        ("line2", ITKDisplayMessageInfo),
    ]


def _decode_fixed(value: Array[c_char]) -> str:
    raw = bytes(value)
    return raw.split(b"\x00", 1)[0].decode("latin-1", errors="ignore").rstrip()


def _encode_fixed(value: str, size: int) -> bytes:
    raw = value.encode("latin-1", errors="ignore")[: max(0, size - 1)]
    return raw + b"\x00" * (size - len(raw))

from __future__ import annotations

from enum import IntEnum


class PacketProtocol(IntEnum):
    """Protocolos de mensajería observados en el ejemplo VBA."""

    IN1 = 1
    NEXT = 2


class TableId(IntEnum):
    """Identificadores de tabla vistos en el ejemplo VBA."""

    USER = 0
    HISTORIC = 1
    TASK = 2


class OpenFlags(IntEnum):
    """Flags para operaciones de archivos sobre la SD."""

    O_RDONLY = 0
    O_WRONLY = 1
    O_RDWR = 2
    O_APPEND = 8
    O_CREAT = 512


class UserStatus(IntEnum):
    """Estados de usuario observados en `itkcom_structs.bas`."""

    ENABLED = 1
    DISABLED = 108
    VACATION = 146
    SICK = 145


class HistoryEvent(IntEnum):
    """Eventos históricos relevantes."""

    UNKNOWN = 0
    OK = 1
    INTRUDER = 106
    INVALID_FINGER = 146
    DISABLED = 108
    PASSWORD = 150
    SICK = 148
    NOT_AUTHORIZED = 107


class HistoryDirection(IntEnum):
    """Sentido de paso informado por el histórico."""

    UNKNOWN = 0
    IN = 200
    OUT = 201
    INTER_IN = 202
    INTER_OUT = 203
    SOFTWARE = 204

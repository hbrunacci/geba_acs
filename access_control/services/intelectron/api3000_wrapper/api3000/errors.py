from __future__ import annotations

from dataclasses import dataclass


ERROR_MESSAGES: dict[int, str] = {
    1: "operacion exitosa",
    2: "parametros invalidos",
    3: "la tabla de usuarios esta llena",
    4: "error en la tabla de usuarios",
    5: "el usuario ya existe",
    7: "la tabla de usuarios esta vacia",
    8: "fin de tabla",
    10: "error en el sensor biometrico",
    11: "el template no existe",
    12: "el sensor biometrico no esta presente",
    15: "el template es invalido",
    16: "error desconocido",
    17: "la tabla de historico esta llena",
    18: "error en la tabla de historico",
    21: "la tabla de historico esta vacia",
    22: "la tabla no existe",
    23: "identificador de parametro numerico invalido",
    24: "identificador de entrada auxiliar invalido",
    30: "no se pudo abrir el archivo",
    31: "fin de archivo",
    32: "archivo cerrado",
    33: "error escribiendo archivo",
    34: "error borrando archivo",
    106: "el usuario no existe",
    147: "parametros invalidos",
    151: "el usuario biometrico no existe",
    156: "no se pueden dar mas usuarios de alta",
    200: "formato de template incorrecto",
    1000: "memoria insuficiente",
    1001: "connection string invalido",
    1002: "no se pudo abrir el puerto serie",
    1003: "nombre de dispositivo invalido",
    1004: "baudrate invalido",
    1005: "error configurando el puerto serie",
    1006: "puerto serie cerrado",
    1007: "no se pudo crear el thread de recepcion",
    1008: "host invalido",
    1009: "direccion IP invalida",
    1010: "no se pudo crear el socket",
    1011: "no se pudo conectar al nodo",
    1012: "no se pudo obtener estado del socket",
    1013: "estado de conexion incorrecto",
    1014: "timeout estableciendo conexion",
    1021: "timeout en transferencia",
    1024: "nodo destino invalido",
    1026: "timeout esperando paquete",
    1028: "respuesta fuera de secuencia",
    1030: "socket TCP cerrado",
    1031: "la libreria ya fue inicializada",
    1034: "la libreria no fue inicializada",
    1035: "protocolo de paquete invalido",
    1036: "handle de link invalido",
    1038: "comando no soportado por la libreria",
    1040: "no se pudo cerrar el thread de recepcion",
    1042: "no se pudo bindear el socket",
    1043: "callback no configurado",
    1046: "el socket no pudo entrar en listen",
    1063: "fallo keepalive",
}


class Api3000Error(Exception):
    """Error base del wrapper API-3000."""


@dataclass(slots=True)
class Api3000NativeError(Api3000Error):
    """Error producido por la librería nativa."""

    code: int
    operation: str

    def __str__(self) -> str:
        message = ERROR_MESSAGES.get(self.code, "error no mapeado")
        return f"{self.operation} fallo con codigo {self.code}: {message}"


def ensure_ok(code: int, operation: str) -> int:
    """Valida el código devuelto por la librería nativa.

    La documentación VBA usa `OP_OK = 1` como operación exitosa.
    """
    if code != 1:
        raise Api3000NativeError(code=code, operation=operation)
    return code

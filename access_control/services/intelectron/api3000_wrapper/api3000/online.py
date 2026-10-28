"""Modo ONLINE de la API-3000/4000: la placa pregunta y el host decide.

Cómo funciona (manual "Guía de configuración API-3000" + DLLs de la app de
Clever, CapaLogica_INREIN, reflejadas el 28/10/2026):

* El equipo es SERVIDOR TCP: nosotros abrimos la sesión con ``itk_open`` y le
  pasamos tres callbacks (evento de usuario, entrada auxiliar, conexión). Con
  "Modo Envío" = Online u Online/Offline y la casilla "Comunicación" marcada en
  Acciones, cada lectura llega como ``EvUser`` con ``event_code = 2``
  (PorAutorizar) y el equipo ESPERA la respuesta.
* Se responde con ``itk_answer_user_event`` y una estructura cuyo
  ``user_info.status`` decide (1 = habilitado, 108 = deshabilitado) y cuyo
  ``user_info.access_ctl`` dice qué lector acciona (bit ``1 << source_id``).
  El relé y el tiempo los pone el equipo según su propia configuración de
  "Aceptado" para ese lector.
* Si el host no contesta antes de CFG_HOST_TIMEOUT (ms) el equipo decide solo
  (offline) o rechaza, según su modo.

Firmas y layouts: sacados por reflexión de ``CapaLogica_INREIN.Funciones``
(P/Invoke) y ``CapaLogica_INREIN.Estructuras`` de la app que Clever usa en
producción, así que son los que la librería realmente espera. Las estructuras
van con alineación natural (NO ``_pack_ = 1``): en la DLL ``access_ctl`` está en
el offset 12, no en el 11.
"""

from __future__ import annotations

import logging
import threading
import time
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    byref,
    c_char,
    c_char_p,
    c_long,
    c_uint8,
    c_uint16,
    c_uint32,
    c_void_p,
    cast,
    sizeof,
)
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .constants import PacketProtocol
from .errors import Api3000NativeError, ensure_ok
from .native import NativeLibrary
from .structs import ITKDateTime

logger = logging.getLogger(__name__)

# --- Códigos (Enums de CapaEntidades_INREIN) --------------------------------

EV_ENTRADA_AUXILIAR = 0
EV_ACEPTADO = 1
EV_POR_AUTORIZAR = 2          # la placa pide decisión al host
EV_NO_IDENTIFICADO = 106
EV_NO_AUTORIZADO = 107
EV_SUSPENDIDO = 108
EV_HUELLA_INVALIDA = 146
EV_FUERA_HORARIO = 148
EV_PUERTA_EXCLUSA = 149
EV_CLAVE_INVALIDA = 150

EVENT_NAMES = {
    EV_ENTRADA_AUXILIAR: "Entrada auxiliar",
    EV_ACEPTADO: "Aceptado",
    EV_POR_AUTORIZAR: "Por autorizar",
    EV_NO_IDENTIFICADO: "No identificado",
    EV_NO_AUTORIZADO: "No autorizado",
    EV_SUSPENDIDO: "Suspendido",
    EV_HUELLA_INVALIDA: "Huella inválida",
    EV_FUERA_HORARIO: "Fuera de horario",
    EV_PUERTA_EXCLUSA: "Puerta exclusa",
    EV_CLAVE_INVALIDA: "Clave inválida",
}

DIR_IN = 200
DIR_OUT = 201
DIR_INTER_IN = 202
DIR_INTER_OUT = 203
DIR_SOFTWARE = 204

STATUS_ENABLED = 1
STATUS_DISABLED = 108

# MaskFieldEnum
FLD_USER_ACCESS_ID = 1
FLD_USER_STATUS = 4
FLD_USER_ACCESS_CTL = 8
FLD_USER_UMSG = 512
FLD_USER_TODO = 4095

ACCESS_CTL_TOTAL = 4095

CONN_CLOSED_BY_DEVICE = 2     # cause del callback de conexión cuando el equipo corta
PROC_NET_TCP = "/proc/net/tcp"  # tabla TCP del kernel (Linux) que mira el watchdog de socket


# --- Estructuras ---------------------------------------------------------------


class ITKUserEvent(Structure):
    """``EventoUsuario`` (EV_USER_ITK): lo que llega en el callback de usuario."""

    _fields_ = [
        ("access_id", c_uint32),      # @0  credencial como entero sin signo
        ("source_id", c_uint16),      # @4  0 = lector 1, 1 = lector 2, 4 = bio 1, 5 = teclado...
        ("event_code", c_uint8),      # @6  EV_*
        ("direction", c_uint8),       # @7  DIR_*
        ("date_time", ITKDateTime),   # @8
    ]


class ITKAuxEvent(Structure):
    """``EventoEntradaAuxiliar`` (EV_AUX_INPUT_ITK): sensores."""

    _fields_ = [
        ("input_id", c_uint8),
        ("event_code", c_uint8),
        ("date_time", ITKDateTime),
    ]


class ITKOnlineUserInfo(Structure):
    """``Usuario`` (ITK_USER_INFO) con alineación natural, como en la DLL de Clever.

    Offsets verificados contra la reflexión: status @10, access_ctl @12,
    user_name @18, user_msg @274, user_id @530, schedule_id @552.
    """

    _fields_ = [
        ("mask_fields", c_uint32),
        ("access_id", c_uint32),
        ("password", c_uint16),
        ("status", c_uint8),
        ("access_ctl", c_uint16),
        ("panic_code", c_uint8),
        ("bio_count", c_uint8),
        ("bio_level", c_uint8),
        ("sec_level", c_uint8),
        ("user_name", c_char * 256),
        ("user_msg", c_char * 256),
        ("user_id", c_char * 21),
        ("schedule_id", c_uint16),
        ("anti_passback", c_uint8),
    ]


class ITKTicket(Structure):
    """``Ticket``: diez punteros a texto para la impresora. Nulos = sin ticket."""

    _fields_ = [(f"aux{i}", c_char_p) for i in range(10)]


class ITKAnswerUserEvent(Structure):
    """``RespuestaEventoUsuario`` (ANSWER_EV_USER_ITK): lo que se devuelve.

    En la DLL: date_time @0, user_info @8, print_enable @564, ticket @568.
    """

    _fields_ = [
        ("date_time", ITKDateTime),
        ("user_info", ITKOnlineUserInfo),
        ("print_enable", c_uint8),
        ("ticket", ITKTicket),
    ]


# Firmas de los callbacks (delegates de CapaLogica_INREIN.Funciones):
#   user_callback(args, h_link, src_node, EventoUsuario&)
#   input_callback(args, h_link, src_node, EventoEntradaAuxiliar&)
#   conn_callback(args, h_link, cause)
USER_CALLBACK = CFUNCTYPE(None, c_void_p, c_void_p, c_uint16, POINTER(ITKUserEvent))
AUX_CALLBACK = CFUNCTYPE(None, c_void_p, c_void_p, c_uint16, POINTER(ITKAuxEvent))
CONN_CALLBACK = CFUNCTYPE(None, c_void_p, c_long, c_long)


# --- Helpers ------------------------------------------------------------------


def tag_from_access_id(access_id: int) -> str:
    """Convierte el ``access_id`` de la placa al tag que guarda Clever.

    Clever guarda la credencial Mifare como el entero de 32 bits en hexadecimal
    MAYÚSCULA, con los bytes en el orden en que vienen en el cable (little
    endian). Verificado contra ``Clientes.Credencial_Nro`` el 28/10/2026:
    82531707 -> ``7B55EB04``.
    """
    value = int(access_id) & 0xFFFFFFFF
    return value.to_bytes(4, "little").hex().upper()


def tag_for_event(ev, lectores_hex: set[int] | None = None) -> str:
    """Tag a mandarle a Clever según el lector que leyó.

    En ``CD_Controladores_Lectores`` cada lector tiene ``Tipo_Conversion``:
    ``H08`` (credencial Mifare: hex de 8, ``source_id`` 1 en las placas del
    club) o ``D`` (lector de QR/DNI, ``source_id`` 0: el número va en decimal
    tal cual, y el SP lo interpreta como documento o token de QR dinámico).
    """
    lectores_hex = {1} if lectores_hex is None else lectores_hex
    if int(ev.source_id) in lectores_hex:
        return tag_from_access_id(ev.access_id)
    return str(int(ev.access_id) & 0xFFFFFFFF)


def tipo_acceso_from_event(ev, lectores_salida: set[int] | None = None) -> str:
    """'E' (entrada) o 'S' (salida) a partir del sentido que informa la placa.

    ``ev`` es cualquier objeto con ``direction`` y ``source_id`` (el struct o
    ``EventoUsuario``). Si el sentido viene desconocido se decide por el lector
    con la lista de lectores de salida.
    """
    direction = int(ev.direction)
    if direction in (DIR_IN, DIR_INTER_IN):
        return "E"
    if direction in (DIR_OUT, DIR_INTER_OUT):
        return "S"
    if lectores_salida and int(ev.source_id) in lectores_salida:
        return "S"
    return "E"


def build_answer(
    *,
    access_id: int,
    habilitado: bool,
    source_id: int,
    mensaje: str = "",
    cuando: datetime | None = None,
    access_ctl: int | None = None,
) -> ITKAnswerUserEvent:
    """Arma la respuesta para ``itk_answer_user_event``.

    ``access_ctl``: bit del lector que acciona (``1 << source_id``). Si se
    deniega va en 0 para que no se mueva ningún relé.
    """
    answer = ITKAnswerUserEvent()
    answer.date_time = ITKDateTime.from_datetime(cuando or datetime.now())
    ui = answer.user_info
    ui.mask_fields = FLD_USER_ACCESS_ID | FLD_USER_STATUS | FLD_USER_ACCESS_CTL | FLD_USER_UMSG
    ui.access_id = int(access_id) & 0xFFFFFFFF
    if habilitado:
        ui.status = STATUS_ENABLED
        ui.access_ctl = (1 << int(source_id)) & ACCESS_CTL_TOTAL if access_ctl is None else int(access_ctl)
    else:
        ui.status = STATUS_DISABLED
        ui.access_ctl = 0
    ui.user_msg = (mensaje or "")[:255].encode("latin-1", errors="replace")
    answer.print_enable = 0
    return answer


@dataclass
class EventoUsuario:
    """Copia Python del evento (el struct nativo sólo vale durante el callback)."""

    access_id: int
    source_id: int
    event_code: int
    direction: int
    device_time: datetime | None
    node: int
    recibido: datetime = field(default_factory=datetime.now)

    @property
    def tag(self) -> str:
        return tag_from_access_id(self.access_id)

    @property
    def event_name(self) -> str:
        return EVENT_NAMES.get(self.event_code, f"código {self.event_code}")

    @classmethod
    def from_struct(cls, ev: ITKUserEvent, node: int) -> "EventoUsuario":
        try:
            dt = ev.date_time.to_python_datetime()
        except Exception:
            dt = None
        return cls(
            access_id=int(ev.access_id),
            source_id=int(ev.source_id),
            event_code=int(ev.event_code),
            direction=int(ev.direction),
            device_time=dt,
            node=int(node),
        )


class OnlineSession:
    """Sesión ``itk_open`` con callbacks y reconexión automática.

    ``on_user(evento) -> ITKAnswerUserEvent | None`` corre EN EL HILO NATIVO de
    recepción de la librería, con el equipo esperando: tiene que ser rápido
    (decenas de ms) y nunca lanzar. Si devuelve una respuesta, se envía con
    ``itk_answer_user_event`` ahí mismo.

    El equipo cierra la conexión cuando no se contesta su EcoTest (parámetro
    "Timeout Conexión"); llega ``conn cause=2`` y ``mantener()`` reconecta.
    """

    def __init__(
        self,
        *,
        ip: str,
        port: int,
        dest_node: int = 1,
        source_node: int = 0,
        timeout_ms: int = 8000,
        on_user: Callable[[EventoUsuario], ITKAnswerUserEvent | None] | None = None,
        on_aux: Callable[[int, int], None] | None = None,
        on_conn: Callable[[int], None] | None = None,
        log_path: str = "/tmp/itk_online.log",
        log_level: int = 3,
        keepalive: tuple[int, int] | None = (30, 3),
        native: NativeLibrary | None = None,
    ) -> None:
        self.ip, self.port = ip, int(port)
        self.dest_node, self.source_node = int(dest_node), int(source_node)
        self.timeout_ms = int(timeout_ms)
        self.on_user, self.on_aux, self.on_conn = on_user, on_aux, on_conn
        self.log_path, self.log_level = log_path, int(log_level)
        self._native = native or NativeLibrary()
        self._lib = self._native.cdll
        self._configure_answer_signature()
        self._handle: int = 0
        self._viva = False
        self._lock = threading.Lock()
        self.reconexiones = 0
        self.caidas_socket = 0      # cortes detectados por el watchdog (sin aviso de la librería)
        self.eventos = 0
        self.respuestas = 0
        self.errores_respuesta = 0
        # Las referencias a los callbacks tienen que vivir tanto como la sesión:
        # si el GC las levanta, la librería llama a memoria liberada.
        self._cb_user = USER_CALLBACK(self._user_cb)
        self._cb_aux = AUX_CALLBACK(self._aux_cb)
        self._cb_conn = CONN_CALLBACK(self._conn_cb)
        # Keepalive (EcoTest) de la librería hacia el equipo: sin esto la placa
        # cierra la sesión al cumplirse su "Timeout Conexión" (100 s en las de
        # Clever) y hay que reconectar; Clever usa 60 s / 3 reintentos. Tiene
        # que configurarse ANTES de itk_init (después devuelve 1031).
        self.keepalive = keepalive
        if keepalive:
            timeout_s, retry = keepalive
            code = int(self._lib.itk_set_keepalive(int(timeout_s), int(retry), self.dest_node))
            try:
                ensure_ok(code, "itk_set_keepalive")   # OP_OK = 1; 1031 si ya hubo itk_init
            except Api3000NativeError as exc:
                logger.warning("%s", exc)
        self._lib.itk_init(self.log_path.encode("latin-1", "ignore"), self.log_level)

    # -- firmas que native.py todavía no declara ---------------------------------

    def _configure_answer_signature(self) -> None:
        lib = self._lib
        lib.itk_answer_user_event.argtypes = [c_long, c_uint16, POINTER(ITKAnswerUserEvent)]
        lib.itk_answer_user_event.restype = c_long

    # -- callbacks nativos -------------------------------------------------------

    def _user_cb(self, args, h_link, node, ev_ptr) -> None:
        self.eventos += 1
        try:
            evento = EventoUsuario.from_struct(ev_ptr.contents, node)
        except Exception:  # noqa: BLE001 - nunca propagar al hilo nativo
            logger.exception("evento de usuario ilegible")
            return
        if self.on_user is None:
            return
        try:
            answer = self.on_user(evento)
        except Exception:  # noqa: BLE001
            logger.exception("on_user falló; se deniega por seguridad")
            answer = build_answer(access_id=evento.access_id, habilitado=False,
                                  source_id=evento.source_id, mensaje="ERROR")
        if answer is not None:
            self.responder(answer, node=node)

    def _aux_cb(self, args, h_link, node, ev_ptr) -> None:
        try:
            ev = ev_ptr.contents
            if self.on_aux:
                self.on_aux(int(ev.input_id), int(ev.event_code))
        except Exception:  # noqa: BLE001
            logger.exception("evento auxiliar ilegible")

    def _conn_cb(self, args, h_link, cause) -> None:
        cause = int(cause)
        if cause == CONN_CLOSED_BY_DEVICE:
            self._viva = False
        if self.on_conn:
            try:
                self.on_conn(cause)
            except Exception:  # noqa: BLE001
                logger.exception("on_conn falló")

    # -- API ---------------------------------------------------------------------

    def responder(self, answer: ITKAnswerUserEvent, *, node: int | None = None) -> bool:
        with self._lock:
            handle = self._handle
        if handle <= 0:
            self.errores_respuesta += 1
            return False
        code = self._lib.itk_answer_user_event(
            c_long(handle), c_uint16(self.dest_node if node is None else int(node)), byref(answer)
        )
        try:
            ensure_ok(int(code), "itk_answer_user_event")
        except Api3000NativeError as exc:
            self.errores_respuesta += 1
            logger.warning("itk_answer_user_event: %s", exc)
            return False
        self.respuestas += 1
        return True

    def abrir(self) -> int:
        err = c_long(0)
        conn = f"{self.ip}:{self.port}".encode("latin-1", "ignore")
        handle = int(self._lib.itk_open(
            byref(err), self.source_node, int(PacketProtocol.NEXT), conn, self.timeout_ms,
            0,
            cast(self._cb_user, c_void_p).value,
            cast(self._cb_aux, c_void_p).value,
            cast(self._cb_conn, c_void_p).value,
        ))
        if handle <= 0:
            raise Api3000NativeError(code=int(err.value) or handle, operation=f"itk_open {self.ip}:{self.port}")
        with self._lock:
            self._handle = handle
        self._viva = True
        return handle

    def cerrar(self) -> None:
        with self._lock:
            handle, self._handle = self._handle, 0
        self._viva = False
        if handle > 0:
            try:
                self._lib.itk_close(c_long(handle))
            except Exception:  # noqa: BLE001
                pass

    def uninit(self) -> None:
        self.cerrar()
        try:
            self._lib.itk_uninit()
        except Exception:  # noqa: BLE001
            pass

    @property
    def viva(self) -> bool:
        return self._viva and self._handle > 0

    def hay_socket(self) -> bool | None:
        """¿Existe una conexión TCP ESTABLISHED hacia ``ip:port``?

        Mira ``/proc/net/tcp`` (Linux, que es donde corre el contenedor). Devuelve
        ``None`` cuando no se puede saber (Windows, sin permisos). Hace falta
        porque la placa a veces cierra la sesión sin que la librería avise por el
        callback de conexión (visto el 28/10/2026 al grabarle la configuración
        por la web): ``viva`` queda en True, nadie contesta y el molinete deniega.
        """
        try:
            with open(PROC_NET_TCP, encoding="ascii") as fh:
                lineas = fh.readlines()[1:]
        except OSError:
            return None
        try:
            octetos = [int(o) for o in self.ip.split(".")]
            # /proc/net/tcp muestra la IP como u32 en orden nativo (little-endian) en hex.
            remoto = "".join(f"{o:02X}" for o in reversed(octetos)) + f":{self.port:04X}"
        except ValueError:
            return None
        for linea in lineas:
            partes = linea.split()
            if len(partes) > 3 and partes[2].upper() == remoto and partes[3] == "01":
                return True
        return False

    def mantener(self, *, parar: Callable[[], bool], tick: float = 0.5,
                 reintento: float = 2.0, on_reconexion: Callable[[int], None] | None = None,
                 watchdog: float = 10.0, watchdog_fallas: int = 2) -> None:
        """Bucle de mantenimiento: reconecta cuando el equipo corta. Vuelve cuando ``parar()``.

        Cada ``watchdog`` segundos verifica con ``hay_socket()`` que la conexión
        TCP siga; tras ``watchdog_fallas`` comprobaciones seguidas sin socket da la
        sesión por muerta y reconecta (``caidas_socket`` las cuenta).
        """
        ultimo_chk = time.monotonic()
        fallas = 0
        while not parar():
            if self.viva and watchdog > 0 and time.monotonic() - ultimo_chk >= watchdog:
                ultimo_chk = time.monotonic()
                if self.hay_socket() is False:
                    fallas += 1
                    if fallas >= watchdog_fallas:
                        logger.warning("sin socket hacia %s:%s en %d comprobaciones: se reconecta",
                                       self.ip, self.port, fallas)
                        self.caidas_socket += 1
                        self._viva = False
                        fallas = 0
                else:
                    fallas = 0
            if not self.viva:
                fallas = 0
                ultimo_chk = time.monotonic()
                self.cerrar()
                try:
                    self.abrir()
                except Api3000NativeError as exc:
                    logger.warning("reconexión a %s:%s falló (%s); reintento en %.0fs", self.ip, self.port, exc, reintento)
                    time.sleep(reintento)
                    continue
                self.reconexiones += 1
                if on_reconexion:
                    on_reconexion(self.reconexiones)
            time.sleep(tick)


__all__ = [
    "ITKUserEvent", "ITKAuxEvent", "ITKOnlineUserInfo", "ITKTicket", "ITKAnswerUserEvent",
    "USER_CALLBACK", "AUX_CALLBACK", "CONN_CALLBACK",
    "EventoUsuario", "OnlineSession", "build_answer", "tag_from_access_id", "tag_for_event",
    "tipo_acceso_from_event",
    "EV_POR_AUTORIZAR", "EV_ACEPTADO", "EVENT_NAMES", "STATUS_ENABLED", "STATUS_DISABLED",
    "DIR_IN", "DIR_OUT",
]

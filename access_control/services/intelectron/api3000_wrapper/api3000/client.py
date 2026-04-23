from __future__ import annotations

from ctypes import byref, c_int16, c_long, c_uint8, create_string_buffer
from datetime import datetime
from typing import Iterable

from .constants import PacketProtocol
from .errors import Api3000Error, ensure_ok
from .native import NativeLibrary
from .structs import ITKAuxInput, ITKDateTime, ITKMarkInfo, ITKUserInfo


class Api3000Client:
    """Cliente Python de alto nivel para placas API-3000.

    Esta clase envuelve la librería nativa `libitkcom` usando `ctypes`.
    La versión inicial prioriza estabilidad y claridad para las primeras pruebas.
    """

    def __init__(
        self,
        *,
        lib_path: str | None = None,
        source_node: int = 1,
        packet_protocol: PacketProtocol = PacketProtocol.NEXT,
        conn_string: str | None = None,
        timeout: int = 5000,
        log_path: str = "itkcom_python.log",
        log_level: int = 5,
    ) -> None:
        self.source_node = int(source_node)
        self.packet_protocol = PacketProtocol(packet_protocol)
        self.conn_string = conn_string
        self.timeout = int(timeout)
        self.log_path = log_path
        self.log_level = int(log_level)

        self._native = NativeLibrary(lib_path)
        self._initialized = False
        self._handle: int | None = None

    @property
    def handle(self) -> int:
        """Retorna el handle nativo abierto."""
        if self._handle is None:
            raise Api3000Error("no hay una conexion abierta")
        return self._handle

    def __enter__(self) -> "Api3000Client":
        self.init_library()
        if self.conn_string:
            self.open(self.conn_string, timeout=self.timeout)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._handle is not None:
                self.close()
        finally:
            if self._initialized:
                self.uninit_library()

    def init_library(self) -> None:
        """Inicializa la librería nativa."""
        if self._initialized:
            return

        code = self._native.cdll.itk_init(
            self.log_path.encode("latin-1", errors="ignore"),
            self.log_level,
        )
        if int(code) == 1031:
            self._initialized = True
            return
        ensure_ok(code, "itk_init")
        self._initialized = True

    def uninit_library(self) -> None:
        """Libera la librería nativa."""
        if not self._initialized:
            return

        code = self._native.cdll.itk_uninit()
        if int(code) == 1034:
            self._initialized = False
            return
        ensure_ok(code, "itk_uninit")
        self._initialized = False

    def lib_version(self) -> str:
        """Retorna la versión reportada por la librería."""
        buff = create_string_buffer(256)
        self._native.cdll.itk_lib_version(buff)
        return buff.value.decode("latin-1", errors="ignore").strip()

    def set_keepalive(self, timeout: int, retry: int, dest_node: int) -> None:
        """Configura keepalive en la librería nativa."""
        code = self._native.cdll.itk_set_keepalive(timeout, retry, dest_node)
        ensure_ok(code, "itk_set_keepalive")

    def open(self, conn_string: str, *, timeout: int | None = None) -> int:
        """Abre la conexión con la placa.

        Ejemplo típico de `conn_string`:
        `192.168.0.10:3001`
        """
        if not self._initialized:
            self.init_library()

        error_code = c_long(0)
        handle = self._native.cdll.itk_open(
            byref(error_code),
            self.source_node,
            int(self.packet_protocol),
            conn_string.encode("latin-1", errors="ignore"),
            self.timeout if timeout is None else int(timeout),
            0,
            0,
            0,
            0,
        )

        if handle <= 0:
            code = int(error_code.value) if error_code.value else handle
            raise Api3000Error(f"itk_open fallo. codigo={code}, conn_string='{conn_string}'")

        self._handle = int(handle)
        self.conn_string = conn_string
        return self._handle

    def close(self) -> None:
        """Cierra la conexión abierta."""
        if self._handle is None:
            return

        code = self._native.cdll.itk_close(self._handle)
        ensure_ok(code, "itk_close")
        self._handle = None

    def set_timeouts(
        self,
        *,
        receive_timeout: int | None = None,
        send_timeout: int | None = None,
        interbyte_timeout: int | None = None,
    ) -> None:
        """Configura timeouts del link."""
        if receive_timeout is not None:
            ensure_ok(
                self._native.cdll.itk_set_rcv_timeout(self.handle, int(receive_timeout)),
                "itk_set_rcv_timeout",
            )

        if send_timeout is not None:
            ensure_ok(
                self._native.cdll.itk_set_snd_timeout(self.handle, int(send_timeout)),
                "itk_set_snd_timeout",
            )

        if interbyte_timeout is not None:
            ensure_ok(
                self._native.cdll.itk_set_interbyte_timeout(self.handle, int(interbyte_timeout)),
                "itk_set_interbyte_timeout",
            )

    def get_time(self, *, dest_node: int) -> ITKDateTime:
        """Lee fecha y hora del equipo."""
        date_time = ITKDateTime()
        code = self._native.cdll.itk_get_time(self.handle, dest_node, byref(date_time))
        ensure_ok(code, "itk_get_time")
        return date_time

    def get_time_as_datetime(self, *, dest_node: int, century: int = 2000) -> datetime:
        """Lee la hora del equipo y la convierte a `datetime`."""
        return self.get_time(dest_node=dest_node).to_python_datetime(century=century)

    def set_time(self, *, dest_node: int, value: datetime) -> None:
        """Escribe fecha y hora en el equipo."""
        date_time = ITKDateTime.from_datetime(value)
        code = self._native.cdll.itk_set_time(self.handle, dest_node, byref(date_time))
        ensure_ok(code, "itk_set_time")

    def get_numeric_config(self, *, dest_node: int, num_size: int, num_id: int) -> int:
        """Lee una configuración numérica del equipo."""
        value = c_long(0)
        code = self._native.cdll.itk_get_num_cfg(
            self.handle,
            dest_node,
            num_size,
            num_id,
            byref(value),
        )
        ensure_ok(code, "itk_get_num_cfg")
        return int(value.value)

    def set_numeric_config(self, *, dest_node: int, num_size: int, num_id: int, value: int) -> None:
        """Escribe una configuración numérica del equipo."""
        code = self._native.cdll.itk_set_num_cfg(
            self.handle,
            dest_node,
            num_size,
            num_id,
            value,
        )
        ensure_ok(code, "itk_set_num_cfg")

    def get_aux_input(self, *, dest_node: int, input_id: int) -> ITKAuxInput:
        """Lee configuración de una entrada auxiliar."""
        aux = ITKAuxInput()
        code = self._native.cdll.itk_get_aux_input(
            self.handle,
            dest_node,
            input_id,
            byref(aux),
        )
        ensure_ok(code, "itk_get_aux_input")
        return aux

    def set_aux_input(self, *, dest_node: int, input_id: int, value: ITKAuxInput) -> None:
        """Escribe configuración de una entrada auxiliar."""
        code = self._native.cdll.itk_set_aux_input(
            self.handle,
            dest_node,
            input_id,
            byref(value),
        )
        ensure_ok(code, "itk_set_aux_input")

    def state_aux_input(self, *, dest_node: int) -> int:
        """Retorna bitmap con el estado de entradas auxiliares."""
        value = c_long(0)
        code = self._native.cdll.itk_state_aux_input(self.handle, dest_node, byref(value))
        ensure_ok(code, "itk_state_aux_input")
        return int(value.value)

    def list_users(
        self,
        *,
        dest_node: int,
        start_position: int = 0,
        records_to_list: int = 1,
    ) -> tuple[list[ITKUserInfo], int]:
        """Lista usuarios desde la posición indicada.

        Nota:
        la firma nativa usa un puntero a bloque de `ITKUserInfo`. Esta versión crea
        un arreglo contiguo de tamaño `records_to_list`.
        """
        array_type = ITKUserInfo * records_to_list
        items = array_type()
        records_read = c_int16(0)

        code = self._native.cdll.itk_list_users(
            self.handle,
            dest_node,
            start_position,
            records_to_list,
            items,
            byref(records_read),
        )
        ensure_ok(code, "itk_list_users")
        count = int(records_read.value)
        return list(items[:count]), count

    def add_user(
        self,
        *,
        dest_node: int,
        user: ITKUserInfo,
        record_index: int = 0,
        records_to_add: int = 1,
    ) -> int:
        """Da de alta uno o más usuarios.

        Para esta versión inicial, lo normal es usar `records_to_add=1`.
        """
        records_added = c_int16(0)
        code = self._native.cdll.itk_add_user(
            self.handle,
            dest_node,
            record_index,
            records_to_add,
            byref(user),
            byref(records_added),
        )
        ensure_ok(code, "itk_add_user")
        return int(records_added.value)

    def edit_user(
        self,
        *,
        dest_node: int,
        user: ITKUserInfo,
        record_index: int = 0,
        records_to_edit: int = 1,
    ) -> int:
        """Edita uno o más usuarios."""
        records_edited = c_int16(0)
        code = self._native.cdll.itk_edit_user(
            self.handle,
            dest_node,
            record_index,
            records_to_edit,
            byref(user),
            byref(records_edited),
        )
        ensure_ok(code, "itk_edit_user")
        return int(records_edited.value)

    def delete_user(
        self,
        *,
        dest_node: int,
        user: ITKUserInfo,
        record_index: int = 0,
        records_to_del: int = 1,
    ) -> int:
        """Elimina uno o más usuarios."""
        records_deleted = c_int16(0)
        code = self._native.cdll.itk_del_user(
            self.handle,
            dest_node,
            record_index,
            records_to_del,
            byref(user),
            byref(records_deleted),
        )
        ensure_ok(code, "itk_del_user")
        return int(records_deleted.value)

    def list_marks(
        self,
        *,
        dest_node: int,
        start_position: int = 0,
        records_to_list: int = 1,
    ) -> tuple[list[ITKMarkInfo], int]:
        """Lista marcas históricas."""
        array_type = ITKMarkInfo * records_to_list
        items = array_type()
        listed = c_int16(0)

        code = self._native.cdll.itk_list_marks(
            self.handle,
            dest_node,
            start_position,
            records_to_list,
            items,
            byref(listed),
        )
        ensure_ok(code, "itk_list_marks")
        count = int(listed.value)
        return list(items[:count]), count

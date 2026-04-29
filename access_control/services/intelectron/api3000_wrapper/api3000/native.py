from __future__ import annotations

import os
from ctypes import (
    CDLL,
    POINTER,
    byref,
    c_char_p,
    c_int16,
    c_int32,
    c_long,
    c_uint8,
)
from pathlib import Path
from typing import Final

from .errors import Api3000Error
from .structs import (
    ITKAuxInput,
    ITKDateTime,
    ITKDisplayMessage2Info,
    ITKMarkInfo,
    ITKScheduleHeader,
    ITKScheduleInfo,
    ITKScheduleSegment,
    ITKUserInfo,
)

DEFAULT_ENV_VAR: Final[str] = "API3000_LIB_PATH"
LINUX_LIB_FILENAME: Final[str] = "libitkcom.so.0.0.0"
WINDOWS_LIB_FILENAME: Final[str] = "itkcom.dll"
WINDOWS_ALT_LIB_FILENAME: Final[str] = "libitkcom.dll"


def resolve_library_path(explicit_path: str | None = None) -> str:
    """Resuelve la ruta de la librería compartida.

    Prioridad:
    1. `explicit_path`
    2. variable de entorno `API3000_LIB_PATH`
    3. librería incluida en el wrapper (si existe)
    4. nombre visible por el loader dinámico
    """
    if explicit_path:
        return explicit_path

    env_path = os.getenv(DEFAULT_ENV_VAR)
    if env_path:
        return env_path

    bundled_candidate = Path(__file__).resolve().parent.parent / LINUX_LIB_FILENAME
    if bundled_candidate.exists():
        return str(bundled_candidate)

    return LINUX_LIB_FILENAME


def resolve_library_candidates(explicit_path: str | None = None) -> list[str]:
    """Devuelve rutas candidatas para cargar la librería nativa."""
    primary_candidate = resolve_library_path(explicit_path)
    fallback_candidates = [primary_candidate]

    if explicit_path is not None:
        return fallback_candidates

    for windows_candidate in (WINDOWS_ALT_LIB_FILENAME, WINDOWS_LIB_FILENAME):
        if primary_candidate != windows_candidate:
            fallback_candidates.append(windows_candidate)

    return fallback_candidates


class NativeLibrary:
    """Encapsula la carga de `libitkcom` y define firmas conocidas."""

    def __init__(self, lib_path: str | None = None) -> None:
        errors: list[str] = []
        candidates = resolve_library_candidates(lib_path)

        for candidate in candidates:
            try:
                self._cdll = CDLL(candidate)
                self.lib_path = candidate
                break
            except OSError as exc:
                errors.append(f"{candidate}: {exc}")
        else:
            attempted = "; ".join(errors)
            raise Api3000Error(
                f"No se pudo cargar la libreria nativa. Intentos: {attempted}"
            )

        self._configure_signatures()

    @property
    def cdll(self) -> CDLL:
        return self._cdll

    def _configure_signatures(self) -> None:
        lib = self._cdll

        lib.itk_init.argtypes = [c_char_p, c_long]
        lib.itk_init.restype = c_long

        lib.itk_uninit.argtypes = []
        lib.itk_uninit.restype = c_long

        lib.itk_lib_version.argtypes = [c_char_p]
        lib.itk_lib_version.restype = None

        lib.itk_set_keepalive.argtypes = [c_long, c_long, c_int16]
        lib.itk_set_keepalive.restype = c_long

        lib.itk_open.argtypes = [
            POINTER(c_long),      # error_code
            c_int16,              # source_node
            c_uint8,              # packet_protocol
            c_char_p,             # conn_string
            c_long,               # timeout
            c_long,               # callback_args
            c_long,               # callback_ev_user
            c_long,               # callback_ev_aux_input
            c_long,               # callback_conn
        ]
        lib.itk_open.restype = c_long

        lib.itk_close.argtypes = [c_long]
        lib.itk_close.restype = c_long

        lib.itk_set_rcv_timeout.argtypes = [c_long, c_long]
        lib.itk_set_rcv_timeout.restype = c_long

        lib.itk_set_snd_timeout.argtypes = [c_long, c_long]
        lib.itk_set_snd_timeout.restype = c_long

        lib.itk_set_interbyte_timeout.argtypes = [c_long, c_long]
        lib.itk_set_interbyte_timeout.restype = c_long

        lib.itk_get_time.argtypes = [c_long, c_int16, POINTER(ITKDateTime)]
        lib.itk_get_time.restype = c_long

        lib.itk_set_time.argtypes = [c_long, c_int16, POINTER(ITKDateTime)]
        lib.itk_set_time.restype = c_long

        lib.itk_list_marks.argtypes = [
            c_long,
            c_int16,
            c_int16,
            c_int16,
            POINTER(ITKMarkInfo),
            POINTER(c_int16),
        ]
        lib.itk_list_marks.restype = c_long

        lib.itk_get_num_cfg.argtypes = [c_long, c_int16, c_uint8, c_int16, POINTER(c_long)]
        lib.itk_get_num_cfg.restype = c_long

        lib.itk_set_num_cfg.argtypes = [c_long, c_int16, c_uint8, c_int16, c_long]
        lib.itk_set_num_cfg.restype = c_long

        lib.itk_get_aux_input.argtypes = [c_long, c_int16, c_int16, POINTER(ITKAuxInput)]
        lib.itk_get_aux_input.restype = c_long

        lib.itk_set_aux_input.argtypes = [c_long, c_int16, c_int16, POINTER(ITKAuxInput)]
        lib.itk_set_aux_input.restype = c_long

        lib.itk_state_aux_input.argtypes = [c_long, c_int16, POINTER(c_long)]
        lib.itk_state_aux_input.restype = c_long

        lib.itk_get_info.argtypes = [c_long, c_int16, c_char_p, c_char_p]
        lib.itk_get_info.restype = c_long

        lib.itk_reset_node.argtypes = [c_long, c_int16, c_long, c_long]
        lib.itk_reset_node.restype = c_long

        lib.itk_flush_table.argtypes = [c_long, c_int16, c_uint8]
        lib.itk_flush_table.restype = c_long

        lib.itk_del_records.argtypes = [c_long, c_int16, c_uint8, c_int16, POINTER(c_int16)]
        lib.itk_del_records.restype = c_long

        lib.itk_block_node.argtypes = [c_long, c_int16, c_uint8]
        lib.itk_block_node.restype = c_long

        lib.itk_add_user.argtypes = [
            c_long,
            c_int16,
            c_int16,
            c_int16,
            POINTER(ITKUserInfo),
            POINTER(c_int16),
        ]
        lib.itk_add_user.restype = c_long

        lib.itk_del_user.argtypes = [
            c_long,
            c_int16,
            c_int16,
            c_int16,
            POINTER(ITKUserInfo),
            POINTER(c_int16),
        ]
        lib.itk_del_user.restype = c_long

        lib.itk_edit_user.argtypes = [
            c_long,
            c_int16,
            c_int16,
            c_int16,
            POINTER(ITKUserInfo),
            POINTER(c_int16),
        ]
        lib.itk_edit_user.restype = c_long

        lib.itk_list_users.argtypes = [
            c_long,
            c_int16,
            c_int16,
            c_int16,
            POINTER(ITKUserInfo),
            POINTER(c_int16),
        ]
        lib.itk_list_users.restype = c_long

        lib.itk_read_template.argtypes = [
            c_long,
            c_int16,
            c_int16,
            c_long,
            c_uint8,
            POINTER(c_uint8),
            c_long,
        ]
        lib.itk_read_template.restype = c_long

        lib.itk_write_template.argtypes = [
            c_long,
            c_int16,
            c_int16,
            c_long,
            c_uint8,
            POINTER(c_uint8),
            c_long,
        ]
        lib.itk_write_template.restype = c_long

        lib.itk_flush_templates.argtypes = [c_long, c_int16, c_int16]
        lib.itk_flush_templates.restype = c_long

        lib.itk_del_template.argtypes = [c_long, c_int16, c_int16, c_long, c_uint8]
        lib.itk_del_template.restype = c_long

        lib.itk_fopen.argtypes = [c_long, c_int16, c_char_p, c_long, POINTER(c_long)]
        lib.itk_fopen.restype = c_long

        lib.itk_fread.argtypes = [c_long, c_int16, POINTER(c_uint8), c_long, POINTER(c_long)]
        lib.itk_fread.restype = c_long

        lib.itk_fwrite.argtypes = [c_long, c_int16, POINTER(c_uint8), c_long]
        lib.itk_fwrite.restype = c_long

        lib.itk_fclose.argtypes = [c_long, c_int16, POINTER(c_long)]
        lib.itk_fclose.restype = c_long

        lib.itk_fdel.argtypes = [c_long, c_int16, c_char_p]
        lib.itk_fdel.restype = c_long

        lib.itk_get_schd_header.argtypes = [c_long, c_int16, c_int16, POINTER(ITKScheduleHeader)]
        lib.itk_get_schd_header.restype = c_long

        lib.itk_get_schd_segments.argtypes = [
            c_long,
            c_int16,
            c_int16,
            POINTER(ITKScheduleSegment),
            c_long,
            c_long,
            POINTER(c_long),
        ]
        lib.itk_get_schd_segments.restype = c_long

        lib.itk_set_schd_header.argtypes = [c_long, c_int16, POINTER(ITKScheduleHeader)]
        lib.itk_set_schd_header.restype = c_long

        lib.itk_set_schd_segments.argtypes = [
            c_long,
            c_int16,
            c_int16,
            POINTER(ITKScheduleSegment),
            c_long,
            c_long,
            POINTER(c_long),
        ]
        lib.itk_set_schd_segments.restype = c_long

        lib.itk_del_schd.argtypes = [c_long, c_int16, c_int16]
        lib.itk_del_schd.restype = c_long

        lib.itk_list_schd.argtypes = [
            c_long,
            c_int16,
            POINTER(ITKScheduleInfo),
            POINTER(c_int16),
            c_long,
            POINTER(c_long),
        ]
        lib.itk_list_schd.restype = c_long

        lib.itk_rele_control.argtypes = [c_long, c_int16, c_long, c_long]
        lib.itk_rele_control.restype = c_long

        lib.itk_display_msg.argtypes = [
            c_long,
            c_int16,
            c_int16,
            POINTER(ITKDisplayMessage2Info),
            c_long,
            POINTER(c_int16),
        ]
        lib.itk_display_msg.restype = c_long

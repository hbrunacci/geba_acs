"""Generación de miniaturas de fotos de socios (Pillow)."""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

try:  # pragma: no cover - depende del entorno
    from PIL import Image  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    Image = None  # type: ignore

# Tamaño máximo de la miniatura (ancho x alto), se conserva aspecto.
THUMBNAIL_SIZE = (96, 120)


def pillow_disponible() -> bool:
    return Image is not None


def make_thumbnail(data: bytes, size: tuple[int, int] = THUMBNAIL_SIZE) -> bytes | None:
    """Devuelve una miniatura JPEG de ``data`` o None si no se puede generar."""
    if not data or Image is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            img.thumbnail(size, Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=80, optimize=True)
            return out.getvalue()
    except Exception as exc:  # pragma: no cover - imágenes corruptas
        logger.warning("no se pudo generar miniatura: %s", exc)
        return None


def resize_for_face(data: bytes, max_side: int = 600, quality: int = 88) -> bytes | None:
    """Redimensiona una foto para enrolarla en BioStar.

    BioStar 2 devuelve ``500 code 1000 "Ran out of stack space trying to match the
    regular expression"`` cuando el ``template_ex_picture`` (base64) es muy grande.
    Bajar el lado mayor a ~600 px resuelve el rechazo. Devuelve JPEG RGB o None.
    """
    if not data or Image is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            img.thumbnail((max_side, max_side), Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)
            return out.getvalue()
    except Exception as exc:  # pragma: no cover - imágenes corruptas
        logger.warning("no se pudo redimensionar foto para facial: %s", exc)
        return None

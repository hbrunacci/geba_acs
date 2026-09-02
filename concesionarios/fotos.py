"""Foto cargada a mano: guardarla, mostrarla y mandarla al rostro de BioStar.

La foto de xSys sólo la tienen 4 de los 101 concesionarios de la nómina, así
que el rostro del facial no tiene de dónde salir. Acá se carga la que saca la
oficina y se enrola con el mismo camino que usa ``biostar_enroll_faces``, que
ya sabe achicar la imagen cuando BioStar la rechaza por tamaño.
"""

from __future__ import annotations

import hashlib

from django.utils import timezone

from concesionarios.models import FotoPersona

TAMANO_MAXIMO = 8 * 1024 * 1024
TIPOS = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


class FotoInvalida(ValueError):
    """La imagen no sirve (formato, tamaño o no se pudo procesar)."""


def guardar(id_cliente: int, datos: bytes, *, content_type: str = "", usuario: str = "") -> FotoPersona:
    """Guarda (o reemplaza) la foto de una persona y le arma la miniatura."""
    if not datos:
        raise FotoInvalida("No llegó ninguna imagen.")
    if len(datos) > TAMANO_MAXIMO:
        raise FotoInvalida("La imagen supera los 8 MB.")

    jpeg = _a_jpeg(datos)
    if jpeg is None:
        raise FotoInvalida("No se pudo leer la imagen. Se aceptan JPG, PNG o WEBP.")

    from xsys.services.images import make_thumbnail
    try:
        thumb = make_thumbnail(jpeg)
    except Exception:
        thumb = None

    foto, _ = FotoPersona.objects.update_or_create(
        id_cliente=id_cliente,
        defaults={
            "imagen": jpeg,
            "thumbnail": thumb,
            "content_type": "image/jpeg",
            "sha256": hashlib.sha256(jpeg).hexdigest(),
            "subido_por": (usuario or "")[:150],
            "created_at": timezone.now(),
            # La foto cambió: lo que estaba enrolado en BioStar ya no es ésta.
            "enrolada_at": None,
            "enrolada_resultado": "",
        },
    )
    return foto


def _a_jpeg(datos: bytes) -> bytes | None:
    """Normaliza a JPEG. BioStar quiere JPEG, y así la miniatura es una sola rama."""
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(datos))
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        salida = io.BytesIO()
        img.save(salida, format="JPEG", quality=90, optimize=True)
        return salida.getvalue()
    except Exception:
        return None


def bytes_para_mostrar(id_cliente: int, *, miniatura: bool = True) -> tuple[bytes | None, str]:
    """La cara de la persona: primero la cargada a mano, si no la de xSys."""
    foto = FotoPersona.objects.filter(id_cliente=id_cliente).first()
    if foto:
        datos = foto.thumbnail if (miniatura and foto.thumbnail) else foto.imagen
        return (bytes(datos) if datos else None), "image/jpeg"

    from xsys.models import XsysSocioFoto
    espejo = XsysSocioFoto.objects.filter(id_cliente=id_cliente).order_by("nro").first()
    if not espejo:
        return None, ""
    datos = espejo.thumbnail if (miniatura and espejo.thumbnail) else espejo.imagen
    return (bytes(datos) if datos else None), "image/jpeg"


def enrolar(id_cliente: int, *, nombre: str = "") -> dict:
    """Manda la foto al rostro de BioStar y deja anotado cómo salió.

    Reusa ``enroll_one``, que crea el usuario si no existe, le asigna el grupo
    de acceso y va achicando la imagen mientras BioStar la rechace por tamaño.
    """
    foto = FotoPersona.objects.filter(id_cliente=id_cliente).first()
    if not foto or not foto.imagen:
        return {"ok": False, "detalle": "La persona no tiene foto cargada."}

    try:
        from access_control.services.biostar2_client import BioStar2Client
        from access_control.services.biostar_face_sync import enroll_one
    except Exception as exc:  # pragma: no cover - depende del entorno
        return {"ok": False, "detalle": f"No se pudo cargar el cliente de BioStar: {exc}"}

    try:
        client = BioStar2Client()
        resultado = enroll_one(
            client, id_cliente=id_cliente, jpeg_bytes=bytes(foto.imagen),
            name=nombre, exists=False)
    except Exception as exc:
        resultado = {"action": "failed", "reason": str(exc)[:180]}

    accion = resultado.get("action", "failed")
    detalle = resultado.get("reason", "")
    foto.enrolada_at = timezone.now()
    foto.enrolada_resultado = (f"{accion}: {detalle}" if detalle else accion)[:200]
    foto.save(update_fields=["enrolada_at", "enrolada_resultado"])
    return {"ok": accion in ("enrolled", "created"), "accion": accion,
            "detalle": detalle, "resultado": foto.enrolada_resultado}

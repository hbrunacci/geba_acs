from __future__ import annotations

from django.db import models
from django.utils import timezone


class SocioAviso(models.Model):
    """Aviso/registro que un operador deja sobre un socio desde el diagnóstico de
    facial: p.ej. "notificar que pase por Socios" (predefinido) o una nota libre.

    Queda asociado al ``id_cliente`` para poder revisarlo después (histórico de
    avisos del socio). No modifica xSys ni BioStar: es un registro local.
    """

    TIPO_PASE_POR_SOCIOS = "pase_por_socios"
    TIPO_TOMAR_FOTO = "tomar_foto"
    TIPO_DEUDA = "deuda"
    TIPO_DATOS_A_ACTUALIZAR = "datos_a_actualizar"
    TIPO_LIBRE = "libre"

    TEXTO_PASE_POR_SOCIOS = "Se indica pasar por oficina de socios"
    TEXTO_TOMAR_FOTO = "Se indica tomar foto"
    TEXTO_DEUDA = "Se notifica deuda"
    # Lo deja el sistema, no un operador: el socio figura dado de baja por un
    # proceso que la oficina de Socios todavía no validó, y hasta entonces se lo
    # deja pasar. El aviso es para que alguien lo llame y lo resuelva.
    TEXTO_DATOS_A_ACTUALIZAR = "Figura dado de baja por error: debe actualizar sus datos en oficina de socios"

    # Avisos de un toque: el operador del molinete los deja sin escribir nada, así
    # que el texto lo fija el servidor y no viaja en el request.
    TEXTOS_PREDEFINIDOS = {
        TIPO_PASE_POR_SOCIOS: TEXTO_PASE_POR_SOCIOS,
        TIPO_TOMAR_FOTO: TEXTO_TOMAR_FOTO,
        TIPO_DEUDA: TEXTO_DEUDA,
        TIPO_DATOS_A_ACTUALIZAR: TEXTO_DATOS_A_ACTUALIZAR,
    }

    id_cliente = models.BigIntegerField(db_index=True)
    tipo = models.CharField(max_length=32, default=TIPO_LIBRE)
    texto = models.CharField(max_length=500)
    creado_por = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    # Estado de gestión: la oficina de Socios lo marca como notificado/resuelto.
    resuelto = models.BooleanField(default=False, db_index=True)
    resuelto_at = models.DateTimeField(null=True, blank=True)
    resuelto_por = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        db_table = "socio_aviso"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("id_cliente", "-created_at"))]
        verbose_name = "Aviso de socio"
        verbose_name_plural = "Avisos de socios"

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"aviso {self.id_cliente}: {self.texto[:40]}"

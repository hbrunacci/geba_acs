from __future__ import annotations

from django.db import models
from django.utils import timezone


class XsysSocioEdicion(models.Model):
    """Auditoría de los cambios que ESTA app hace sobre la ficha de xSys.

    xSys no guarda quién tocó qué campo: ``Clientes_Hist`` sólo versiona un puñado
    de columnas (categoría, activo, titular) y ni siquiera registra el valor viejo
    de las demás. Como acá se edita la ficha completa, cada campo modificado deja
    su propia fila con el antes y el después, para poder contestar "¿quién le
    cambió el documento a este socio?" sin depender del ERP.

    Una fila por campo y no por guardado: así el historial se lee por columna
    ("el documento cambió tres veces") sin desarmar un JSON.
    """

    id_cliente = models.IntegerField(db_index=True)
    campo = models.CharField(max_length=60)
    etiqueta = models.CharField(max_length=80, blank=True, default="")
    # Los valores se guardan ya formateados como los vio y los dejó el operador.
    # Guardar el crudo de SQL obligaría a reinterpretar tipos para mostrarlos.
    valor_anterior = models.CharField(max_length=255, blank=True, default="")
    valor_nuevo = models.CharField(max_length=255, blank=True, default="")
    usuario = models.CharField(max_length=150, blank=True, default="")
    ip = models.CharField(max_length=45, blank=True, default="")
    creado_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "xsys_socio_edicion"
        verbose_name = "Edición de ficha (xSys)"
        verbose_name_plural = "Ediciones de fichas (xSys)"
        ordering = ("-creado_at", "-id")
        indexes = [
            models.Index(fields=("id_cliente", "-creado_at"), name="xsys_socio_edic_cli"),
        ]

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"{self.campo} de {self.id_cliente}: {self.valor_anterior!r} → {self.valor_nuevo!r}"

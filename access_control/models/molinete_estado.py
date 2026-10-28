"""Estado en vivo de cada molinete Intelektron que atiende ``intelektron_online``.

Una fila por placa, que el servicio actualiza al arrancar, cada pocos
segundos y en cada paso. Es lo que muestra la pantalla "Molinetes en línea":
si ``updated_at`` es viejo, el servicio no está corriendo (o no llega a la
base), aunque la última foto diga ``viva``.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

# Si el servicio no actualiza en este tiempo, la pantalla lo da por caído.
ESTADO_VENCIDO_SEG = 45


class MolineteEstado(models.Model):
    ip = models.CharField(max_length=45)
    port = models.IntegerField(default=3001)
    id_controlador = models.IntegerField(db_index=True)
    id_acceso = models.IntegerField(null=True, blank=True)
    nombre = models.CharField(max_length=120, blank=True, default="")
    modo = models.CharField(max_length=10, blank=True, default="")
    validador = models.CharField(max_length=10, blank=True, default="")
    rele = models.IntegerField(default=0)
    rele_tiempo = models.IntegerField(default=0)
    viva = models.BooleanField(default=False)            # sesión abierta según la librería
    socket = models.BooleanField(null=True, blank=True)  # conexión TCP vista por el watchdog (None = no se sabe)
    eventos = models.IntegerField(default=0)
    respuestas = models.IntegerField(default=0)
    abiertos = models.IntegerField(default=0)
    denegados = models.IntegerField(default=0)
    errores = models.IntegerField(default=0)             # decisiones con error (SIN CONEXION, etc.)
    errores_respuesta = models.IntegerField(default=0)
    reconexiones = models.IntegerField(default=0)
    caidas_socket = models.IntegerField(default=0)
    ultimo_evento = models.DateTimeField(null=True, blank=True)
    ultima_apertura = models.DateTimeField(null=True, blank=True)
    ultimo_tag = models.CharField(max_length=100, blank=True, default="")
    ultimo_mensaje = models.CharField(max_length=300, blank=True, default="")
    ultimo_error = models.CharField(max_length=300, blank=True, default="")
    proceso = models.CharField(max_length=120, blank=True, default="")   # host:pid que la atiende
    iniciado_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "acs_molinete_estado"
        unique_together = (("ip", "port"),)
        ordering = ("id_acceso", "id_controlador")
        verbose_name = "Estado de molinete en línea"
        verbose_name_plural = "Estados de molinetes en línea"

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.ip}:{self.port} ctrl {self.id_controlador}"

    @property
    def vencido(self) -> bool:
        return (timezone.now() - self.updated_at).total_seconds() > ESTADO_VENCIDO_SEG

    @property
    def en_linea(self) -> bool:
        return self.viva and self.socket is not False and not self.vencido

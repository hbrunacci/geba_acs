from __future__ import annotations

from django.db import models
from django.utils import timezone


class PasoMolinete(models.Model):
    """Lectura decidida por geba_acs en un molinete que atiende él mismo.

    Es nuestro equivalente a la fila de ``CD_ES`` de Clever, pero vive en esta
    base: Clever no se entera de estos pasos. La escribe el comando
    ``intelektron_online`` por cada lectura que la placa manda a validar, en
    cualquiera de sus modos:

    * ``modo = observar``: se evaluó pero NO se accionó el molinete.
    * ``modo = real``: la respuesta fue la que recibió la placa.
    * ``modo = libre``: se abrió con cualquier lectura, sin validar (prueba de relé).

    ``validador`` dice quién decidió: ``local`` (la réplica Python de la
    cascada de Clever, ``validador_local``) o ``clever`` (el SP de Clever,
    ``CP_SCA_RegistrarAcceso``). ``id_motivo`` usa los mismos códigos que
    ``CD_Motivos`` para que el visor y los informes hablen el mismo idioma.

    ``antirebote`` marca las lecturas repetidas (mismo tag y controlador en
    pocos segundos) a las que se les contestó lo mismo que a la primera sin
    volver a evaluar; Clever directamente no las graba.
    """

    VALIDADORES = (("local", "geba_acs (réplica local)"), ("clever", "Clever (CP_SCA_RegistrarAcceso)"))
    MODOS = (("observar", "Observar (no acciona)"), ("real", "Real (acciona el molinete)"),
             ("libre", "Libre (abre sin validar, prueba de relé)"))

    fecha = models.DateTimeField(default=timezone.now, db_index=True)
    id_controlador = models.IntegerField(db_index=True)
    id_acceso = models.IntegerField(null=True, blank=True, db_index=True)
    tipo = models.CharField(max_length=1, default="E")            # E entrada / S salida
    tag = models.CharField(max_length=100, blank=True, default="", db_index=True)
    lector = models.IntegerField(null=True, blank=True)
    evento_codigo = models.IntegerField(null=True, blank=True)

    id_cliente = models.IntegerField(default=0, db_index=True)
    id_cliente_ref = models.IntegerField(default=0)
    habilitado = models.BooleanField(default=False)
    accionado = models.BooleanField(default=False)               # se mandó abrir el molinete
    id_motivo = models.IntegerField(null=True, blank=True, db_index=True)
    mensaje = models.CharField(max_length=300, blank=True, default="")
    detalle = models.CharField(max_length=200, blank=True, default="")
    info = models.CharField(max_length=10, blank=True, default="")   # OBSER / ADVER / ERROR

    validador = models.CharField(max_length=10, choices=VALIDADORES, default="local")
    modo = models.CharField(max_length=10, choices=MODOS, default="observar")
    antirebote = models.BooleanField(default=False)
    duracion_ms = models.FloatField(default=0.0)
    error = models.CharField(max_length=300, blank=True, default="")
    raw = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "acs_paso_molinete"
        ordering = ("-fecha", "-id")
        indexes = [
            models.Index(fields=("id_controlador", "-fecha")),
            models.Index(fields=("tag", "-fecha")),
            models.Index(fields=("id_cliente", "-fecha")),
        ]
        verbose_name = "Paso de molinete (geba_acs)"
        verbose_name_plural = "Pasos de molinete (geba_acs)"

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        estado = "OK" if self.habilitado else "NO"
        return f"{self.fecha:%d/%m %H:%M:%S} ctrl {self.id_controlador} {self.tag} {estado} {self.id_motivo}"

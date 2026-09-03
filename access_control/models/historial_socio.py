from __future__ import annotations

from django.db import models
from django.utils import timezone


class SocioAcceso(models.Model):
    """Historial permanente de pasos por socio: cada lectura y qué le contestó.

    Ya teníamos los dos espejos, pero ninguno sirve como historial:

    - ``ExternalAccessLogEntry`` copia ``CD_ES`` y se purga a los 7 días
      (``CD_ES_RETENTION_DAYS``); está pensado para alimentar el visor, no para
      consultar el pasado de una persona.
    - ``BiostarAccessEvent`` copia el log de los faciales y también se purga.

    Además están separados, y el mismo socio entra por los dos lados: por
    credencial/DNI/QR (queda en xSys) o por la cara (queda en BioStar, que xSys
    colapsa en un único controlador puente). Un historial que mire uno solo
    miente por la mitad.

    Esta tabla junta los dos canales, en una fila por lectura, y **no se purga**.
    Se escribe en la ingesta —donde ya se resuelven molinete y puerta— y no al
    consultar, así que el nombre del molinete queda congelado como estaba ese
    día aunque después se rearme la puerta.

    ``referencia`` (``cdes:<Id_ES>`` / ``biostar:<id>``) es la clave de
    idempotencia: los pollers reprocesan eventos y el backfill puede correrse
    las veces que haga falta sin duplicar nada.

    Lo que se guarda es lo que dijo el equipo, no una interpretación: el
    ``mensaje`` es el texto que xSys mandó a la pantalla. Los avisos que el visor
    arma para el operador ("Deuda de Actividades", "Pasa · figura dado de baja")
    dependen del estado del socio en ese instante y no se congelan acá.
    """

    ORIGEN_CREDENCIAL = "credencial"
    ORIGEN_FACIAL = "facial"

    # Sin ``db_index`` en estos dos: los índices compuestos de ``Meta`` ya los
    # cubren por prefijo. Un índice de más no es gratis en una tabla que sólo
    # crece — se paga en disco y en cada paso que se registra.
    id_cliente = models.BigIntegerField()
    fecha = models.DateTimeField()
    origen = models.CharField(max_length=12, default=ORIGEN_CREDENCIAL)
    referencia = models.CharField(max_length=48, unique=True)

    # Resultado de la lectura.
    permitido = models.BooleanField(default=False)
    # 'S'/'N' de CD_ES. Vacío en los faciales, que no tienen ese campo.
    resultado = models.CharField(max_length=4, blank=True, default="")
    # El texto que se le mostró: motivo de pantalla u observación de xSys.
    mensaje = models.CharField(max_length=255, blank=True, default="")
    # Id_CD_Motivo (xSys) o event_type code (BioStar).
    motivo_code = models.IntegerField(null=True, blank=True)
    # La letra chica: observación completa de xSys, o el nombre del evento de
    # BioStar (VERIFY_SUCCESS, AUTH_FAILED_TIMEOUT…).
    detalle = models.CharField(max_length=255, blank=True, default="")

    # Dónde pasó, con los nombres tal como estaban ese día.
    puerta = models.CharField(max_length=80, blank=True, default="")
    molinete = models.CharField(max_length=60, blank=True, default="")
    id_acceso = models.IntegerField(null=True, blank=True)
    id_controlador = models.IntegerField(null=True, blank=True)
    device_id = models.BigIntegerField(null=True, blank=True)

    # Ver ExternalAccessLogEntry.conflicto_molinete: la credencial ya estaba
    # reservada en otro molinete cuando llegó esta lectura.
    conflicto_molinete = models.CharField(max_length=60, blank=True, default="")

    creado_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "socio_acceso"
        ordering = ("-fecha", "-id")
        indexes = [
            models.Index(fields=("id_cliente", "-fecha"), name="socio_acceso_cli_fecha"),
            models.Index(fields=("-fecha",), name="socio_acceso_fecha"),
        ]
        verbose_name = "Acceso de socio (historial)"
        verbose_name_plural = "Accesos de socios (historial)"

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        estado = "OK" if self.permitido else "rechazado"
        return f"{self.id_cliente} @ {self.fecha:%d/%m/%Y %H:%M} · {self.molinete} · {estado}"

from __future__ import annotations

from django.db import models
from django.utils import timezone


class FaceEnrollment(models.Model):
    """Qué foto de xSys quedó cargada como rostro en BioStar, por socio.

    El enrolamiento por cambio de foto (``push_faces_affected``) es de un solo
    tiro: si en ese momento el socio todavía no estaba habilitado, o BioStar
    falló, el cambio se perdía y nadie lo reintentaba (el backfill horario sólo
    miraba a quien no tenía rostro). Con esto el backfill compara la foto vigente
    de xSys contra la que se cargó y re-enrola la que quedó vieja.
    """

    id_cliente = models.IntegerField(unique=True)
    # ``Clientes_Fotos.Fecha`` de la foto enrolada (aware, hora local).
    foto_fecha = models.DateTimeField(null=True, blank=True)
    enrolado_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "access_control_face_enrollment"
        verbose_name = "Rostro enrolado en BioStar"
        verbose_name_plural = "Rostros enrolados en BioStar"

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"Rostro {self.id_cliente} (foto {self.foto_fecha})"

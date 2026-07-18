from __future__ import annotations

from django.db import models
from django.utils import timezone


class XsysSocioFoto(models.Model):
    """Espejo local de `Clientes_Fotos`. La imagen se guarda como binario en la DB.

    No usamos FK dura a ``XsysSocio`` a propósito: una foto puede sincronizarse
    antes que su socio. El vínculo es lógico por ``id_cliente``.
    """

    id_cliente = models.IntegerField(db_index=True)
    nro = models.PositiveSmallIntegerField()
    imagen = models.BinaryField(null=True, blank=True)
    thumbnail = models.BinaryField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    fecha = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "xsys_socio_foto"
        verbose_name = "Foto de socio (xSys)"
        verbose_name_plural = "Fotos de socios (xSys)"
        constraints = [
            models.UniqueConstraint(
                fields=("id_cliente", "nro"), name="uniq_xsys_foto_cliente_nro"
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"Foto {self.id_cliente}/{self.nro}"

    @property
    def socio(self):
        from xsys.models.socio import XsysSocio

        return XsysSocio.objects.filter(pk=self.id_cliente).first()

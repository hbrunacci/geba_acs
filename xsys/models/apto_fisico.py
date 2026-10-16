from __future__ import annotations

import datetime as _dt

from django.db import models
from django.utils import timezone

SIN_APTO = "sin_apto"
VENCIDO = "vencido"
VIGENTE = "vigente"


class XsysAptoFisico(models.Model):
    """Apto físico de quienes hacen un deporte, copiado de xSys.

    El apto vive en ``Clientes_Venc`` con ``Id_Tipo_Venc = 'AF'``: una fila por
    persona y ``Fecha`` es el vencimiento. Lo carga el sector de Socios.

    Acá están SÓLO los que tienen un contrato vigente de alguna familia
    deportiva (``XSYS_APTO_FAMILIAS``): a ellos se les avisa en el monitor de la
    puerta. Al resto no se le exige todavía, y por eso no figura. Que alguien no
    esté en la tabla significa "no se le controla", no "no tiene apto".

    ``vence`` vacío es "no presentó nunca": la persona no tiene fila en
    ``Clientes_Venc``, la tiene sin fecha, o con la fecha de relleno de 1950.
    """

    id_cliente = models.IntegerField(primary_key=True)
    vence = models.DateField(null=True, blank=True)
    deportes = models.CharField(max_length=200, blank=True, default="")
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "xsys_apto_fisico"
        verbose_name = "Apto físico (xSys)"
        verbose_name_plural = "Aptos físicos (xSys)"

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"{self.id_cliente}: {self.estado()} ({self.vence or 'sin fecha'})"

    def estado(self, hoy: _dt.date | None = None) -> str:
        if self.vence is None:
            return SIN_APTO
        hoy = hoy or timezone.localdate()
        return VIGENTE if self.vence >= hoy else VENCIDO

    def texto(self, hoy: _dt.date | None = None) -> str:
        """Lo que lee el de la puerta. Vacío si está en regla."""
        estado = self.estado(hoy)
        if estado == SIN_APTO:
            return "Sin Apto Físico"
        if estado == VENCIDO:
            return "Apto Físico vencido " + self.vence.strftime("%d/%m/%Y")
        return ""

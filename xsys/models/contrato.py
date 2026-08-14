from __future__ import annotations

from django.db import models
from django.utils import timezone


class XsysContrato(models.Model):
    """Espejo local de los contratos activos del socio (Contratos + Contratos_Tipos).

    Ej: CUOTA SOCIAL, DEPORTES FEDERADOS, COLONIA, ED NATACION, etc. Se usa en el
    modal del monitor para ver si el socio adquirió otros contratos.
    """

    id_contrato = models.IntegerField(primary_key=True)
    id_cliente = models.IntegerField(db_index=True)
    id_tipo_con = models.SmallIntegerField(null=True, blank=True)
    descripcion = models.CharField(max_length=50, blank=True, default="")
    fecha_alta = models.DateTimeField(null=True, blank=True)
    fecha_hasta = models.DateTimeField(null=True, blank=True)
    activo = models.SmallIntegerField(null=True, blank=True, db_index=True)
    # Disciplina concreta: el tipo de contrato dice "DEPORTES FEDERADOS" para
    # todos, y la actividad real (BASQUET / ATLETISMO / ESGRIMA...) sale del
    # producto asociado en Contratos_Prod -> Productos.Descripcion_Resumida.
    producto_desc = models.CharField(max_length=80, blank=True, default="")
    # Último pago imputado a este contrato: recibo (Importe < 0 en la cuenta
    # corriente) cuyo Id_Trans_Origen es un comprobante con este Id_Contrato.
    ultimo_pago_fecha = models.DateField(null=True, blank=True)
    ultimo_pago_importe = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    # Saldo impago YA VENCIDO (no incluye la cuota del mes siguiente, que el club
    # emite por adelantado) y fecha del último comprobante emitido — esta última
    # es la que decide si el contrato sigue "vivo" o es residuo histórico.
    deuda = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    ultimo_cbte_fecha = models.DateField(null=True, blank=True)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "xsys_contrato"
        verbose_name = "Contrato de socio (xSys)"
        verbose_name_plural = "Contratos de socios (xSys)"
        ordering = ("id_cliente", "descripcion")

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.descripcion} (cli {self.id_cliente})"

"""Foto de la deuda para el tablero.

Los otros tres gráficos del tablero (padrón, altas/bajas y categorías) se
resuelven con agregados de xSys que tardan décimas de segundo y se leen en vivo.
La deuda no: recorrer ``Clientes_CtaCte`` entera contra ``Cbtes`` para saber qué
está impago tarda entre 3 y 13 segundos según cómo se agrupe, y hacerlo en cada
apertura de pantalla castiga a xSys —que es la base de producción del club— sin
que el número cambie de un minuto a otro.

Por eso la deuda se calcula una vez, se guarda acá y la pantalla la lee de
Postgres. Se guarda en la base y no en un caché en memoria por dos razones: no
hay ``CACHES`` configurado (sería ``LocMemCache``, distinto por cada worker de
gunicorn, con lo que dos usuarios verían números distintos), y una foto guardada
sobrevive al reinicio del contenedor.

Se guardan las tres granularidades que la pantalla necesita, todas del mismo
cálculo, para que el gráfico, los filtros y el Excel no puedan contradecirse:

    XsysDeudaFoto     cabecera: cuándo se calculó, cuánto tardó, si salió bien
    XsysDeudaMes      deuda por mes y categoría, que es lo que se grafica
    XsysDeudaSocio    una fila por socio, que es lo que se baja a Excel

La foto anterior se conserva hasta que la nueva termina bien: si el cálculo
falla —VPN caída, xSys ocupado— la pantalla sigue mostrando la última foto buena
con su fecha a la vista, en vez de quedarse vacía.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class XsysDeudaFoto(models.Model):
    """Cabecera de un cálculo de deuda: cuándo, cuánto tardó y cómo salió."""

    calculado_at = models.DateTimeField(default=timezone.now, db_index=True)
    # Quién la pidió. Vacío = la recalculó la tarea programada de la noche.
    usuario = models.CharField(max_length=150, blank=True, default="")
    ok = models.BooleanField(default=False, db_index=True)
    duracion_ms = models.IntegerField(default=0)
    error = models.CharField(max_length=300, blank=True, default="")

    # Totales de control, para poder comparar dos fotos sin recorrer el detalle.
    socios = models.IntegerField(default=0)
    cupones = models.IntegerField(default=0)
    importe = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        db_table = "xsys_deuda_foto"
        verbose_name = "Foto de deuda (xSys)"
        verbose_name_plural = "Fotos de deuda (xSys)"
        ordering = ("-calculado_at",)

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        estado = "ok" if self.ok else "con error"
        return f"Deuda al {self.calculado_at:%d/%m/%Y %H:%M} ({estado})"

    @classmethod
    def ultima_buena(cls) -> "XsysDeudaFoto | None":
        """La foto más reciente que terminó bien; ``None`` si nunca hubo una."""
        return cls.objects.filter(ok=True).order_by("-calculado_at").first()


class XsysDeudaMes(models.Model):
    """Deuda impaga agrupada por mes del cupón y categoría del socio.

    ``mes`` es el mes del comprobante impago, no el de la foto: un cupón de
    marzo que sigue impago en septiembre suma en marzo. Es lo que hace que el
    gráfico muestre en qué meses se juntó la deuda.
    """

    foto = models.ForeignKey(XsysDeudaFoto, on_delete=models.CASCADE, related_name="meses")
    mes = models.DateField(db_index=True)
    id_tipo_cli = models.IntegerField(null=True, blank=True, db_index=True)
    categoria = models.CharField(max_length=100, blank=True, default="")
    # Socio de verdad (categoría con Flag_Tipo P o S) contra el resto.
    es_socio = models.BooleanField(default=False, db_index=True)
    activo = models.BooleanField(default=False, db_index=True)
    # De qué es la deuda: ``cuota_social``, ``actividades`` u ``otros``. Sale del
    # tipo de contrato que generó el comprobante; ver el mapeo en
    # ``xsys.services.tableros``.
    rubro = models.CharField(max_length=20, default="otros", db_index=True)
    # El deporte, sólo cuando ``rubro`` es actividades; vacío en el resto. El
    # tipo de contrato no alcanza para saberlo: DEPORTES FEDERADOS es el más
    # grande y mete vóley, hockey, rugby, básquet y waterpolo en la misma bolsa.
    actividad = models.CharField(max_length=60, blank=True, default="", db_index=True)
    socios = models.IntegerField(default=0)
    cupones = models.IntegerField(default=0)
    importe = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        db_table = "xsys_deuda_mes"
        verbose_name = "Deuda por mes (xSys)"
        verbose_name_plural = "Deuda por mes (xSys)"
        ordering = ("mes", "categoria")
        indexes = [
            models.Index(fields=("foto", "mes"), name="xsys_deuda_mes_foto"),
        ]

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"{self.mes:%Y-%m} {self.categoria}: {self.importe}"


class XsysDeudaSocio(models.Model):
    """Una fila por persona con deuda. Es la base del Excel.

    ``cuotas`` es la cantidad de comprobantes impagos, que es como el club mide
    la mora ("debe 3 cuotas"), y ``mes_mas_viejo`` es desde cuándo debe, que es
    lo que distingue al que se atrasó un mes del que dejó de pagar hace dos años.
    """

    foto = models.ForeignKey(XsysDeudaFoto, on_delete=models.CASCADE, related_name="socios_deuda")
    id_cliente = models.IntegerField(db_index=True)
    nro_socio = models.CharField(max_length=14, blank=True, default="")
    doc_nro = models.BigIntegerField(null=True, blank=True)
    apellido = models.CharField(max_length=100, blank=True, default="")
    nombre = models.CharField(max_length=100, blank=True, default="")
    id_tipo_cli = models.IntegerField(null=True, blank=True, db_index=True)
    categoria = models.CharField(max_length=100, blank=True, default="")
    es_socio = models.BooleanField(default=False, db_index=True)
    activo = models.BooleanField(default=False, db_index=True)
    cuotas = models.IntegerField(default=0, db_index=True)
    importe = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # La misma deuda abierta por rubro. Va en columnas y no en filas aparte
    # porque el Excel es un listado de PERSONAS: una fila por rubro haría
    # aparecer al mismo socio hasta tres veces y quien filtrara la planilla
    # contaría morosos de más.
    importe_cuota_social = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    importe_actividades = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    importe_otros = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cuotas_cuota_social = models.IntegerField(default=0)
    cuotas_actividades = models.IntegerField(default=0)

    mes_mas_viejo = models.DateField(null=True, blank=True)
    mes_mas_nuevo = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "xsys_deuda_socio"
        verbose_name = "Deuda por socio (xSys)"
        verbose_name_plural = "Deuda por socio (xSys)"
        ordering = ("-importe",)
        indexes = [
            models.Index(fields=("foto", "-importe"), name="xsys_deuda_soc_imp"),
            models.Index(fields=("foto", "cuotas"), name="xsys_deuda_soc_cuo"),
        ]

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"{self.apellido}, {self.nombre}: {self.cuotas} cuota(s)"


class XsysDeudaSocioMes(models.Model):
    """El detalle fino: qué debe cada persona, de qué, y de qué mes.

    Existe para poder abrir el gráfico. Al tocar "HOCKEY S/ CESPED" hay que poder
    decir QUIÉNES lo deben, desde cuándo y —esto es lo que obliga a guardar el
    mes— **cuánto de eso cae dentro del período que el usuario tiene filtrado**.
    Sin el mes, el modal contestaba siempre con la deuda histórica completa y no
    coincidía con la barra que se acababa de tocar.

    Se guarda en la foto en vez de consultarse en vivo a xSys por dos razones: el
    detalle tiene que dar exactamente lo mismo que el gráfico que se acaba de
    tocar —si se consultara en vivo, una foto de hace tres horas y un detalle de
    ahora mostrarían totales distintos y nadie sabría cuál creer—, y además abre
    al instante en vez de esperar a la base de producción en cada clic.

    Son unas 313.000 filas por foto, que es de lejos la tabla más grande de las
    cuatro. Por eso ``_limpiar_fotos_viejas`` conserva pocas fotos: la anterior
    alcanza para comparar contra ayer, y guardar diez sería medio millón de filas
    que nadie mira.
    """

    socio = models.ForeignKey(XsysDeudaSocio, on_delete=models.CASCADE,
                              related_name="detalle")
    mes = models.DateField(db_index=True)
    rubro = models.CharField(max_length=20, default="otros", db_index=True)
    actividad = models.CharField(max_length=60, blank=True, default="", db_index=True)
    cuotas = models.IntegerField(default=0)
    importe = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        db_table = "xsys_deuda_socio_mes"
        verbose_name = "Deuda por socio y mes (xSys)"
        verbose_name_plural = "Deuda por socio y mes (xSys)"
        ordering = ("mes",)
        indexes = [
            # El índice que usa el modal: filtra por actividad (o rubro) y mes.
            models.Index(fields=("actividad", "mes"), name="xsys_deuda_sm_act"),
            models.Index(fields=("rubro", "mes"), name="xsys_deuda_sm_rub"),
        ]

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"{self.mes:%Y-%m} {self.actividad or self.rubro}: {self.importe}"

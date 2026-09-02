"""Concesionarios: empresas, documentación con vencimiento y horarios de ingreso.

Tres piezas que se usan juntas pero son independientes a propósito:

- **Empresa / Concesionario**: el dato que xSys no tiene. En xSys un
  concesionario es sólo una categoría de socio (``Id_Tipo_Cli = 1015``); para
  quién trabaja no está en ningún lado. Acá se agrupa por empresa sin tocar el
  espejo: la persona se referencia por ``id_cliente``, igual que
  ``XsysWhitelist`` y ``XsysBajaRevision``, y no por ForeignKey, porque el
  espejo se reconstruye desde xSys y una FK lo trabaría.

- **TipoDocumento / Documento**: modelo aparte y **genérico**. Cualquiera que
  pase por el control de acceso puede tener documentación que lo habilite o lo
  frene (ART, seguro, apto médico, libreta sanitaria), no sólo los
  concesionarios; por eso ``Documento`` cuelga de ``id_cliente`` y no de
  ``Concesionario``. Existe ``people.PersonDocument``, pero cuelga de
  ``people.Person``, que está vacío (0 filas) y no es lo que identifica a la
  gente en el control de acceso.

- **HorarioAcceso / HorarioFranja**: una franja horaria con nombre
  ("Lun a Vie 9 a 18") que después se asigna a una persona o a una empresa
  entera y sabe contestar si a tal hora puede entrar.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

DIAS = (
    (0, "Lunes"),
    (1, "Martes"),
    (2, "Miércoles"),
    (3, "Jueves"),
    (4, "Viernes"),
    (5, "Sábado"),
    (6, "Domingo"),
)
DIAS_CORTOS = {0: "Lun", 1: "Mar", 2: "Mié", 3: "Jue", 4: "Vie", 5: "Sáb", 6: "Dom"}


# --------------------------------------------------------------------- horarios
class HorarioAcceso(models.Model):
    """Una recurrencia con nombre, compuesta por franjas de día y hora."""

    nombre = models.CharField(max_length=80, unique=True)
    descripcion = models.CharField(max_length=200, blank=True, default="")
    activo = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "conc_horario"
        verbose_name = "Horario de acceso"
        verbose_name_plural = "Horarios de acceso"
        ordering = ("nombre",)

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return self.nombre

    # -- evaluación ---------------------------------------------------------
    def permite(self, momento: datetime | None = None) -> bool:
        """¿Este horario deja pasar en ese instante?

        Un horario sin franjas no habilita nada: es un horario a medio cargar,
        y tratarlo como "siempre" sería abrir la puerta por un descuido.
        """
        if not self.activo:
            return False
        momento = momento or timezone.localtime()
        dia, hora = momento.weekday(), momento.time()
        for franja in self.franjas.all():
            if franja.cubre(dia, hora):
                return True
        return False

    @property
    def resumen(self) -> str:
        """Texto corto: agrupa los días que comparten la misma franja horaria."""
        por_horario: dict[tuple[time, time], list[int]] = {}
        for f in self.franjas.all():
            por_horario.setdefault((f.hora_desde, f.hora_hasta), []).append(f.dia_semana)
        partes = []
        for (desde, hasta), dias in sorted(por_horario.items()):
            partes.append(f"{_dias_texto(sorted(dias))} {desde:%H:%M}–{hasta:%H:%M}")
        return " · ".join(partes)


def _dias_texto(dias: list[int]) -> str:
    """[0,1,2,3,4] -> 'Lun a Vie'; [0,2,4] -> 'Lun, Mié, Vie'."""
    if not dias:
        return ""
    if len(dias) > 2 and dias == list(range(dias[0], dias[-1] + 1)):
        return f"{DIAS_CORTOS[dias[0]]} a {DIAS_CORTOS[dias[-1]]}"
    return ", ".join(DIAS_CORTOS[d] for d in dias)


class HorarioFranja(models.Model):
    """Un día de la semana con su ventana horaria.

    Si ``hora_hasta`` es menor que ``hora_desde`` la franja cruza la medianoche
    (22:00–06:00 = de ese día a las 22 hasta las 6 del día siguiente). Hace falta
    para los turnos de limpieza y mantenimiento.
    """

    horario = models.ForeignKey(HorarioAcceso, on_delete=models.CASCADE, related_name="franjas")
    dia_semana = models.SmallIntegerField(choices=DIAS)
    hora_desde = models.TimeField()
    hora_hasta = models.TimeField()

    class Meta:
        db_table = "conc_horario_franja"
        verbose_name = "Franja horaria"
        verbose_name_plural = "Franjas horarias"
        ordering = ("dia_semana", "hora_desde")
        unique_together = ("horario", "dia_semana", "hora_desde", "hora_hasta")

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"{DIAS_CORTOS[self.dia_semana]} {self.hora_desde:%H:%M}–{self.hora_hasta:%H:%M}"

    def clean(self):
        super().clean()
        if self.hora_desde == self.hora_hasta:
            raise ValidationError(
                {"hora_hasta": "La hora de fin no puede ser igual a la de inicio."})

    @property
    def cruza_medianoche(self) -> bool:
        return self.hora_hasta < self.hora_desde

    def cubre(self, dia: int, hora: time) -> bool:
        if not self.cruza_medianoche:
            return self.dia_semana == dia and self.hora_desde <= hora < self.hora_hasta
        # Cruza medianoche: la cola cae en el día siguiente al de la franja.
        if self.dia_semana == dia and hora >= self.hora_desde:
            return True
        return (self.dia_semana + 1) % 7 == dia and hora < self.hora_hasta


# --------------------------------------------------------------------- empresas
class Empresa(models.Model):
    """La concesionaria para la que trabaja la persona."""

    nombre = models.CharField(max_length=120, unique=True)
    cuit = models.CharField(max_length=20, blank=True, default="")
    rubro = models.CharField(max_length=80, blank=True, default="")
    contacto_nombre = models.CharField(max_length=120, blank=True, default="")
    contacto_email = models.CharField(max_length=200, blank=True, default="")
    contacto_telefono = models.CharField(max_length=60, blank=True, default="")
    # Horario que rige para toda la empresa. El de la persona, si tiene, manda.
    horario = models.ForeignKey(
        HorarioAcceso, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="empresas")
    activa = models.BooleanField(default=True, db_index=True)
    observaciones = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conc_empresa"
        verbose_name = "Empresa concesionaria"
        verbose_name_plural = "Empresas concesionarias"
        ordering = ("nombre",)

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return self.nombre


class Concesionario(models.Model):
    """La persona del control de acceso, atada a su empresa.

    ``id_cliente`` es el de xSys y es único: una persona trabaja para una
    empresa por vez. Si cambia de empresa se edita el registro; el historial de
    quién fue de quién no es lo que se pidió y agregarlo sin que nadie lo use
    sólo complica las altas.
    """

    id_cliente = models.IntegerField(unique=True, db_index=True)
    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="concesionarios")
    cargo = models.CharField(max_length=80, blank=True, default="")
    horario = models.ForeignKey(
        HorarioAcceso, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="concesionarios")
    activo = models.BooleanField(default=True, db_index=True)
    fecha_alta = models.DateField(null=True, blank=True)
    fecha_baja = models.DateField(null=True, blank=True)
    observaciones = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conc_concesionario"
        verbose_name = "Concesionario"
        verbose_name_plural = "Concesionarios"
        ordering = ("empresa__nombre", "id_cliente")

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"{self.id_cliente} · {self.empresa.nombre}"

    @property
    def horario_vigente(self) -> HorarioAcceso | None:
        """El de la persona si tiene; si no, el de la empresa."""
        return self.horario or self.empresa.horario

    def permite_horario(self, momento: datetime | None = None) -> bool:
        """Sin horario asignado no hay restricción horaria: no la inventamos."""
        horario = self.horario_vigente
        return True if horario is None else horario.permite(momento)


# ---------------------------------------------------------------- documentación
class TipoDocumento(models.Model):
    """Clase de documento (ART, seguro, apto médico, libreta sanitaria...)."""

    codigo = models.CharField(max_length=32, unique=True)
    nombre = models.CharField(max_length=120)
    descripcion = models.CharField(max_length=200, blank=True, default="")
    requiere_vencimiento = models.BooleanField(default=True)
    dias_aviso = models.PositiveIntegerField(
        default=30, help_text="Días antes del vencimiento en que se avisa que está por vencer.")
    # Si está vencido, ¿la persona deja de poder ingresar? Hay documentación que
    # se archiva y documentación que habilita; sólo la segunda frena el paso.
    bloquea_acceso = models.BooleanField(default=False)
    activo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "conc_tipo_documento"
        verbose_name = "Tipo de documento"
        verbose_name_plural = "Tipos de documento"
        ordering = ("nombre",)

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return self.nombre


def ruta_documento(instance: "Documento", filename: str) -> str:
    """documentos/<id_cliente>/<uuid>.<ext> — el nombre original no se usa como
    ruta: llega del navegador y no hay que confiarle el path."""
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[1].lower()[:10]
    return f"documentos/{instance.id_cliente}/{uuid.uuid4().hex}{ext}"


class Documento(models.Model):
    """Un documento adjunto de una persona del control de acceso.

    Cuelga de ``id_cliente`` y no de ``Concesionario`` a propósito: la
    documentación con vencimiento le puede corresponder a cualquiera que pase
    por una puerta —un profesor, un proveedor, un socio con una autorización
    médica— y no sólo a los concesionarios.
    """

    SIN_VENCIMIENTO = "sin_vencimiento"
    VIGENTE = "vigente"
    POR_VENCER = "por_vencer"
    VENCIDO = "vencido"
    ESTADOS = {
        SIN_VENCIMIENTO: "Sin vencimiento",
        VIGENTE: "Vigente",
        POR_VENCER: "Por vencer",
        VENCIDO: "Vencido",
    }

    id_cliente = models.IntegerField(db_index=True)
    tipo = models.ForeignKey(TipoDocumento, on_delete=models.PROTECT, related_name="documentos")
    numero = models.CharField(max_length=64, blank=True, default="")
    archivo = models.FileField(upload_to=ruta_documento, blank=True, null=True)
    archivo_nombre = models.CharField(max_length=255, blank=True, default="")
    fecha_emision = models.DateField(null=True, blank=True)
    fecha_vencimiento = models.DateField(null=True, blank=True, db_index=True)
    observaciones = models.CharField(max_length=300, blank=True, default="")
    subido_por = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "conc_documento"
        verbose_name = "Documento"
        verbose_name_plural = "Documentos"
        ordering = ("fecha_vencimiento", "tipo__nombre")
        indexes = [models.Index(fields=["id_cliente", "fecha_vencimiento"])]

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"{self.tipo} de {self.id_cliente}"

    def clean(self):
        super().clean()
        errores = {}
        if self.fecha_emision and self.fecha_vencimiento and self.fecha_vencimiento < self.fecha_emision:
            errores["fecha_vencimiento"] = "El vencimiento no puede ser anterior a la emisión."
        if self.tipo_id and self.tipo.requiere_vencimiento and not self.fecha_vencimiento:
            errores["fecha_vencimiento"] = f"{self.tipo.nombre} requiere fecha de vencimiento."
        if errores:
            raise ValidationError(errores)

    # -- estado -------------------------------------------------------------
    def estado(self, hoy: date | None = None) -> str:
        if not self.fecha_vencimiento:
            return self.SIN_VENCIMIENTO
        hoy = hoy or timezone.localdate()
        if self.fecha_vencimiento < hoy:
            return self.VENCIDO
        aviso = self.tipo.dias_aviso if self.tipo_id else 30
        if self.fecha_vencimiento <= hoy + timedelta(days=aviso):
            return self.POR_VENCER
        return self.VIGENTE

    def dias_para_vencer(self, hoy: date | None = None) -> int | None:
        if not self.fecha_vencimiento:
            return None
        return (self.fecha_vencimiento - (hoy or timezone.localdate())).days


# --------------------------------------------------------------------- la foto
class FotoPersona(models.Model):
    """Foto cargada a mano para el rostro de BioStar.

    De los 101 concesionarios de la nómina, **4** tienen foto en xSys: el
    enrolamiento facial no tiene de dónde sacar la cara. Esta es la que carga
    la oficina, y manda sobre la de xSys donde haya que mostrar a la persona.

    La imagen va como binario en la base, igual que ``XsysSocioFoto``, y no como
    archivo: es lo que consumen ``make_thumbnail`` y ``resize_for_face``, y lo
    que hay que mandarle a BioStar en base64. Cuelga de ``id_cliente`` —no de
    ``Concesionario``— por lo mismo que la documentación: le sirve a cualquiera
    que pase por una puerta.
    """

    id_cliente = models.IntegerField(unique=True, db_index=True)
    imagen = models.BinaryField()
    thumbnail = models.BinaryField(null=True, blank=True)
    content_type = models.CharField(max_length=60, blank=True, default="image/jpeg")
    sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    subido_por = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    # Resultado del último intento de enrolar el rostro en BioStar.
    enrolada_at = models.DateTimeField(null=True, blank=True)
    enrolada_resultado = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "conc_foto_persona"
        verbose_name = "Foto cargada"
        verbose_name_plural = "Fotos cargadas"
        ordering = ("-created_at",)

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"Foto de {self.id_cliente}"

    @property
    def enrolada(self) -> bool:
        return bool(self.enrolada_at and self.enrolada_resultado.startswith(("enrolled", "created")))

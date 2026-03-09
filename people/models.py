from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models


class PersonType(models.TextChoices):
    MEMBER = "member", "Socio"
    EMPLOYEE = "employee", "Empleado"
    PROVIDER = "provider", "Proveedor"
    GUEST = "guest", "Invitado"


class GuestType(models.TextChoices):
    MEMBER_GUEST = "member_guest", "Invitado Acompañante Socio"
    EVENT_VISITOR = "event_visitor", "Invitado Visitante Evento"


class Person(models.Model):
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    dni = models.CharField(max_length=32, unique=True)
    address = models.CharField(max_length=255)
    phone = models.CharField(max_length=32)
    email = models.EmailField()
    credential_code = models.CharField(max_length=64, unique=True, null=True, blank=True)
    facial_enrolled = models.BooleanField(default=False)
    person_type = models.CharField(max_length=16, choices=PersonType.choices)
    person_category = models.ForeignKey(
        "people.PersonCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="persons",
    )
    guest_type = models.CharField(
        max_length=32,
        choices=GuestType.choices,
        null=True,
        blank=True,
        help_text="Requerido para personas de tipo invitado.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    def clean(self):
        super().clean()
        if self.person_type == PersonType.GUEST and not self.guest_type:
            raise ValidationError({"guest_type": "Debe seleccionar el tipo de invitado."})
        if self.person_type != PersonType.GUEST and self.guest_type:
            raise ValidationError({"guest_type": "Solo los invitados pueden tener un tipo de invitado."})

    def __str__(self):
        return f"{self.last_name}, {self.first_name}"


class GuestInvitation(models.Model):
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="guest_invitations",
    )
    event = models.ForeignKey(
        "institutions.Event",
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    guest_type = models.CharField(max_length=32, choices=GuestType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Invitación"
        verbose_name_plural = "Invitaciones"
        unique_together = ("person", "event")

    def clean(self):
        super().clean()
        if self.person.person_type != PersonType.GUEST:
            raise ValidationError({"person": "Solo se pueden invitar personas registradas como invitados."})
        if self.guest_type != self.person.guest_type:
            raise ValidationError(
                {"guest_type": "El tipo de invitado debe coincidir con el tipo configurado en la persona."}
            )
        if self.event and self.guest_type not in self.event.allowed_guest_types:
            raise ValidationError({"event": "El evento no admite invitados de este tipo."})

    def __str__(self):
        return f"{self.person} -> {self.event}"


class PersonCategory(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DocumentType(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    requires_expiration = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PersonCategoryDocumentRequirement(models.Model):
    person_category = models.ForeignKey(
        "people.PersonCategory",
        on_delete=models.CASCADE,
        related_name="document_requirements",
    )
    document_type = models.ForeignKey(
        "people.DocumentType",
        on_delete=models.CASCADE,
        related_name="category_requirements",
    )
    is_mandatory = models.BooleanField(default=True)
    requires_expiration = models.BooleanField(default=False)
    max_validity_days = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = ("person_category", "document_type")
        ordering = ["person_category__name", "document_type__name"]

    def __str__(self):
        return f"{self.person_category} - {self.document_type}"


class PersonDocument(models.Model):
    class Status(models.TextChoices):
        VALID = "valid", "Vigente"
        EXPIRING_SOON = "expiring_soon", "Por vencer"
        EXPIRED = "expired", "Vencido"

    person = models.ForeignKey("people.Person", on_delete=models.CASCADE, related_name="documents")
    document_type = models.ForeignKey(
        "people.DocumentType",
        on_delete=models.CASCADE,
        related_name="person_documents",
    )
    document_number = models.CharField(max_length=64, blank=True)
    file_url = models.URLField(blank=True)
    issued_at = models.DateField(null=True, blank=True)
    expires_at = models.DateField(null=True, blank=True)

    class Meta:
        unique_together = ("person", "document_type", "document_number")
        ordering = ["expires_at", "person__last_name"]

    def get_status(self, warning_days: int = 30) -> str:
        if not self.expires_at:
            return self.Status.VALID
        from django.utils import timezone

        today = timezone.localdate()
        if self.expires_at < today:
            return self.Status.EXPIRED
        if self.expires_at <= today + timedelta(days=warning_days):
            return self.Status.EXPIRING_SOON
        return self.Status.VALID

    def clean(self):
        super().clean()
        errors = {}
        if self.issued_at and self.expires_at and self.expires_at < self.issued_at:
            errors["expires_at"] = "La fecha de vencimiento debe ser posterior a la de emisión."
        if self.document_type and self.document_type.requires_expiration and not self.expires_at:
            errors["expires_at"] = "Este tipo documental requiere vencimiento."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.person} - {self.document_type}"


class Cliente(models.Model):
    id_cliente = models.IntegerField(primary_key=True, db_column="Id_Cliente")
    razon_social = models.CharField(max_length=100, null=True, blank=True, db_column="Razon_Social")
    apellido = models.CharField(max_length=100, null=True, blank=True, db_column="Apellido")
    nombre = models.CharField(max_length=100, null=True, blank=True, db_column="Nombre")
    sexo = models.CharField(max_length=1, null=True, blank=True, db_column="Sexo")
    fecha_nac = models.DateTimeField(null=True, blank=True, db_column="Fecha_Nac")
    id_tipo_doc = models.CharField(max_length=3, null=True, blank=True, db_column="Id_Tipo_Doc")
    doc_nro = models.BigIntegerField(null=True, blank=True, db_column="Doc_Nro")
    ingresos_brutos = models.CharField(max_length=20, null=True, blank=True, db_column="Ingresos_Brutos")
    direccion = models.CharField(max_length=100, null=True, blank=True, db_column="Direccion")
    cp = models.CharField(max_length=10, null=True, blank=True, db_column="Cp")
    id_pais = models.CharField(max_length=3, null=True, blank=True, db_column="Id_Pais")
    id_provincia = models.CharField(max_length=3, null=True, blank=True, db_column="Id_Provincia")
    provincia_descrip = models.CharField(max_length=200, null=True, blank=True, db_column="Provincia_Descrip")
    id_localidad = models.IntegerField(null=True, blank=True, db_column="Id_Localidad")
    localidad_descrip = models.CharField(max_length=200, null=True, blank=True, db_column="Localidad_Descrip")
    fax = models.TextField(null=True, blank=True, db_column="Fax")
    cuit = models.CharField(max_length=19, null=True, blank=True, db_column="Cuit")
    email = models.CharField(max_length=600, null=True, blank=True, db_column="Email")
    activo = models.SmallIntegerField(null=True, blank=True, db_column="Activo")
    id_estado_cliente = models.SmallIntegerField(null=True, blank=True, db_column="Id_Estado_Cliente")
    id_grupo_afinidad = models.SmallIntegerField(null=True, blank=True, db_column="Id_Grupo_Afinidad")
    porc_desc_especial = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        db_column="Porc_DescEspecial",
    )
    id_lista_precio = models.SmallIntegerField(null=True, blank=True, db_column="Id_Lista_Precio")
    id_vendedor = models.CharField(max_length=10, null=True, blank=True, db_column="Id_Vendedor")
    id_transportista = models.CharField(max_length=10, null=True, blank=True, db_column="Id_Transportista")
    bonif = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, db_column="Bonif")
    id_cond_pago = models.SmallIntegerField(null=True, blank=True, db_column="Id_Cond_Pago")
    cred_ctacte = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, db_column="Cred_CtaCte")
    cred_che_pro = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, db_column="Cred_ChePro")
    cred_che_ter = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, db_column="Cred_CheTer")
    id_iva = models.CharField(max_length=3, null=True, blank=True, db_column="Id_Iva")
    cte = models.CharField(max_length=50, null=True, blank=True, db_column="CTE")
    id_concepto = models.IntegerField(null=True, blank=True, db_column="Id_Concepto")
    id_cta_banco = models.SmallIntegerField(null=True, blank=True, db_column="Id_Cta_Banco")
    id_zona = models.CharField(max_length=5, null=True, blank=True, db_column="Id_Zona")
    observacion = models.TextField(null=True, blank=True, db_column="Observacion")
    id_cliente_externo = models.CharField(max_length=14, null=True, blank=True, db_column="Id_Cliente_Externo")
    id_transporte = models.IntegerField(null=True, blank=True, db_column="Id_Transporte")
    id_alias_cta_cont_venta = models.SmallIntegerField(
        null=True,
        blank=True,
        db_column="Id_Alias_Cta_Cont_Venta",
    )
    mercado = models.CharField(max_length=1, null=True, blank=True, db_column="Mercado")
    observa_fact = models.TextField(null=True, blank=True, db_column="Observa_Fact")
    entre_calle_1 = models.CharField(max_length=35, null=True, blank=True, db_column="Entre_Calle_1")
    entre_calle_2 = models.CharField(max_length=35, null=True, blank=True, db_column="Entre_Calle_2")
    plano_nro = models.CharField(max_length=10, null=True, blank=True, db_column="Plano_Nro")
    plano_matriz = models.CharField(max_length=10, null=True, blank=True, db_column="Plano_Matriz")
    id_cobrador = models.CharField(max_length=10, null=True, blank=True, db_column="Id_Cobrador")
    id_motivo_est = models.SmallIntegerField(null=True, blank=True, db_column="Id_Motivo_Est")
    id_tipo_cli = models.SmallIntegerField(null=True, blank=True, db_column="Id_Tipo_Cli")
    id_dep_fisico = models.CharField(max_length=3, null=True, blank=True, db_column="Id_Dep_Fisico")
    id_dep_logico = models.CharField(max_length=3, null=True, blank=True, db_column="Id_Dep_Logico")
    id_tarjeta = models.CharField(max_length=10, null=True, blank=True, db_column="Id_Tarjeta")
    id_banco = models.IntegerField(null=True, blank=True, db_column="Id_Banco")
    tar_nro = models.CharField(max_length=50, null=True, blank=True, db_column="Tar_Nro")
    tar_fecha_vence = models.DateTimeField(null=True, blank=True, db_column="Tar_Fecha_Vence")
    tar_titular = models.CharField(max_length=50, null=True, blank=True, db_column="Tar_Titular")
    direccion_cob = models.CharField(max_length=100, null=True, blank=True, db_column="Direccion_Cob")
    cp_cob = models.CharField(max_length=10, null=True, blank=True, db_column="Cp_Cob")
    id_pais_cob = models.CharField(max_length=3, null=True, blank=True, db_column="Id_Pais_Cob")
    id_provincia_cob = models.CharField(max_length=3, null=True, blank=True, db_column="Id_Provincia_Cob")
    provincia_descrip_cob = models.CharField(max_length=200, null=True, blank=True, db_column="Provincia_Descrip_Cob")
    id_localidad_cob = models.IntegerField(null=True, blank=True, db_column="Id_Localidad_Cob")
    localidad_descrip_cob = models.CharField(max_length=200, null=True, blank=True, db_column="Localidad_Descrip_Cob")
    entre_calle_1_cob = models.CharField(max_length=35, null=True, blank=True, db_column="Entre_Calle_1_Cob")
    entre_calle_2_cob = models.CharField(max_length=35, null=True, blank=True, db_column="Entre_Calle_2_Cob")
    plano_nro_cob = models.CharField(max_length=10, null=True, blank=True, db_column="Plano_Nro_Cob")
    plano_matriz_cob = models.CharField(max_length=10, null=True, blank=True, db_column="Plano_Matriz_Cob")
    fecha_ven_trib = models.DateTimeField(null=True, blank=True, db_column="Fecha_Ven_Trib")
    web = models.CharField(max_length=254, null=True, blank=True, db_column="Web")
    id_cliente_ref = models.IntegerField(null=True, blank=True, db_column="Id_Cliente_Ref")
    fecha_alta = models.DateTimeField(null=True, blank=True, db_column="Fecha_Alta")
    fecha_baja = models.DateTimeField(null=True, blank=True, db_column="Fecha_Baja")
    id_centro_costo = models.CharField(max_length=10, null=True, blank=True, db_column="Id_Centro_Costo")
    id_promotor = models.CharField(max_length=10, null=True, blank=True, db_column="Id_Promotor")
    nombre_fantasia = models.CharField(max_length=100, null=True, blank=True, db_column="Nombre_Fantasia")
    id_cond_vta = models.CharField(max_length=10, null=True, blank=True, db_column="Id_Cond_Vta")
    id_puerto = models.CharField(max_length=3, null=True, blank=True, db_column="Id_Puerto")
    id_calle = models.IntegerField(null=True, blank=True, db_column="Id_Calle")
    nro = models.IntegerField(null=True, blank=True, db_column="Nro")
    depto = models.CharField(max_length=10, null=True, blank=True, db_column="Depto")
    fecha_modif = models.DateTimeField(null=True, blank=True, db_column="Fecha_Modif")
    foto = models.CharField(max_length=254, null=True, blank=True, db_column="Foto")
    latitud = models.DecimalField(max_digits=11, decimal_places=7, null=True, blank=True, db_column="Latitud")
    longitud = models.DecimalField(max_digits=11, decimal_places=7, null=True, blank=True, db_column="Longitud")
    id_calle_cob = models.IntegerField(null=True, blank=True, db_column="Id_Calle_Cob")
    nro_cob = models.IntegerField(null=True, blank=True, db_column="Nro_Cob")
    depto_cob = models.CharField(max_length=10, null=True, blank=True, db_column="Depto_Cob")
    latitud_cob = models.DecimalField(max_digits=11, decimal_places=7, null=True, blank=True, db_column="Latitud_Cob")
    longitud_cob = models.DecimalField(
        max_digits=11,
        decimal_places=7,
        null=True,
        blank=True,
        db_column="Longitud_Cob",
    )
    id_precio_especial = models.IntegerField(null=True, blank=True, db_column="Id_Precio_Especial")
    cod_autoriz_trib = models.CharField(max_length=30, null=True, blank=True, db_column="Cod_Autoriz_Trib")
    id_alias_cta_cont_venta_dol = models.SmallIntegerField(
        null=True,
        blank=True,
        db_column="Id_Alias_Cta_Cont_Venta_Dol",
    )
    estado_civil = models.CharField(max_length=1, null=True, blank=True, db_column="Estado_Civil")
    id_nacionalidad = models.CharField(max_length=3, null=True, blank=True, db_column="Id_Nacionalidad")
    id_cli_relac = models.SmallIntegerField(null=True, blank=True, db_column="Id_Cli_Relac")
    tipo_persona = models.CharField(max_length=1, null=True, blank=True, db_column="Tipo_Persona")
    telefono = models.CharField(max_length=900, null=True, blank=True, db_column="telefono")
    calle_descrip = models.CharField(max_length=100, null=True, blank=True, db_column="Calle_Descrip")
    calle_cob_descrip = models.CharField(max_length=100, null=True, blank=True, db_column="Calle_Cob_Descrip")
    imp_aporte = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, db_column="Imp_Aporte")
    fecha_aporte = models.DateTimeField(null=True, blank=True, db_column="Fecha_Aporte")
    credencial_nro = models.CharField(max_length=30, null=True, blank=True, db_column="Credencial_Nro")
    credencial_banda1 = models.CharField(max_length=100, null=True, blank=True, db_column="Credencial_Banda1")
    credencial_banda2 = models.CharField(max_length=100, null=True, blank=True, db_column="Credencial_Banda2")
    credencial_imp = models.SmallIntegerField(null=True, blank=True, db_column="Credencial_Imp")
    ult_cuota_paga = models.DateTimeField(null=True, blank=True, db_column="Ult_Cuota_Paga")
    legajo = models.CharField(max_length=30, null=True, blank=True, db_column="Legajo")
    credencial_entrega = models.DateTimeField(null=True, blank=True, db_column="Credencial_Entrega")
    id_unid_negocio = models.CharField(max_length=10, null=True, blank=True, db_column="Id_Unid_Negocio")
    clave_web = models.CharField(max_length=30, null=True, blank=True, db_column="Clave_Web")
    flag_anmat = models.SmallIntegerField(null=True, blank=True, db_column="Flag_Anmat")
    nro_anmat = models.CharField(max_length=26, null=True, blank=True, db_column="Nro_Anmat")
    tar_cuit = models.CharField(max_length=19, null=True, blank=True, db_column="Tar_Cuit")
    id_anmat = models.CharField(max_length=3, null=True, blank=True, db_column="Id_Anmat")
    coef_comi_vta = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True, db_column="Coef_Comi_Vta")
    coef_comi_cobros = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        db_column="Coef_Comi_Cobros",
    )
    email_cob = models.CharField(max_length=600, null=True, blank=True, db_column="Email_Cob")
    recorrido_cob = models.IntegerField(null=True, blank=True, db_column="Recorrido_Cob")
    tar_fecha_alta = models.DateTimeField(null=True, blank=True, db_column="Tar_Fecha_Alta")
    tar_fecha_baja = models.DateTimeField(null=True, blank=True, db_column="Tar_Fecha_Baja")
    imp_fac_max_mes = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        db_column="Imp_Fac_Max_Mes",
    )
    email_web = models.CharField(max_length=300, null=True, blank=True, db_column="Email_Web")
    tel_movil = models.CharField(max_length=20, null=True, blank=True, db_column="Tel_Movil")
    flag_tel_movil_verif = models.SmallIntegerField(null=True, blank=True, db_column="Flag_Tel_Movil_Verif")
    flag_comunic_sms = models.SmallIntegerField(null=True, blank=True, db_column="Flag_Comunic_Sms")
    flag_comunic_email = models.SmallIntegerField(null=True, blank=True, db_column="Flag_Comunic_Email")
    carpeta = models.CharField(max_length=256, null=True, blank=True, db_column="Carpeta")
    id_emp_area = models.SmallIntegerField(null=True, blank=True, db_column="Id_Emp_Area")
    id_ar_arba_cot_tipo_dom_nro = models.CharField(
        max_length=5,
        null=True,
        blank=True,
        db_column="Id_AR_ARBA_COT_Tipo_Dom_Nro",
    )
    dir_entrega_numero = models.SmallIntegerField(null=True, blank=True, db_column="Dir_Entrega_Numero")
    dir_entrega_piso = models.CharField(max_length=3, null=True, blank=True, db_column="Dir_Entrega_Piso")
    dir_entrega_dto = models.CharField(max_length=4, null=True, blank=True, db_column="Dir_Entrega_Dto")
    dir_entrega_barrio = models.CharField(max_length=30, null=True, blank=True, db_column="Dir_Entrega_Barrio")
    dir_entrega_cp = models.CharField(max_length=8, null=True, blank=True, db_column="Dir_Entrega_CP")
    dir_entrega_localidad = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        db_column="Dir_Entrega_Localidad",
    )
    id_cliente_grupo = models.IntegerField(null=True, blank=True, db_column="Id_Cliente_Grupo")
    id_usuario_alta = models.SmallIntegerField(null=True, blank=True, db_column="Id_Usuario_Alta")
    id_usuario_baja = models.SmallIntegerField(null=True, blank=True, db_column="Id_Usuario_Baja")
    flag_fact_no_imprimir = models.SmallIntegerField(null=True, blank=True, db_column="Flag_Fact_No_Imprimir")
    flag_fact_no_mail = models.SmallIntegerField(null=True, blank=True, db_column="Flag_Fact_No_Mail")
    id_facebook = models.CharField(max_length=255, null=True, blank=True, db_column="Id_Facebook")
    id_instagram = models.CharField(max_length=255, null=True, blank=True, db_column="Id_Instagram")
    id_twitter = models.CharField(max_length=255, null=True, blank=True, db_column="Id_Twitter")
    id_linkedin = models.CharField(max_length=255, null=True, blank=True, db_column="Id_Linkedin")
    id_ogle = models.CharField(max_length=255, null=True, blank=True, db_column="Id_ogle")
    id_microsoft = models.CharField(max_length=255, null=True, blank=True, db_column="Id_Microsoft")
    fecha_app = models.DateTimeField(null=True, blank=True, db_column="Fecha_App")
    id_google = models.CharField(max_length=255, null=True, blank=True, db_column="Id_Google")
    z_id_invitado_2020_verano = models.IntegerField(
        null=True,
        blank=True,
        db_column="z_Id_Invitado_2020__Verano",
    )
    pasap_nro = models.CharField(max_length=50, null=True, blank=True, db_column="Pasap_Nro")
    fecha_web_registro = models.DateTimeField(null=True, blank=True, db_column="Fecha_Web_Registro")
    fecha_web_ult_ingreso = models.DateTimeField(null=True, blank=True, db_column="Fecha_Web_Ult_Ingreso")
    flag_fac_dir_cli = models.SmallIntegerField(null=True, blank=True, db_column="Flag_Fac_Dir_Cli")
    fecha_sinc_event = models.DateTimeField(null=True, blank=True, db_column="Fecha_Sinc_Event")
    fecha_ult_modif_dp = models.DateTimeField(null=True, blank=True, db_column="Fecha_Ult_Modif_DP")
    login_codigo_acceso = models.CharField(max_length=150, null=True, blank=True, db_column="Login_Codigo_Acceso")

    class Meta:
        db_table = "Clientes"
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"

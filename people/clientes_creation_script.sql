USE [xsys_geba]
GO

/****** Object:  Table [dbo].[Clientes]    Script Date: 01/04/2026 11:32:39 a.m. ******/
SET ANSI_NULLS ON
GO

SET QUOTED_IDENTIFIER ON
GO

SET ANSI_PADDING ON
GO

CREATE TABLE [dbo].[Clientes](
	[Id_Cliente] [int] NOT NULL,
	[Razon_Social] [varchar](100) NULL,
	[Apellido] [varchar](100) NULL,
	[Nombre] [varchar](100) NULL,
	[Sexo] [char](1) NULL CONSTRAINT [DF__Clientes__Sexo__308E3499]  DEFAULT ('N'),
	[Fecha_Nac] [datetime] NULL,
	[Id_Tipo_Doc] [char](3) NULL,
	[Doc_Nro] [bigint] NULL,
	[Ingresos_Brutos] [varchar](20) NULL,
	[Direccion] [varchar](100) NULL,
	[Cp] [varchar](10) NULL,
	[Id_Pais] [char](3) NULL,
	[Id_Provincia] [char](3) NULL,
	[Provincia_Descrip] [varchar](200) NULL,
	[Id_Localidad] [int] NULL,
	[Localidad_Descrip] [varchar](200) NULL,
	[Fax] [text] NULL,
	[Cuit] [varchar](19) NULL,
	[Email] [varchar](600) NULL,
	[Activo] [tinyint] NULL,
	[Id_Estado_Cliente] [smallint] NULL,
	[Id_Grupo_Afinidad] [smallint] NULL,
	[Porc_DescEspecial] [decimal](5, 2) NULL,
	[Id_Lista_Precio] [smallint] NULL,
	[Id_Vendedor] [varchar](10) NULL,
	[Id_Transportista] [varchar](10) NULL,
	[Bonif] [decimal](5, 2) NULL,
	[Id_Cond_Pago] [smallint] NULL,
	[Cred_CtaCte] [decimal](12, 2) NULL,
	[Cred_ChePro] [decimal](12, 2) NULL,
	[Cred_CheTer] [decimal](12, 2) NULL,
	[Id_Iva] [char](3) NULL,
	[CTE] [varchar](50) NULL,
	[Id_Concepto] [int] NULL,
	[Id_Cta_Banco] [smallint] NULL,
	[Id_Zona] [char](5) NULL,
	[Observacion] [text] NULL,
	[Id_Cliente_Externo] [varchar](14) NULL,
	[Id_Transporte] [int] NULL,
	[Id_Alias_Cta_Cont_Venta] [smallint] NULL,
	[Mercado] [char](1) NULL,
	[Observa_Fact] [text] NULL,
	[Entre_Calle_1] [varchar](35) NULL,
	[Entre_Calle_2] [varchar](35) NULL,
	[Plano_Nro] [varchar](10) NULL,
	[Plano_Matriz] [varchar](10) NULL,
	[Id_Cobrador] [varchar](10) NULL,
	[Id_Motivo_Est] [smallint] NULL,
	[Id_Tipo_Cli] [smallint] NULL,
	[Id_Dep_Fisico] [char](3) NULL,
	[Id_Dep_Logico] [char](3) NULL,
	[Id_Tarjeta] [varchar](10) NULL,
	[Id_Banco] [int] NULL,
	[Tar_Nro] [varchar](50) NULL,
	[Tar_Fecha_Vence] [datetime] NULL,
	[Tar_Titular] [varchar](50) NULL,
	[Direccion_Cob] [varchar](100) NULL,
	[Cp_Cob] [varchar](10) NULL,
	[Id_Pais_Cob] [char](3) NULL,
	[Id_Provincia_Cob] [char](3) NULL,
	[Provincia_Descrip_Cob] [varchar](200) NULL,
	[Id_Localidad_Cob] [int] NULL,
	[Localidad_Descrip_Cob] [varchar](200) NULL,
	[Entre_Calle_1_Cob] [varchar](35) NULL,
	[Entre_Calle_2_Cob] [varchar](35) NULL,
	[Plano_Nro_Cob] [varchar](10) NULL,
	[Plano_Matriz_Cob] [varchar](10) NULL,
	[Fecha_Ven_Trib] [datetime] NULL,
	[Web] [varchar](254) NULL,
	[Id_Cliente_Ref] [int] NULL,
	[Fecha_Alta] [datetime] NULL,
	[Fecha_Baja] [datetime] NULL,
	[Id_Centro_Costo] [char](10) NULL,
	[Id_Promotor] [char](10) NULL,
	[Nombre_Fantasia] [varchar](100) NULL,
	[Id_Cond_Vta] [char](10) NULL,
	[Id_Puerto] [char](3) NULL,
	[Id_Calle] [int] NULL,
	[Nro] [int] NULL,
	[Depto] [char](10) NULL,
	[Fecha_Modif] [datetime] NULL,
	[Foto] [varchar](254) NULL,
	[Latitud] [decimal](11, 7) NULL,
	[Longitud] [decimal](11, 7) NULL,
	[Id_Calle_Cob] [int] NULL,
	[Nro_Cob] [int] NULL,
	[Depto_Cob] [varchar](10) NULL,
	[Latitud_Cob] [decimal](11, 7) NULL,
	[Longitud_Cob] [decimal](11, 7) NULL,
	[Id_Precio_Especial] [int] NULL,
	[Cod_Autoriz_Trib] [varchar](30) NULL,
	[Id_Alias_Cta_Cont_Venta_Dol] [smallint] NULL,
	[Estado_Civil] [char](1) NULL,
	[Id_Nacionalidad] [char](3) NULL,
	[Id_Cli_Relac] [smallint] NULL,
	[Tipo_Persona] [char](1) NULL,
	[telefono] [varchar](900) NULL,
	[Calle_Descrip] [varchar](100) NULL,
	[Calle_Cob_Descrip] [varchar](100) NULL,
	[Imp_Aporte] [decimal](12, 2) NULL,
	[Fecha_Aporte] [datetime] NULL,
	[Credencial_Nro] [varchar](30) NULL,
	[Credencial_Banda1] [varchar](100) NULL,
	[Credencial_Banda2] [varchar](100) NULL,
	[Credencial_Imp] [smallint] NULL,
	[Ult_Cuota_Paga] [datetime] NULL,
	[Legajo] [varchar](30) NULL,
	[Credencial_Entrega] [datetime] NULL,
	[Id_Unid_Negocio] [char](10) NULL,
	[Clave_Web] [varchar](30) NULL,
	[Flag_Anmat] [tinyint] NULL,
	[Nro_Anmat] [varchar](26) NULL,
	[Tar_Cuit] [varchar](19) NULL,
	[Id_Anmat] [char](3) NULL,
	[Coef_Comi_Vta] [decimal](8, 4) NULL,
	[Coef_Comi_Cobros] [decimal](8, 4) NULL,
	[Email_Cob] [varchar](600) NULL,
	[Recorrido_Cob] [int] NULL,
	[Tar_Fecha_Alta] [datetime] NULL,
	[Tar_Fecha_Baja] [datetime] NULL,
	[Imp_Fac_Max_Mes] [decimal](12, 2) NULL,
	[Email_Web] [varchar](300) NULL,
	[Tel_Movil] [varchar](20) NULL,
	[Flag_Tel_Movil_Verif] [tinyint] NULL,
	[Flag_Comunic_Sms] [tinyint] NULL,
	[Flag_Comunic_Email] [tinyint] NULL,
	[Carpeta] [varchar](256) NULL,
	[Id_Emp_Area] [smallint] NULL,
	[Id_AR_ARBA_COT_Tipo_Dom_Nro] [varchar](5) NULL,
	[Dir_Entrega_Numero] [smallint] NULL,
	[Dir_Entrega_Piso] [varchar](3) NULL,
	[Dir_Entrega_Dto] [varchar](4) NULL,
	[Dir_Entrega_Barrio] [varchar](30) NULL,
	[Dir_Entrega_CP] [varchar](8) NULL,
	[Dir_Entrega_Localidad] [varchar](50) NULL,
	[Id_Cliente_Grupo] [int] NULL,
	[Id_Usuario_Alta] [smallint] NULL,
	[Id_Usuario_Baja] [smallint] NULL,
	[Flag_Fact_No_Imprimir] [tinyint] NULL,
	[Flag_Fact_No_Mail] [tinyint] NULL,
	[Id_Facebook] [varchar](255) NULL,
	[Id_Instagram] [varchar](255) NULL,
	[Id_Twitter] [varchar](255) NULL,
	[Id_Linkedin] [varchar](255) NULL,
	[Id_ogle] [varchar](255) NULL,
	[Id_Microsoft] [varchar](255) NULL,
	[Fecha_App] [datetime] NULL,
	[Id_Google] [varchar](255) NULL,
	[z_Id_Invitado_2020__Verano] [int] NULL,
	[Pasap_Nro] [varchar](50) NULL,
	[Fecha_Web_Registro] [datetime] NULL,
	[Fecha_Web_Ult_Ingreso] [datetime] NULL,
	[Flag_Fac_Dir_Cli] [tinyint] NULL,
	[Fecha_Sinc_Event] [datetime] NULL,
	[Fecha_Ult_Modif_DP] [datetime] NULL,
	[Login_Codigo_Acceso] [varchar](150) NULL,
 CONSTRAINT [PK_CLI_PorId] PRIMARY KEY CLUSTERED
(
	[Id_Cliente] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]

GO

SET ANSI_PADDING OFF
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_AR_ARBA_COT_Tipo_Domicilio_Nro] FOREIGN KEY([Id_AR_ARBA_COT_Tipo_Dom_Nro])
REFERENCES [dbo].[AR_ARBA_COT_Tipo_Domicilio_Nro] ([Id_AR_ARBA_COT_Tipo_Dom_Nro])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_AR_ARBA_COT_Tipo_Domicilio_Nro]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Bancos_Ctas] FOREIGN KEY([Id_Cta_Banco])
REFERENCES [dbo].[Bancos_Ctas] ([Id_Cta_Banco])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Bancos_Ctas]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Cbtes_Cond_Pago] FOREIGN KEY([Id_Cond_Pago])
REFERENCES [dbo].[Cbtes_Cond_Pago] ([Id_Cond_Pago])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Cbtes_Cond_Pago]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Cbtes_Cond_Vtas] FOREIGN KEY([Id_Cond_Vta])
REFERENCES [dbo].[Cbtes_Cond_Vtas] ([Id_Cond_Vta])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Cbtes_Cond_Vtas]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Centro_Costos] FOREIGN KEY([Id_Centro_Costo])
REFERENCES [dbo].[Centro_Costos] ([Id_Centro_Costo])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Centro_Costos]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Clientes] FOREIGN KEY([Id_Cliente_Ref])
REFERENCES [dbo].[Clientes] ([Id_Cliente])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Clientes]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Clientes_Estados] FOREIGN KEY([Id_Estado_Cliente])
REFERENCES [dbo].[Clientes_Estados] ([Id_Estado_Cliente])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Clientes_Estados]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Clientes_Promotores] FOREIGN KEY([Id_Promotor])
REFERENCES [dbo].[Clientes_Promotores] ([Id_Promotor])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Clientes_Promotores]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Clientes_Relaciones] FOREIGN KEY([Id_Cli_Relac])
REFERENCES [dbo].[Clientes_Relaciones] ([Id_Cli_Relac])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Clientes_Relaciones]
GO

ALTER TABLE [dbo].[Clientes]  WITH NOCHECK ADD  CONSTRAINT [FK_CLI_Clientes_Tipos] FOREIGN KEY([Id_Tipo_Cli])
REFERENCES [dbo].[Clientes_Tipos] ([Id_Tipo_Cli])
NOT FOR REPLICATION
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Clientes_Tipos]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Cobrador] FOREIGN KEY([Id_Cobrador])
REFERENCES [dbo].[Vendedores] ([Id_Vendedor])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Cobrador]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Conceptos] FOREIGN KEY([Id_Concepto])
REFERENCES [dbo].[Conceptos] ([Id_Concepto])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Conceptos]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_Cli_Cond_Vtas] FOREIGN KEY([Id_Cond_Vta])
REFERENCES [dbo].[Cbtes_Cond_Vtas] ([Id_Cond_Vta])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_Cli_Cond_Vtas]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Dep_Fisicos] FOREIGN KEY([Id_Dep_Fisico])
REFERENCES [dbo].[Dep_Fisicos] ([Id_Deposito_Fisico])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Dep_Fisicos]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Dep_Logicos] FOREIGN KEY([Id_Dep_Fisico], [Id_Dep_Logico])
REFERENCES [dbo].[Dep_Logicos] ([Id_Deposito_Fisico], [Id_Deposito_Log])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Dep_Logicos]
GO

ALTER TABLE [dbo].[Clientes]  WITH NOCHECK ADD  CONSTRAINT [FK_CLI_Documentos_Tipos] FOREIGN KEY([Id_Tipo_Doc])
REFERENCES [dbo].[Documentos_Tipos] ([Id_Tipo_Doc])
NOT FOR REPLICATION
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Documentos_Tipos]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_Cli_Emp_Areas] FOREIGN KEY([Id_Emp_Area])
REFERENCES [dbo].[Tab_Emp_Areas] ([Id_Emp_Area])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_Cli_Emp_Areas]
GO

ALTER TABLE [dbo].[Clientes]  WITH NOCHECK ADD  CONSTRAINT [FK_Cli_Empresas_Unid_Negocios] FOREIGN KEY([Id_Unid_Negocio])
REFERENCES [dbo].[Empresas_Unid_Negocios] ([Id_Unid_Negocio])
NOT FOR REPLICATION
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_Cli_Empresas_Unid_Negocios]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Iva] FOREIGN KEY([Id_Iva])
REFERENCES [dbo].[Iva] ([Id_Iva])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Iva]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Prod_Listas_Precios] FOREIGN KEY([Id_Lista_Precio])
REFERENCES [dbo].[Prod_Listas_Precios] ([Id_Lista_Precio])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Prod_Listas_Precios]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Proveedores] FOREIGN KEY([Id_Transporte])
REFERENCES [dbo].[Proveedores] ([Id_Proveedor])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Proveedores]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Tab_Calles] FOREIGN KEY([Id_Calle])
REFERENCES [dbo].[Tab_Calles] ([Id_Calle])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Tab_Calles]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Tab_Grupos_Afinidad] FOREIGN KEY([Id_Grupo_Afinidad])
REFERENCES [dbo].[Tab_Grupos_Afinidad] ([Id_Grupo_Afinidad])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Tab_Grupos_Afinidad]
GO

ALTER TABLE [dbo].[Clientes]  WITH NOCHECK ADD  CONSTRAINT [FK_CLI_Tab_Localidades] FOREIGN KEY([Id_Pais], [Id_Provincia], [Id_Localidad])
REFERENCES [dbo].[Tab_Localidades] ([Id_Pais], [Id_Provincia], [Id_Localidad])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Tab_Localidades]
GO

ALTER TABLE [dbo].[Clientes]  WITH NOCHECK ADD  CONSTRAINT [FK_CLI_Tab_Localidades_Cob] FOREIGN KEY([Id_Pais_Cob], [Id_Provincia_Cob], [Id_Localidad_Cob])
REFERENCES [dbo].[Tab_Localidades] ([Id_Pais], [Id_Provincia], [Id_Localidad])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Tab_Localidades_Cob]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Tab_Motivos_Est] FOREIGN KEY([Id_Motivo_Est])
REFERENCES [dbo].[Tab_Motivos_Est] ([Id_Motivo_Est])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Tab_Motivos_Est]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Tab_Paises] FOREIGN KEY([Id_Nacionalidad])
REFERENCES [dbo].[Tab_Paises] ([Id_Pais])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Tab_Paises]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Tab_Planos] FOREIGN KEY([Plano_Nro], [Plano_Matriz])
REFERENCES [dbo].[Tab_Planos] ([Plano_Nro], [Plano_Matriz])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Tab_Planos]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Tab_Provincias] FOREIGN KEY([Id_Pais_Cob], [Id_Provincia_Cob])
REFERENCES [dbo].[Tab_Provincias] ([Id_Pais], [Id_Provincia])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Tab_Provincias]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Tab_Puertos] FOREIGN KEY([Id_Puerto])
REFERENCES [dbo].[Tab_Puertos] ([Id_Puerto])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Tab_Puertos]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Tab_Zonas] FOREIGN KEY([Id_Zona])
REFERENCES [dbo].[Tab_Zonas] ([Id_Zona])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Tab_Zonas]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Valores_Tarjetas_Cred] FOREIGN KEY([Id_Tarjeta])
REFERENCES [dbo].[Valores_Tarjetas_Cred] ([Id_Tarjeta])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Valores_Tarjetas_Cred]
GO

ALTER TABLE [dbo].[Clientes]  WITH CHECK ADD  CONSTRAINT [FK_CLI_Vendedores] FOREIGN KEY([Id_Vendedor])
REFERENCES [dbo].[Vendedores] ([Id_Vendedor])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_CLI_Vendedores]
GO

ALTER TABLE [dbo].[Clientes]  WITH NOCHECK ADD  CONSTRAINT [FK_Clientes3_Grupos] FOREIGN KEY([Id_Cliente_Grupo])
REFERENCES [dbo].[Clientes] ([Id_Cliente])
GO

ALTER TABLE [dbo].[Clientes] CHECK CONSTRAINT [FK_Clientes3_Grupos]
GO



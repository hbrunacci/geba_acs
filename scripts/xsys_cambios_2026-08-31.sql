/* =============================================================================
   Cambios aplicados sobre xsys_geba el 31/08/2026, en el orden en que se
   aplicaron. Este archivo NO se ejecuta solo: es el registro de lo que quedo
   en la base, para poder reponerlo si la aplicacion de CleverSoft vuelve a
   recrear los objetos (como paso el 29/07/2026, que dejo CP_SCA_RegistrarAcceso
   sin un argumento y tiro abajo 6 dias de accesos por producto).

   Respaldos de las versiones anteriores, en la propia base:
     zCP_SCA_RegistrarAcceso_20260831     antes de la excepcion por bajas en revision
     zCP_SCA_RegistrarAcceso_20260831_b   antes del motivo 115 (QR vencido)
     zCP_SCA_RegistrarAcceso_20260831_c   antes del antirebote de lectura
     zCF_SCA_IdentifIdCliente_20260831    antes de la ventana de 5 min

   1) CP_SCA_RegistrarAcceso
      - excepcion por bajas en revision (CD_Clientes_Baja_Revision)
      - rechazo por QR vencido -> motivo 115, con el socio identificado
      - antirebote de lectura (3 s por tag + controlador)
   2) CF_SCA_IdentifIdCliente
      - ventana del QR dinamico de 3 a 5 minutos (3 lugares)
   3) CD_Motivos 115
      - texto que ve el socio

   Parametro opcional para ajustar el antirebote sin tocar el SP:
     INSERT INTO xParametros (IdParametro, Descripcion, TipoDato, Valor_Integer)
     VALUES ('SCA_Antirebote_Seg', 'Segundos de antirebote del control de acceso',
             'INTEGER', 3)
   Si no esta cargado rige el default de 3 s; con 0 el antirebote queda apagado.
   ============================================================================= */

/* ---------- CP_SCA_RegistrarAcceso ---------- */
GO
CREATE PROCEDURE [dbo].[CP_SCA_RegistrarAcceso]	@pId_Controlador		SMALLINT,
												@pTipoAcceso			CHAR(1),		/*E: Entrada / S:Salida*/
												@pFecha					DATETIME,		/*Fecha a comparar*/
												@pFechaEvento			DATETIME,		/*Fecha del Evento a comparar. Si no es un evento ingresar 19000101*/
												@pTag					VARCHAR(MAX),	/*Credencial MiFare / Wiegand*/
												@pDoc					BIGINT,			/*Documento*/
												@pHue					VARCHAR(5000),	/*Huella Dactilar*/
												@pCba					VARCHAR(20),	/*Codigo de Barra*/
												@pDma					VARCHAR(5000),	/*Data Matrix*/
												@pFlag_GrabaRegistro	TINYINT = NULL, /*23/09/2019. 1(default): Graba Registro. 0: No graba el registro*/
												@pFlag_Permite_Paso		TINYINT = NULL,	/*05/02/2020. 1: Si el resultado es habilitado permite que la persona pase 1 vez mas la proxima vez*/
												@pFlag_Generacion_LB	TINYINT = NULL	/*02/11/2020. 1: Si la llamada la esta haciendo para generar Listas Blancas, en esos casos hay validaciones que no hace como Validacion de Turnos*/
AS

/* ============================================================================
   [2026-08-05] REGISTRO DE ERROR / CORRECCION
   El 29/07/2026 12:37 se RECREO este SP y su EXEC a CP_SCA_ValidarProducVta_Kit
   quedo SIN el argumento @pFlag_GrabaRegistro (6o parametro, obligatorio) ->
   error SQL 201 -> el SP abortaba -> TODO acceso habilitado por producto/kit
   fallaba (motivo 207 sin registros del 28/07 al 04/08, ~6 dias), mientras el
   resto de accesos seguia normal; el middleware recibia data:null y no abria.
   FIX: se RESTAURO @pFlag_GrabaRegistro en el EXEC (marcado mas abajo).
   Version original de referencia: dump scripts/geba_xsys_2026.sql (17/07/2026).
   ============================================================================ */

BEGIN

/*Variables Locales*/
DECLARE @Id_Cliente							INT
DECLARE @Id_Cliente_Ref						INT
DECLARE @Id_Cliente_QR_Vencido				INT
DECLARE @Huella								VARBINARY
DECLARE @Tipo_Validacion					CHAR(1)
DECLARE @Activo								TINYINT
DECLARE @Foto								VARCHAR(254)
DECLARE @Foto_Bin							VARBINARY(MAX)
DECLARE @Ult_Foto_Bin						TINYINT
DECLARE @ValidarAcceso						TINYINT
DECLARE @ValidarPersona						TINYINT
DECLARE @ValidoTagsImportados				TINYINT
DECLARE @ValidoTicketEvento					TINYINT		/*07/10/2021(2): Validacion Agregada CP_SCA_ValidoTagsImportados*/
DECLARE @ValidarContratosTiposRechazos		INT
DECLARE @Flag_ValidarMaster					TINYINT
DECLARE @ValidarContratosTipos				INT
DECLARE @ContratoRechazo_Descripcion		VARCHAR(500)
DECLARE @ContratoHabilitado_Descripcion		VARCHAR(500)
DECLARE @ValidarTipo						SMALLINT
DECLARE @Clientes_Tipos_Habil_Descripcion	VARCHAR(5000)
DECLARE @ValidarTipoRechazo					SMALLINT
DECLARE @Clientes_Tipos_Rechazo_Descripcion	VARCHAR(500)
DECLARE @ValidarProdVta						VARCHAR(14)
DECLARE @Producto_Descripcion_Resumida		VARCHAR(100)
DECLARE @ValidarEvento						SMALLINT
DECLARE @Clientes_Tipos_Habil_Evento		VARCHAR(5000)
DECLARE @Flag_Antipassback					CHAR(1)
DECLARE @Flag_ValidarUCP					TINYINT
DECLARE @ValidarVencimientosPersona			VARCHAR(10)
DECLARE @VencimientoPersona_Descripcion		VARCHAR(50)
DECLARE @pId_Acceso							SMALLINT
DECLARE @Flag_Foto_Validar					TINYINT		/*14/03/2019 Validacion Agregada*/
DECLARE @Flag_Turno_Validar					TINYINT		/*10/08/2020 Validacion Agregada*/
DECLARE @Id_CD_Motivo						INT
DECLARE @AccesoPermitidoManualmente			TINYINT		/*05/02/2020 Agregado*/
DECLARE @AccesoPermitidoAcompananteVisita	TINYINT		/*22/11/2024 Agregado*/
DECLARE @Flag_Clientes_Link_Validar			TINYINT		/*28/09/2020 Validacion agregada por QR impresos*/
DECLARE @ValidoCleverQR						INT			/*21/10/2020 Validacion agregada de rechazo por CleverQR*/
DECLARE @Tipo_Cont							CHAR(1)
DECLARE @Flag_Evento						TINYINT
DECLARE @Evento_QR_Formato					VARCHAR(40)
DECLARE @Flag_Horario						TINYINT
DECLARE @Horario							TINYINT
DECLARE @Flag_TipoValidacionUCP				TINYINT     /*15/11/2022 Agregado para el rechazo y habilitacion por UCP*/
DECLARE @Antirebote_Seg						INT			/*31/08/2026 Antirebote de lectura*/
DECLARE @Antirebote_Param					VARCHAR(20)
DECLARE @Antirebote_Obs						VARCHAR(5000)
DECLARE @Antirebote_Tiempo					SMALLINT
DECLARE @Antirebote_Tag						VARCHAR(100)
DECLARE @Antirebote_Id_ES					INT
DECLARE @Antirebote_Id_Cliente				INT
DECLARE @Antirebote_Id_Cliente_Ref			INT
DECLARE @Antirebote_Motivo					INT
DECLARE @Antirebote_Habilitado				TINYINT
DECLARE @Antirebote_Info					VARCHAR(101)
DECLARE @Antirebote_Foto					VARCHAR(254)
/*FIN Variables Locales*/
						

/*  Orden de Validaciones 
  - Verificacion de recepcion de datos de consulta
  - Validacion por Acceso existente
  - Validacion por Tags Importados
  - Validacion por Ticket/Entradas Evento
  - Validacion por CleverQR
  - Validacion por Cliente existente
  - Validacion por Acceso Permitido Manualmente
  - Validacion por Acceso Acompañante/Visita
  - SECCION DE RECHAZOS
  - SECCION DE HABILITACIONES
*/


SELECT 
@Tipo_Cont = ISNULL(Tipo_Cont, '')
FROM CD_Controladores
WHERE
Id_Controlador = @pId_Controlador

SET @pId_Acceso = (dbo.CF_SCA_IdAcceso(@pId_Controlador))


IF(@pFlag_GrabaRegistro IS NULL)
BEGIN
	SET @pFlag_GrabaRegistro = 1
END

IF(@pFlag_Permite_Paso IS NULL)
BEGIN
	SET @pFlag_Permite_Paso = 0
END

IF(@pFlag_Generacion_LB IS NULL)
BEGIN
	SET @pFlag_Generacion_LB = 0
END

/* ---------------------------------------------------------------------------
   [2026-08-31] ANTIREBOTE DE LECTURA
   Los lectores repiten la misma lectura varias veces por cada presentacion:
   en agosto hubo tandas de 8 lecturas identicas en 30 segundos, y cada una
   recorria la cascada completa de rechazos y habilitaciones y grababa su
   propia fila en CD_ES (3.450 lecturas del mes eran repeticion de otra igual
   dentro de 3 segundos).
   Si en los ultimos N segundos ya se contesto EXACTAMENTE la misma lectura
   -mismo tag y mismo controlador- se repite aquella respuesta y se corta.
   La decision no puede haber cambiado: es la misma presentacion fisica, y el
   molinete recibe lo mismo que recibio la primera vez.

   Detalles que importan:
   - La clave incluye el controlador a proposito. La misma credencial en OTRO
     molinete pocos segundos despues no es un rebote sino un paso pendiente, y
     tiene que seguir validandose y quedando registrada.
   - Solo se toman filas con Id_CD_Motivo, que son las que escribio este SP.
     El facial (controlador 68) y los lectores de Aldao Mitre (2 y 3) graban
     por otra via, sin motivo, y no entran aca.
   - No corre cuando no se graba registro (pruebas) ni al generar listas
     blancas: esos llamados tienen que evaluar de verdad.
   - N sale de xParametros('SCA_Antirebote_Seg'). Si no esta cargado, 3
     segundos. Con 0 queda desactivado sin tocar el SP.
   - La busqueda entra por IX_CDES_PorTar (Id_Tarjeta, Fecha): es un seek
     sobre una ventana de segundos, mucho mas barato que la cascada.
--------------------------------------------------------------------------- */
SET @Antirebote_Seg = 3
SET @Antirebote_Param = LTRIM(RTRIM(dbo.CF_xParam('SCA_Antirebote_Seg')))
/* CF_IsNumeric('') devuelve 1 -su bucle no corre sobre una cadena vacia- y
   CONVERT(INT, '') da 0, asi que sin pedir explicitamente que el parametro
   no este vacio el antirebote quedaba apagado cuando no estaba cargado. */
IF(@Antirebote_Param <> '' AND dbo.CF_IsNumeric(@Antirebote_Param) = 1)
BEGIN
	SET @Antirebote_Seg = CONVERT(INT, @Antirebote_Param)
END

SET @Antirebote_Tag = LEFT(ISNULL(@pTag, ''), 100)

IF(@Antirebote_Seg > 0
AND @pFlag_GrabaRegistro = 1
AND @pFlag_Generacion_LB = 0
AND LTRIM(RTRIM(@Antirebote_Tag)) <> '')
BEGIN
	SELECT TOP 1
	@Antirebote_Id_ES		= Id_ES,
	@Antirebote_Id_Cliente	= ISNULL(Id_Cliente, 0),
	@Antirebote_Motivo		= Id_CD_Motivo,
	@Antirebote_Habilitado	= CASE WHEN Resultado = 'S' THEN 1 ELSE 0 END,
	@Antirebote_Obs			= CAST(Observacion AS VARCHAR(5000))
	FROM CD_ES
	WHERE
	Id_Tarjeta = @Antirebote_Tag
	AND Fecha > DATEADD(SECOND, -@Antirebote_Seg, @pFecha)
	AND Fecha <= @pFecha
	AND Id_Controlador = @pId_Controlador
	AND Id_CD_Motivo IS NOT NULL
	ORDER BY Fecha DESC, Id_ES DESC

	IF(@Antirebote_Id_ES IS NOT NULL)
	BEGIN
		SELECT
		@Antirebote_Foto			= ISNULL(Foto, ''),
		@Antirebote_Id_Cliente_Ref	= ISNULL(Id_Cliente_Ref, 0)
		FROM Clientes
		WHERE Id_Cliente = @Antirebote_Id_Cliente

		SET @Antirebote_Info = CASE WHEN @Antirebote_Habilitado = 1 THEN 'OBSER' ELSE 'ERROR' END
		SET @Antirebote_Tiempo = ISNULL((SELECT Tiempo_Mensaje FROM CD_Accesos WHERE Id_Acceso = @pId_Acceso), 1)

		/* Se devuelve la respuesta guardada tal cual, sin pasar por
		   CP_SCA_RegistrarAcceso_Rta: el molinete recibe el mismo texto que la
		   primera vez -incluido el detalle del producto, que Rta arma con
		   @pMensaje_Mostrar y aca no tenemos- y no hay forma de que la
		   repeticion grabe una segunda fila. Las columnas y los tipos son los
		   mismos que arma Rta en su #rTabla. */
		SELECT
		CONVERT(TINYINT, @Antirebote_Habilitado)		AS Flag_Habilitado,
		CONVERT(VARCHAR(5000), @Antirebote_Obs)			AS Mensaje,
		CONVERT(VARCHAR(101), @Antirebote_Info)			AS Info,
		CONVERT(VARCHAR(256), @Antirebote_Foto)			AS Foto,
		CONVERT(VARBINARY(MAX), NULL)					AS Foto_Bin,
		CONVERT(INT, @Antirebote_Id_Cliente)			AS Id_Cliente,
		CONVERT(INT, @Antirebote_Id_Cliente_Ref)		AS Id_Cliente_Ref,
		CONVERT(SMALLINT, @Antirebote_Tiempo)			AS Tiempo_Mensaje
		RETURN
	END
END
/* FIN ANTIREBOTE DE LECTURA */

SET @Huella	= CONVERT(VARBINARY, @pHue)

SET @Tipo_Validacion = dbo.CF_SCA_TipoValidacion(@pTag, @pDoc, @pHue, @pCba, @pDma)


IF(@Tipo_Validacion = 'N')
BEGIN
	SET @Id_CD_Motivo = 101 /*No se enviaron datos de validacion*/
    EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ERROR', '', 0, 0, 0, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
	RETURN
END

/*A partir de aca existe una variable de validacion*/

/* Valido si existe el Acceso */
SET @ValidarAcceso = (dbo.CF_SCA_ValidoAcceso(@pId_Acceso))

IF(@ValidarAcceso = 0)
BEGIN
	SET @Id_CD_Motivo = 102 /*El Acceso no existe*/
	EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ERROR', '', 0, 0, 0, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
	RETURN
END
/* FIN Valido si existe el Acceso */


/*06/10/2021 Se agrega para Extraer Dato Flag_Evento y determinar que validaciones se hacen y cuales no dependiendo si es un evento*/
SELECT
@Flag_Evento = ISNULL(Flag_Evento, 0)
FROM CD_Accesos
WHERE
Id_Acceso = @pId_Acceso
/*FIN 06/10/2021*/

/* 14/11/22 Florencia: Se agrega un nuevo tipo de validacion por UCP.
Tipo 0: No se valida por UCP por Acceso
Tipo 1: Con tener la UCP el socio se ve habilitado (se valida en la seccion de habilitaciones)
Tipo 2: El socio debe tener UCP obligatoriamente y ademas un abono de producto que habilite el acceso (se valida en la seccion de rechazos) */

SELECT @Flag_TipoValidacionUCP = ISNULL(Flag_Ult_Cuota_Paga,0)
								 FROM CD_Accesos
								 WHERE Id_Acceso = @pId_Acceso
/* Fin 14/11/2022*/

/* Valido Tags Importados */
IF(@Flag_Evento IN (1))
BEGIN
	EXEC dbo.CP_SCA_ValidoTagsImportados @pTag, @pId_Acceso, @Flag_TagHabilitado = @ValidoTagsImportados OUTPUT


	IF(ISNULL(@ValidoTagsImportados, 0) = 1)
	BEGIN
		SET @Id_CD_Motivo = 201 /*Ingreso por Ticket Import.*/
		EXEC CP_SCA_RegistrarAcceso_Rta 1, '', 'ADVER', '', 0, 0, 0, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Valido Tags Importados */


/* Valido Ticket/Entradas Evento */
/*07/10/2021(2): Agregado*/
SET @Evento_QR_Formato = dbo.CF_xParam('Evento_QR_Formato')

IF(@Flag_Evento IN (1) AND @Evento_QR_Formato <> 'DNI')
BEGIN
	EXEC dbo.CP_SCA_ValidoTicketEvento @pTag, @pId_Acceso, @Flag_TicketHabilitado = @ValidoTicketEvento OUTPUT


	IF(@ValidoTicketEvento = 1)
	BEGIN
		SET @Id_CD_Motivo = 117 /*Ticket Valido*/
		EXEC CP_SCA_RegistrarAcceso_Rta 1, '', 'OBSER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
	END

	IF(@ValidoTicketEvento = 0)
	BEGIN
		SET @Id_CD_Motivo = 213 /*Ticket Invalido*/
		EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ADVER', '', 0, 0, 0, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
		
	IF(@ValidoTicketEvento = -1)
	BEGIN
		SET @Id_CD_Motivo = 214 /*Ticket ya utilizado*/
		EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ADVER', '', 0, 0, 0, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END

	IF(@ValidoTicketEvento = -2)
	BEGIN
		SET @Id_CD_Motivo = 215 /*Ticket Puerta Incorrecta*/
		EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ADVER', '', 0, 0, 0, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/*FIN 07/10/2021(2): Agregado*/
/* FIN Valido Ticket/Entradas Eventos*/


/* Valido si lo que recibio es un CleverQR */
IF(@Flag_Evento IN (0,1))
BEGIN
	IF(	LEN(@pTag) = 10
	AND dbo.CF_IsNumeric(@pTag) = 1 
	AND SUBSTRING(@pTag,1,1) = '9')
	BEGIN
		SET @pDma = @pTag
	END
	
	IF(@pDma <> '')
	BEGIN
		EXEC dbo.CP_SCA_ValidoCleverQR @pDma, @pId_Acceso, @pFecha, @Flag_QRValido = @ValidoCleverQR OUTPUT
	
		IF(@ValidoCleverQR = 0)
		BEGIN
			SET @Id_CD_Motivo = 114 /*QR Invalido*/
			EXEC CP_SCA_RegistrarAcceso_Rta 1, '', 'ADVER', '', 0, 0, 0, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
			RETURN
		END
	
		IF(@ValidoCleverQR = -1)
		BEGIN
			SET @Id_CD_Motivo = 115 /*QR Vencido*/
			EXEC CP_SCA_RegistrarAcceso_Rta 1, '', 'ERROR', '', 0, 0, 0, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
			RETURN
		END
	
		IF(@ValidoCleverQR = -2)
		BEGIN
			SET @Id_CD_Motivo = 116 /*QR ya utilizado*/
			EXEC CP_SCA_RegistrarAcceso_Rta 1, '', 'ERROR', '', 0, 0, 0, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
			RETURN
		END
	END
END
/* FIN Valido si lo que recibio es un CleverQR */


/* Valido que el cliente exista */
SET @Id_Cliente = (SELECT dbo.CF_SCA_IdentifIdCliente(@pTag))

/* Fin Valido que el cliente exista */

/* Acceso Permitido Manualmente. Agregado 05/02/2020 */
IF(@Flag_Evento IN (0))
BEGIN
	EXEC dbo.CP_SCA_ValidoAccesoPermitidoManualmente @Id_Cliente, @pFecha, @pId_Controlador, @Flag_AccesoPermitidoManualmente = @AccesoPermitidoManualmente OUTPUT


	IF(ISNULL(@AccesoPermitidoManualmente, 0) = 1)
	BEGIN
		SET @Id_CD_Motivo = 211 /*Ingreso por Habilt. Manualmente.*/
		EXEC CP_SCA_RegistrarAcceso_Rta 1, '', 'ADVER', '', 0, @Id_Cliente, 0, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Acceso Permitido Manualmente */

/* Acceso Acompañante/Visita */
IF(@Flag_Evento IN (0))
BEGIN
	EXEC dbo.CP_SCA_ValidoAccesoAcompananteVisita @Id_Cliente, @pFecha, @pId_Controlador, @pId_Acceso, @Flag_TipoValidacionUCP, @Flag_AccesoPermitidoAcompananteVisita = @AccesoPermitidoAcompananteVisita OUTPUT


	IF(ISNULL(@AccesoPermitidoAcompananteVisita, 0) = 1)
	BEGIN
		SET @Id_CD_Motivo = 217 /*Ingreso por Habilt. Acompanante/Visita.*/
		EXEC CP_SCA_RegistrarAcceso_Rta 1, '', 'ADVER', '', 0, @Id_Cliente, 0, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Acceso Acompañante/Visita */


/* Valido Persona */
IF(@Flag_Evento IN (0,1))
BEGIN
	SET @ValidarPersona = (dbo.CF_SCA_ValidarPersona(@Id_Cliente))

	IF(@ValidarPersona = 0)
	BEGIN
		/* [2026-08-31] Si lo que se leyo es un token de QR dinamico que existe
		   pero ya vencio, el rechazo se informa como "QR Vencido" (115) y con el
		   socio identificado, en vez del generico "La Persona es invalida" (103),
		   que mandaba al socio a Socios a averiguar un problema que no tenia.
		   La ventana del token la fija CF_SCA_IdentifIdCliente (5 min). */
		SET @Id_Cliente_QR_Vencido = 0
		IF(LEN(LTRIM(RTRIM(@pTag))) >= 10 AND dbo.CF_IsNumeric(LEFT(LTRIM(RTRIM(@pTag)), 10)) = 1)
		BEGIN
			SET @Id_Cliente_QR_Vencido = ISNULL((SELECT TOP 1 ISNULL(Id_Cliente, 0) FROM Clientes_Links
									WHERE Token = LOWER(LTRIM(RTRIM(LEFT(LTRIM(RTRIM(@pTag)), 10))))
									ORDER BY Fecha_Gen DESC), 0)
		END

		IF(@Id_Cliente_QR_Vencido > 0)
		BEGIN
			SET @Id_CD_Motivo = 115 /*QR Vencido*/
			EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ERROR', '', 0, @Id_Cliente_QR_Vencido, 0, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
			RETURN
		END

		SET @Id_CD_Motivo = 103 /*La Persona es invalida*/
		EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ERROR', '', 0, 0, 0, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Valido Persona */


/* Datos Persona */
SELECT 
@Activo			= ISNULL(Activo, 0),
@Foto			= ISNULL(Foto, ''),
@Id_Cliente_Ref	= ISNULL(Id_Cliente_Ref, 0)
FROM Clientes 
WHERE 
Id_Cliente = @Id_Cliente


SET @Ult_Foto_Bin = ISNULL((SELECT MIN(Nro) FROM Clientes_Fotos WHERE Id_Cliente = @Id_Cliente), 0) ---siempre tomamos la 1

IF(@Ult_Foto_Bin <> 0)
BEGIN
	IF(dbo.CF_xParam('SCA_RtaCtrl_Foto') = 1 AND @Tipo_Cont <> 'K'/*Molinete Intelektron*/)
	BEGIN
		SET @Foto_Bin = (SELECT Foto FROM Clientes_Fotos CF WHERE Id_Cliente = @Id_Cliente AND Nro = @Ult_Foto_Bin)		
	END
		
END
/* FIN Datos Persona */

/* Valido si existen Productos con Horarios */
SET @Flag_Horario = ISNULL((SELECT MAX(1) FROM CD_Accesos_Horarios WHERE Id_Horario>0 AND Id_Acceso=@pId_Acceso),0)

	IF (@Flag_Horario=1)
		SET @Horario=0
	ELSE
		SET @Horario=0
/* Fin si existen Productos con Horarios */

/********************************************************************************
*********************************************************************************
**********************************************************************************/

/* COMIENZO SECCION DE RECHAZOS. Orden de rechazos:

1) Rechaza por Persona Inactiva
2) Rechaza por Vencimientos: CF_SCA_ValidarVencimientosPersona
3) Rechaza por Contratos Tipos de Rechazo: CF_SCA_ValidarContratosTiposRechazos
4) Rechaza por Tipo de Categoria de Rechazo: CF_SCA_ValidarTipoRechazos
5) Rechaza por AntiPassBack: CF_SCA_Antipassback
6) Rechaza por Foto Grabada: CP_SCA_ValidarFoto
7) Rechaza por Turno: CP_SCA_ValidarTurno
8) Rechaza por UCP: CF_SCA_ValidarUltCuotaPaga

*/

/* Rechaza por Persona Inactiva */
IF(@Flag_Evento IN (0,1))
BEGIN
	IF(@Activo = 0 AND NOT EXISTS (SELECT 1 FROM CD_Clientes_Baja_Revision R
	                               WHERE R.Id_Cliente = @Id_Cliente AND ISNULL(R.Activo, 1) = 1))
	/* 31/08/2026 - excepcion por bajas en revision.
	   El 28/08/2026, entre las 15:41 y las 15:46, un proceso externo marco 1.259
	   socios como fallecidos (Id_Usuario 0 en Clientes_Hist, sin rastro en
	   Seg_Usuarios_Audit: no paso por la aplicacion). 1.164 de ellos estaban de
	   alta. Hasta que la oficina de Socios confirme caso por caso, a los de esa
	   tanda no se los frena aca: siguen la cascada y entran por su categoria,
	   como venian entrando. Revisado un socio, se le pone Activo = 0 en
	   CD_Clientes_Baja_Revision y vuelve a regir la regla normal. */
	BEGIN
		SET @Id_CD_Motivo = 104 /*La Persona esta desactivada*/
		EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ADVER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Rechaza por Persona Inactiva */


/* Rechaza por Vencimientos */
IF(@Flag_Evento IN (0))
BEGIN
	SET @ValidarVencimientosPersona = (dbo.CF_SCA_ValidarVencimientosPersona(@Id_Cliente, @pId_Acceso, @pFecha))

	IF(@ValidarVencimientosPersona <> '')
	BEGIN
		SET @Id_CD_Motivo = 105 /*Rechazo por Tipo de Venc.*/
		SET @VencimientoPersona_Descripcion = (SELECT SUBSTRING(RTRIM(LTRIM(Descripcion)), 1, 16) FROM Clientes_Venc_Tipos WHERE Id_Tipo_Venc = @ValidarVencimientosPersona) + ' vencido el: ' + (SELECT CONVERT(VARCHAR(10), CV.Fecha, 105) FROM Clientes_Venc CV WHERE CV.Id_Cliente = @Id_Cliente AND CV.Id_Tipo_Venc = @ValidarVencimientosPersona )
	
		EXEC CP_SCA_RegistrarAcceso_Rta 0, @VencimientoPersona_Descripcion, 'ADVER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Rechaza por Vencimientos */


/* Rechaza por Contratos Tipos de Rechazo */
IF(@Flag_Evento IN (0,1))
BEGIN
	SET @ValidarContratosTiposRechazos = (dbo.CF_SCA_ValidarContratosTiposRechazos(@Id_Cliente, @pId_Acceso, @pFecha))

	IF(@ValidarContratosTiposRechazos <> 0)
	BEGIN
		SET @Id_CD_Motivo = 106 /*Rechazo por Tipo de Contrato*/
		SET @ContratoRechazo_Descripcion = (SELECT RTRIM(LTRIM(CT.Descripcion)) FROM Contratos_Tipos CT, Contratos CO WHERE CT.Id_Tipo_Con = CO.Id_Tipo_Con AND Id_Contrato = @ValidarContratosTiposRechazos)

		EXEC CP_SCA_RegistrarAcceso_Rta 0, @ContratoRechazo_Descripcion, 'ADVER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Rechaza por Contratos Tipos de Rechazo */


/* Rechaza por Tipo de Categoria de Rechazo */
IF(@Flag_Evento IN (0))
BEGIN
	SET @ValidarTipoRechazo = (dbo.CF_SCA_ValidarTipoRechazos(@Id_Cliente, @pId_Acceso, @pFecha))

	IF(@ValidarTipoRechazo <> 0)
	BEGIN
		SET @Id_CD_Motivo = 107 /*Rechazo por Categoria*/
	
		SET @Clientes_Tipos_Rechazo_Descripcion = (SELECT RTRIM(LTRIM(CT.Descripcion)) FROM Clientes_Tipos CT WHERE Id_Tipo_Cli = @ValidarTipo)
	
		EXEC CP_SCA_RegistrarAcceso_Rta 0, @Clientes_Tipos_Rechazo_Descripcion, 'OBSER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Rechaza por Tipo de Categoria de Rechazo */

/* Rechaza por AntiPassBack */
IF(@Flag_Evento IN (0,1))
BEGIN
	SET @Flag_Antipassback = (dbo.CF_SCA_Antipassback(@Id_Cliente, @pId_Acceso, @pFecha, @pTipoAcceso, @pFechaEvento))
	IF(@Flag_Antipassback <> 'O')
	BEGIN
		IF(@Flag_Antipassback = 'Y')
		BEGIN
			SET @Id_CD_Motivo = 108 /*Ya ingresado. Antipassback*/
			EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ADVER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		END
	
		IF(@Flag_Antipassback = 'E')
		BEGIN
			SET @Id_CD_Motivo = 109 /*No Registro Salida. Antipassback*/
			EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ADVER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		END
	
		IF(@Flag_Antipassback = 'S')
		BEGIN
			SET @Id_CD_Motivo = 110 /*No Registro Entrada. Antipassback*/
			EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ADVER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		END
		RETURN
	END
END
/* FIN Rechaza por AntiPassBack */


/* Rechaza por Foto Grabada */
IF(@Flag_Evento IN (0,1))
BEGIN
	EXEC dbo.CP_SCA_ValidarFoto @Id_Cliente, @pId_Acceso, @pFecha, @pTipoAcceso, @pFechaEvento, @Flag_ValidarFoto_Rta = @Flag_Foto_Validar OUTPUT


	IF(@Flag_Foto_Validar <> 1)
	BEGIN
		SET @Id_CD_Motivo = 111 /*No posee foto*/
		EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ADVER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Rechaza por Foto Grabada */


/* Rechaza por Turno (10/08/2020) */
IF(@Flag_Evento IN (0))
BEGIN
	IF(@pFlag_Generacion_LB = 0) --02/11/2020 Solo valida turno si no esta generando una lista blanca (Simulacion de entrada)
	BEGIN
		IF(@pTipoAcceso = 'E') /*Solo se valida Turno si es Entrada*/
		BEGIN
			EXEC dbo.CP_SCA_ValidarTurno @Id_Cliente, @pId_Acceso, @pFecha, @pTipoAcceso, @pFechaEvento, @Flag_ValidarTurno_Rta = @Flag_Turno_Validar OUTPUT


			IF(@Flag_Turno_Validar <> 1)
			BEGIN
				SET @Id_CD_Motivo = 113 /*No posee un Turno*/
				EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ADVER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
				RETURN
			END
		END
	END
END
/* FIN Rechaza por Turno */

/* Rechaza por UCP */
/* Agregado 15/11/22 */
IF(@Flag_Evento IN (0) AND @Flag_TipoValidacionUCP = 2) /* 15/11/2022: Se agrega validacion por Flag_TipoValidacionUCP */
BEGIN
	SET @Flag_ValidarUCP = (dbo.CF_SCA_ValidarUltCuotaPaga(@Id_Cliente, @pId_Acceso, @pFecha))
	IF(@Flag_ValidarUCP = 0)
	BEGIN
	
		SET @Id_CD_Motivo = 300 /*La Persona no posee UCP al dia*/
	
		EXEC CP_SCA_RegistrarAcceso_Rta 0,  '', 'ADVER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Rechaza por UCP */

/* FIN DE SECCION RECHAZOS */

/********************************************************************************
*********************************************************************************
**********************************************************************************/

/* COMIENZO SECCION DE HABILITACIONES. Orden de habilitaciones:

1) Habilita por Acceso Master: CF_SCA_ValidarMaster
2) Habilita por Clientes Links: CP_SCA_ValidarClientes_Links
3) Habilita por UCP: CF_SCA_ValidarUltCuotaPaga
4) Habilita por Contratos Tipos: CF_SCA_ValidarContratosTipos
5) Habilita por Tipo de Categoria de Cliente: CF_SCA_ValidarTipo
6) Habilita por Kit de Productos Comprado: CP_SCA_ValidarProducVta_Kit
7) Habilita por Producto de Comprado: CP_SCA_ValidarProducVta
8) Habilita por Producto de Comprado por Titular: CP_SCA_ValidarProducVta
9) Habilita por Evento: CF_SCA_ValidarEvento
10) Habilita por Tipo de Cliente y por Horario: CF_SCA_ValidarCategHorario
11) Habilita por Producto Comprado y por Horario: CP_SCA_ValidarProducVta

*/

/* Habilita por Acceso Master */
IF(@Flag_Evento IN (0,1))
BEGIN
	SET @Flag_ValidarMaster = (dbo.CF_SCA_ValidarMaster(@Id_Cliente))

	IF(@Flag_ValidarMaster = 1)
	BEGIN
		SET @Id_CD_Motivo = 202 /*Acceso Master*/
	
		EXEC CP_SCA_RegistrarAcceso_Rta 1, '', 'OBSER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Habilita por Acceso Master */

/* Habilita por Clientes_Links (28/09/2020)*/
IF(@Flag_Evento IN (0))
BEGIN
	EXEC dbo.CP_SCA_ValidarClientes_Links @Id_Cliente, @pId_Acceso, @pFecha, @pTipoAcceso, @pFechaEvento, @Flag_ValidarClientes_Link_Rta = @Flag_Clientes_Link_Validar OUTPUT


	IF(@Flag_Clientes_Link_Validar = 1)
	BEGIN
		SET @Id_CD_Motivo = 212 /*Habilit. por QR*/
		EXEC CP_SCA_RegistrarAcceso_Rta 1, '', 'OBSER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Habilita por Clientes_Links */


/* Habilita por UCP */
IF(@Flag_Evento IN (0,1) AND @Flag_TipoValidacionUCP = 1) /* 15/11/2022: Se agrega validacion por Flag_TipoValidacionUCP */
BEGIN
	SET @Flag_ValidarUCP = (dbo.CF_SCA_ValidarUltCuotaPaga(@Id_Cliente, @pId_Acceso, @pFecha))
	IF(@Flag_ValidarUCP = 1)
	BEGIN
	
		SET @Id_CD_Motivo = 203 /*La Persona con UCP al dia*/
	
		EXEC CP_SCA_RegistrarAcceso_Rta 1,  '', 'OBSER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Habilita por UCP */


/* Habilita por Contratos Tipos */
IF(@Flag_Evento IN (0,1))
BEGIN
	SET @ValidarContratosTipos = (dbo.CF_SCA_ValidarContratosTipos(@Id_Cliente, @pId_Acceso, @pFecha))

	IF(@ValidarContratosTipos <> 0)
	BEGIN
		SET @Id_CD_Motivo = 204 /*Habilit. por Tipo de Contrato*/
	
		SET @ContratoHabilitado_Descripcion = (SELECT RTRIM(LTRIM(CT.Descripcion)) FROM Contratos_Tipos CT, Contratos CO WHERE CT.Id_Tipo_Con = CO.Id_Tipo_Con AND Id_Contrato = @ValidarContratosTipos)

		EXEC CP_SCA_RegistrarAcceso_Rta 1,  @ContratoHabilitado_Descripcion, 'OBSER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Habilita por Contratos Tipos */


/* Habilita por Tipo de Categoria de Cliente */
IF(@Flag_Evento IN (0,1))
BEGIN
	SET @ValidarTipo = (dbo.CF_SCA_ValidarTipo(@Id_Cliente, @pId_Acceso, @pFecha))

	IF(@ValidarTipo <> 0)
	BEGIN
		SET @Id_CD_Motivo = 205 /*Habilit. por Tipo de Cliente*/
	
		SET @Clientes_Tipos_Habil_Descripcion = (SELECT RTRIM(LTRIM(CT.Descripcion)) FROM Clientes_Tipos CT WHERE Id_Tipo_Cli = @ValidarTipo)

		EXEC CP_SCA_RegistrarAcceso_Rta 1, @Clientes_Tipos_Habil_Descripcion, 'OBSER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Habilita por Tipo de Categoria de Cliente */

/* Habilita por Produc. Pack Comprado */
IF(@Flag_Evento IN (0))
BEGIN
	EXEC dbo.[CP_SCA_ValidarProducVta_Kit] @Id_Cliente, @pId_Acceso, @pFecha,0,@Horario, @pFlag_GrabaRegistro, @Id_Producto = @ValidarProdVta  OUTPUT   /* [2026-08-05] @pFlag_GrabaRegistro RESTAURADO (lo quito la recreacion del 29/07) */


	IF(@ValidarProdVta <> '')
	BEGIN
		SET @Id_CD_Motivo = 301 /*Habilit. por Produc. Pack Comprado*/
	
		SET @Producto_Descripcion_Resumida = (SELECT ISNULL(Descripcion_Resumida, Id_Producto) FROM Productos WHERE Id_Producto = @ValidarProdVta)
	
		EXEC CP_SCA_RegistrarAcceso_Rta 1, @Producto_Descripcion_Resumida, 'OBSER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Habilita por Produc. Pack Comprado */

/* Habilita por Producto de Comprado */
IF(@Flag_Evento IN (0))
BEGIN
	EXEC dbo.[CP_SCA_ValidarProducVta] @Id_Cliente, @pId_Acceso, @pFecha,0,@Horario, @Id_Producto = @ValidarProdVta  OUTPUT


	IF(@ValidarProdVta <> '')
	BEGIN
		SET @Id_CD_Motivo = 206 /*Habilit. por Produc. Comprado*/
	
		SET @Producto_Descripcion_Resumida = (SELECT ISNULL(Descripcion_Resumida, Id_Producto) FROM Productos WHERE Id_Producto = @ValidarProdVta)
	
		EXEC CP_SCA_RegistrarAcceso_Rta 1, @Producto_Descripcion_Resumida, 'OBSER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Habilita por Producto de Comprado */


/* Habilita por Producto de Comprado por Titular */
IF(@Flag_Evento IN (0))
BEGIN
	IF (@Id_Cliente_Ref<>0)
	BEGIN
		EXEC dbo.[CP_SCA_ValidarProducVta] @Id_Cliente, @pId_Acceso, @pFecha,@Id_Cliente_Ref,@Horario, @Id_Producto = @ValidarProdVta  OUTPUT

		SET @Id_CD_Motivo = 207 /*Habilit. por Produc. Comprado por Titular*/
	

		IF(@ValidarProdVta <> '')
		BEGIN
			SET @Producto_Descripcion_Resumida = (SELECT ISNULL(Descripcion_Resumida, Id_Producto) FROM Productos WHERE Id_Producto = @ValidarProdVta)
	
			EXEC CP_SCA_RegistrarAcceso_Rta 1, @Producto_Descripcion_Resumida, 'OBSER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
			RETURN
		END
	END
END
/* FIN Habilita por Producto de Comprado por Titular */


/* Habilita por Evento */
IF(@Flag_Evento IN (1))
BEGIN
	--Agregado 29/06/2018
	SET @ValidarEvento = (dbo.CF_SCA_ValidarEvento(@Id_Cliente, @pId_Acceso, @pFecha))

	IF(@ValidarEvento <> 0)
	BEGIN
		SET @Id_CD_Motivo = 208 /*Habilit. por Evento*/
	
		SET @Clientes_Tipos_Habil_Evento = (SELECT RTRIM(LTRIM(Descripcion)) FROM Prod_Listas_Precios WHERE Id_Lista_Precio = @ValidarEvento)

		EXEC CP_SCA_RegistrarAcceso_Rta 1, @Clientes_Tipos_Habil_Evento, 'OBSER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Habilita por Evento */


/* Habilita por Tipo de Cliente y por Horario */
IF(@Flag_Evento IN (0))
BEGIN
	SET @ValidarTipo = (dbo.CF_SCA_ValidarCategHorario(@Id_Cliente, @pId_Acceso, @pFecha))

	IF(@ValidarTipo <> 0)
	BEGIN
		SET @Id_CD_Motivo = 209 /*Habilit. por Categoria por horario*/
	
		SET @Clientes_Tipos_Habil_Descripcion = (SELECT RTRIM(LTRIM(CT.Descripcion)) FROM Clientes_Tipos CT WHERE Id_Tipo_Cli = @ValidarTipo)

		EXEC CP_SCA_RegistrarAcceso_Rta 1, @Clientes_Tipos_Habil_Descripcion, 'OBSER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Habilita por Tipo de Cliente y por Horario */


/* Habilita por Producto Comprado y por Horario */
IF(@Flag_Evento IN (0))
BEGIN
	EXEC dbo.[CP_SCA_ValidarProducVta] @Id_Cliente, @pId_Acceso, @pFecha,0,1, @Id_Producto = @ValidarProdVta  OUTPUT


	IF(@ValidarProdVta <> '')
	BEGIN
		SET @Id_CD_Motivo = 210 /*Habilit. por Produc. Comprado por horario*/
	
		SET @Producto_Descripcion_Resumida = (SELECT ISNULL(Descripcion_Resumida, Id_Producto) FROM Productos WHERE Id_Producto = @ValidarProdVta)
	
		EXEC CP_SCA_RegistrarAcceso_Rta 1, @Producto_Descripcion_Resumida, 'OBSER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso
		RETURN
	END
END
/* FIN Habilita por Producto Comprado y por Horario */


/* Rechazo si no cumple con ninguna de las habilitaciones previas */

SET @Id_CD_Motivo = 112 /*No cumple ninguna condicion habilitante*/
EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ADVER', @Foto, @Foto_Bin, @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha, @pId_Controlador, @pId_Acceso, @Id_CD_Motivo, @pFlag_GrabaRegistro, @pFlag_Permite_Paso

RETURN

END

/*
EXEC dbo.CP_SCA_RegistrarAcceso	1,							--@pId_Controlador: Id_ Molinete
								'S',						--@pTipoAcceso: E: Entrada / S:Salida
								'2020-08-18T12:16:00',		--@pFecha: Fecha a comparar
								'19000101',					--@pFechaEvento: Fecha del Evento a comparar. Si no es un evento ingresar 19000101
								'0025545736',				--@pTag: Credencial MiFare / Wiegand
								0,							--@pDoc: Documento
								'',							--@pHue: Huella Dactilar
								'',							--@pCba: Codigo de Barra
								'',							--@pDma: Data Matrix
								1,							--@pFlag_GrabaRegistro(NULL): 23/09/2019. 1(default): Graba Registro. 0: No graba el registro
								0,							--@pFlag_Permite_Paso(NULL):  05/02/2020. 1: Si el resultado es habilitado permite que la persona pase 1 vez mas la proxima vez
								0							--@pFlag_Generacion_LB(NULL): 02/11/2020. 1: Si la llamada la esta haciendo para generar Listas Blancas, en esos casos hay validaciones que no hace como Validacion de Turnos

*/
GO

/* ---------- CF_SCA_IdentifIdCliente ---------- */
GO
CREATE FUNCTION [dbo].[CF_SCA_IdentifIdCliente](@pDato VARCHAR(100))
RETURNS INT
AS
BEGIN
DECLARE @Id_Cliente					INT
DECLARE @Flag_Tipo_DNI				TINYINT
DECLARE @Flag_Formato_Valido_Doc	INT
DECLARE @Doc_Escaneado				VARCHAR(20)
DECLARE @Doc_Nro					BIGINT
DECLARE @Long_Dato					INT
DECLARE @Flag_Es_Numerico			TINYINT
DECLARE @DatoOriginal				VARCHAR(100)

IF EXISTS ( SELECT * FROM sysobjects WHERE id = object_id(N'dbo.CF_SCA_IdentifIdCliente_') AND type = 'FN')
BEGIN
	SET @Id_Cliente = (dbo.CF_SCA_IdentifIdCliente_(@pDato))
	RETURN ISNULL(@Id_Cliente, 0)
END

IF(ISNULL(@pDato, '') = '')
BEGIN
	SET @Id_Cliente = 0
	RETURN @Id_Cliente
END

SET @Long_Dato			= LEN(@pDato)
SET @Flag_Es_Numerico	= dbo.CF_IsNumeric(@pDato)
SET @DatoOriginal		= @pDato

IF(@Long_Dato > 60)
BEGIN
	/*
	08-08-2022: Se deja los mayores de 60 digitos que se consideren como DNI Leidos ya que los QR ahora son de 10 caracteres numericos
	
	/*QR*/
	SET @Id_Cliente = ISNULL((SELECT ISNULL(Id_Cliente, 0) FROM Clientes_Links WHERE LOWER(LTRIM(RTRIM(URL)))= LOWER(LTRIM(RTRIM(@pDato)))), 0)
	RETURN @Id_Cliente
	*/
	IF(dbo.CF_xParam('QR_Dinamico') = 1)
	BEGIN
		SET @pDato = (SELECT SUBSTRING(@pDato,1,10))
		SET @Id_Cliente = ISNULL((SELECT ISNULL(Id_Cliente, 0) FROM Clientes_Links 
									WHERE Token = LOWER(LTRIM(RTRIM(@pDato))) AND FECHA_GEN > DATEADD(MINUTE, -5, GETDATE())),0)
		IF(@Id_Cliente = 0)
		BEGIN
			SET @Id_Cliente = ISNULL((SELECT dbo.CF_IdentifDNI(@DatoOriginal)),0)
			IF(@Id_Cliente = 0)
			BEGIN
				SET @Id_Cliente = ISNULL((SELECT MAX(Id_Cliente) FROM Clientes WHERE Credencial_Nro = UPPER(LTRIM(RTRIM(@DatoOriginal)))), 0)
			END
		END		
	END
	ELSE
	BEGIN
		SET @Id_Cliente = ISNULL((SELECT dbo.CF_IdentifDNI(@pDato)),0)
		IF(@Id_Cliente = 0)
		BEGIN
			SET @Id_Cliente = ISNULL((SELECT MAX(Id_Cliente) FROM Clientes WHERE Credencial_Nro = UPPER(LTRIM(RTRIM(@pDato)))), 0)
		END
	END
	
	RETURN @Id_Cliente
END

IF(@Flag_Es_Numerico = 0)
BEGIN
	/*Credencial MiFare*/
	SET @Id_Cliente = ISNULL((SELECT MAX(Id_Cliente) FROM Clientes WHERE Credencial_Nro = UPPER(LTRIM(RTRIM(@pDato)))), 0)
	IF(@Id_Cliente = 0)
	BEGIN
		IF(dbo.CF_xParam('QR_Dinamico') = 1)
		BEGIN
			SET @pDato = (SELECT SUBSTRING(@pDato,1,10))
			SET @Id_Cliente = ISNULL((SELECT ISNULL(Id_Cliente, 0) FROM Clientes_Links 
										WHERE Token = LOWER(LTRIM(RTRIM(@pDato))) AND FECHA_GEN > DATEADD(MINUTE, -5, GETDATE())),0)
		END
		ELSE
		BEGIN
			SET @Id_Cliente = ISNULL((SELECT dbo.CF_IdentifDNI(@pDato)),0)
			IF(@Id_Cliente = 0)
			BEGIN
				SET @Id_Cliente = ISNULL((SELECT MAX(Id_Cliente) FROM Clientes WHERE Credencial_Nro = UPPER(LTRIM(RTRIM(@pDato)))), 0)
			END
		END
	END
	RETURN @Id_Cliente
END

IF(@Flag_Es_Numerico = 1)
BEGIN
	/*DNI*/
	IF(CONVERT(BIGINT, @pDato) < 3000000000)
	BEGIN
		SET @Id_Cliente = ISNULL((SELECT TOP 1 ISNULL(Id_Cliente, 0) 
												FROM Clientes C
													JOIN Clientes_Tipos T ON C.Id_Tipo_Cli = T.Id_Tipo_Cli
												WHERE Doc_Nro = CONVERT(BIGINT, @pDato)  
												ORDER BY 
														 C.Activo DESC,
													  CASE T.Flag_Tipo
														WHEN 'P' THEN 1
														WHEN 'S' THEN 2
														WHEN ''  THEN 3
														WHEN 'N' THEN 4
														ELSE 5
													  END,
														 C.Ult_Cuota_Paga DESC), 0)
		IF(@Id_Cliente = 0)
		BEGIN
			SET @Id_Cliente = ISNULL((SELECT MAX(Id_Cliente) FROM Clientes WHERE Credencial_Nro = UPPER(LTRIM(RTRIM(@pDato)))), 0)
		END

		RETURN @Id_Cliente
	END
	ELSE
	BEGIN
		/*QR Intelektron Numerico de 10 caracteres que empieza con 9*/
		IF(dbo.CF_xParam('QR_Dinamico') = 1)
		BEGIN
			SET @pDato = (SELECT SUBSTRING(@pDato,1,10))
			SET @Id_Cliente = ISNULL((SELECT ISNULL(Id_Cliente, 0) FROM Clientes_Links 
										WHERE Token = LOWER(LTRIM(RTRIM(@pDato))) AND FECHA_GEN > DATEADD(MINUTE, -5, GETDATE())),0)
			IF(@Id_Cliente = 0)
			BEGIN
				SET @Id_Cliente = ISNULL((SELECT dbo.CF_IdentifDNI(@DatoOriginal)),0)
				IF(@Id_Cliente = 0)
				BEGIN
					SET @Id_Cliente = ISNULL((SELECT MAX(Id_Cliente) FROM Clientes WHERE Credencial_Nro = UPPER(LTRIM(RTRIM(@DatoOriginal)))), 0)
				END
			END				
		END
		ELSE
		BEGIN
			SET @Id_Cliente = ISNULL((SELECT ISNULL(Id_Cliente, 0) FROM Clientes_Links WHERE Token = LOWER(LTRIM(RTRIM(@pDato)))), 0)
		END

		RETURN @Id_Cliente
	END
END

RETURN ISNULL(@Id_Cliente, 0)
END

--POR NRO DOC			SELECT dbo.CF_SCA_IdentifIdCliente('33554240')				RTA: 879212
--POR QR				SELECT dbo.CF_SCA_IdentifIdCliente('3475627707')			RTA: 879212
--POR DNI NUEVO			SELECT dbo.CF_SCA_IdentifIdCliente('00586082921"PAZ WERNER"FEDERICO"M"33554240"B"05-03-1988"11-03-2019"202')		RTA: 879212
--POR DNI VIEJO			SELECT dbo.CF_SCA_IdentifIdCliente('"33554240    "A"1"PAZ WERNER"FEDERICO"ARGENTINA"05-03-1988"M"18-06-2010"00013006433"7000 "18-06-2025"323"0"ILR╬ô├Â┬úÔö£┬¬01.2 C╬ô├Â┬úÔö£┬¬100614.02"UNIDAD ╬ô├Â┬╝Ôö£├ç05 ╬ô├Â┬úÔö£┬║╬ô├Â┬úÔö£┬║ S-N╬ô├Â┬úÔö£┬¬ 0040:2008::0005')   RTA: 879212
GO

/* ---------- CD_Motivos 115 (QR Vencido) ---------- */
UPDATE CD_Motivos
   SET Descripcion_Display  = N'QR VENCIDO - GENERE OTRO',
       Descripcion_Pantalla = N'QR vencido: generá uno nuevo en la app y pasalo enseguida'
 WHERE Id_CD_Motivo = 115
GO

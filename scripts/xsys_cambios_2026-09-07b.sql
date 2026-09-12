/*======================================================================================
  xSys - CORRECCIÓN de SP_Cbtes_Items_Integrantes
  Fecha: 2026-09-07

  Problema (diagnóstico completo en scripts/xsys_facturacion_integrantes_2026-09-07.sql):
  el 2026-08-05 se agregó a este SP la sección 3.2, con dos RETURN que sacan de
  facturación al titular cuando tiene contrato de cuota voluntaria (tipo 37) o es
  Vitalicio +71 (Id_Tipo_Cli 1010). Pero el RETURN corta el procedimiento entero y las
  líneas de los INTEGRANTES del grupo familiar se generan más abajo (pasos 7 a 9), así
  que el grupo completo se queda sin cuota. En el lote de octubre 2026 dejó a 34 socios
  sin facturar por 3.464.940,00.

  Corrección: las dos condiciones pasan a marcar una variable @Excluir_Titular en vez de
  cortar. Esa variable se usa en los dos únicos lugares donde el SP toca el ítem del
  titular (paso 6 rama "sin integrantes", y paso 10). Los integrantes se siguen
  generando. La intención del cambio del 05/08 -- no pisarle el precio al titular -- se
  respeta exactamente.

  Backup previo: zSP_Cbtes_Items_Integrantes_20260907

  Ejecución: script de Python contra xSys, dentro de una transacción explícita, con
  verificación antes del COMMIT. El bloque entre los marcadores ALTER INICIO / ALTER FIN
  es literalmente lo que se ejecutó.
======================================================================================*/


-- ------------------------------------------------------------------------------------
-- 1) BACKUP (se ejecuta antes del ALTER, en la misma transacción)
-- ------------------------------------------------------------------------------------
--     Se toma OBJECT_DEFINITION('dbo.SP_Cbtes_Items_Integrantes') y se crea una copia
--     idéntica renombrada a zSP_Cbtes_Items_Integrantes_20260907.
--     Guardas: aborta si el SP no existe, si el backup ya existe, o si la definición
--     actual no es la que se analizó (se compara que contenga los dos RETURN).


-- ------------------------------------------------------------------------------------
-- 2) EL CAMBIO
-- ------------------------------------------------------------------------------------
-- >>> ALTER INICIO
ALTER PROCEDURE [dbo].[SP_Cbtes_Items_Integrantes]
    @trans INT,
    @Item SMALLINT,
    @Prod VARCHAR(14)
AS
BEGIN
    /********************************************************************
     SP_Cbtes_Items_Integrantes
     Autor: Gemini AI (ajuste técnico) / Reordenado por Maca
     Fecha: 2025-10-22
     Descripción:
       Calcula y distribuye el importe del grupo familiar:
       - El titular nunca queda negativo.
       - Redondeo a 2 decimales.
       - La suma de integrantes + titular = importe total.
       - Evita cursores, utilizando tabla temporal.

     2026-09-07: las exclusiones de Cuota Voluntaria (contrato tipo 37) y Vitalicio +71
     (Id_Tipo_Cli 1010) agregadas el 2026-08-05 cortaban el SP con RETURN y dejaban sin
     cuota a TODO el grupo familiar, no sólo al titular. Ahora marcan @Excluir_Titular y
     sólo se saltea la actualización del ítem del titular.
    ********************************************************************/

    SET NOCOUNT ON;

    DECLARE
        @Id_Familia INT,
        @Id_Cliente INT,
        @Id_Cliente_Ref INT,
        @Id_Cond_Vta CHAR(10),
        @Imp_Final DECIMAL(15,5),
        @Id_Estado_Cbte SMALLINT,
        @Fecha_Venc DATETIME,
        @Lote VARCHAR(20),
        @Bonif DECIMAL(5,2),
        @Descripcion_Producto VARCHAR(100),
        @Flag_QA CHAR(1),
        @Fecha_QA DATETIME,
        @Cant_Integrantes SMALLINT,
        @Precio_CS_Titular DECIMAL(15,5),
        @item_base		SMALLINT,
		@ModoDebug		SMALLINT,
  		@Cond_Pago		SMALLINT,
		@Id_Usuario		SMALLINT,
		@Porc_Iva		DECIMAL(5,2),
		@Imp_Iva		DECIMAL(15,2),
		@Cantidad_saldo	DECIMAL(15,5),
		@Cantidad		DECIMAL (15,5),
		@Excluir_Titular BIT
SET @ModoDebug = 0
SET @Excluir_Titular = 0

    IF @ModoDebug = 1
	BEGIN
			PRINT '---------------------------------------------------------------'
			PRINT 'Inicio SP_Cbtes_Items_Integrantes | Trans=' + CONVERT(VARCHAR(10),@trans)
	END
    -- 1. Verifica si el comprobante compromete factura
    IF NOT EXISTS(
        SELECT 1
        FROM Cbtes_Tipos T
        JOIN Cbtes C ON C.Id_Tipo_Cbte = T.Id_Tipo_Cbte
        WHERE C.Id_Trans = @trans
          AND T.Compromete_Factura IN (1,-1)
    )
    BEGIN
        IF @ModoDebug = 1
		BEGIN
			PRINT 'Tipo de comprobante NO compromete factura. Sale del SP.'
        END
		RETURN
    END

    -- 2. Obtiene cliente titular del comprobante
    SELECT @Id_Cliente = Id_Cliente
    FROM Cbtes
    WHERE Id_Trans = @trans

    IF @Id_Cliente IS NULL OR @Id_Cliente IN (0,1,1105)
    BEGIN
		IF @ModoDebug = 1
		BEGIN
			PRINT 'Id_Cliente no válido o referente. Sale del SP.'
        END
		RETURN
    END

    -- 3. Valida que sea titular (no integrante)
    SELECT @Id_Cliente_Ref = Id_Cliente_Ref,
           @Id_Cond_Vta = Id_Cond_Vta
    FROM Clientes
    WHERE Id_Cliente = @Id_Cliente

    IF @Id_Cliente_Ref <> 0
    BEGIN
        IF @ModoDebug = 1
		BEGIN
			PRINT 'Cliente es integrante. Sale del SP.'
        END
		RETURN
    END

    IF (LTRIM(RTRIM(@Id_Cond_Vta)) = 'GFCP')
    BEGIN
        IF @ModoDebug = 1
		BEGIN
			PRINT 'Condición de venta GFCP (cobro separado). Sale del SP.'
        END
		RETURN
    END

	-- 3.1 Traigo la fecha del item para validar vigencia de contrato
    SELECT @Fecha_QA = Fecha_QA
    FROM Cbtes_Items
    WHERE Id_Trans = @trans AND Item = @Item AND Id_Cliente = @Id_Cliente

    -- 3.2 El TITULAR no paga cuota si tiene Cuota Voluntaria (contrato tipo 37) o si es
    --     Vitalicio +71 (Id_Tipo_Cli 1010). Los INTEGRANTES sí se siguen facturando:
    --     por eso se marca la variable en vez de cortar el procedimiento.
    IF EXISTS (
        SELECT 1 FROM Contratos
        WHERE Id_Cliente = @Id_Cliente
          AND Id_Tipo_Con = 37
          AND Activo = 1
          AND Fecha_Desde <= @Fecha_QA
          AND (Fecha_Hasta >= @Fecha_QA OR Fecha_Hasta IS NULL)
    )
    BEGIN
        IF @ModoDebug = 1
        BEGIN
            PRINT 'Titular con Contrato Cuota Voluntaria (Tipo 37). No se le toca el ítem.'
        END
        SET @Excluir_Titular = 1
    END

    IF EXISTS (
        SELECT 1 FROM Clientes
        WHERE Id_Cliente = @Id_Cliente
          AND Id_Tipo_Cli = 1010
    )
    BEGIN
        IF @ModoDebug = 1
        BEGIN
            PRINT 'Titular Vitalicio +71 (Id_Tipo_Cli 1010). No se le toca el ítem.'
        END
        SET @Excluir_Titular = 1
    END

    -- 4. Verifica familia del producto
    SET @Id_Familia = (SELECT Id_Familia FROM Productos WHERE Id_Producto = @Prod)
    IF @Id_Familia NOT IN (SELECT dbo.CF_xParam('Fam_CuotasSoc'))
        RETURN

    -- 5. Obtiene info del ítem original
    SELECT
        @Precio_CS_Titular = dbo.SF_CS_Precio_Persona(Id_Cliente, Fecha_QA),
        @Imp_Final = ISNULL(Imp_Final,0),
        @Id_Estado_Cbte = Id_Estado_Cbte,
        @Fecha_Venc = Fecha_Venc,
        @Lote = ISNULL(Lote,''),
        @Flag_QA = Flag_QA,
        @Bonif = ISNULL(Bonif,0),
        @Descripcion_Producto = Descripcion_Producto,
        @Fecha_QA = Fecha_QA,
		@Cond_Pago = Id_Cond_Pago,
		@Id_Usuario = Id_Usuario,
		@Porc_Iva	=Porc_Iva,
		@Imp_Iva	=Imp_Iva,
		@Cantidad_Saldo	=Cantidad_Saldo,
		@Cantidad		= Cantidad

    FROM Cbtes_Items
    WHERE Id_Trans = @trans AND Item = @Item AND Id_Cliente = @Id_Cliente

	IF @ModoDebug = 1
	BEGIN
		PRINT 'Precio CS Titular: ' + CONVERT(VARCHAR(30),@Precio_CS_Titular)
	END
    -- 6. Contar integrantes
    SELECT @Cant_Integrantes = COUNT(*)
    FROM Clientes
    WHERE Id_Cliente_Ref = @Id_Cliente
      AND Activo = 1
      AND Tipo_Persona = 'F'

    IF @Cant_Integrantes = 0
    BEGIN
		IF @ModoDebug = 1
		BEGIN
			PRINT 'Sin integrantes. Se actualiza solo el titular.'
        END
		IF @Excluir_Titular = 0
		BEGIN
			UPDATE Cbtes_Items
			SET Precio_Grav = @Precio_CS_Titular,
				Imp_Gravado = @Precio_CS_Titular,
				Imp_Final = @Precio_CS_Titular,
				Precio = @Precio_CS_Titular
			WHERE Id_Trans = @trans AND Item = @Item
		END
        RETURN
    END
	IF @ModoDebug = 1
	BEGIN
		PRINT 'Integrantes detectados: ' + CONVERT(VARCHAR(10),@Cant_Integrantes)
	END
    -- 7. Tabla temporal con todos los miembros del grupo familiar
    SELECT
        c.Id_Cliente,
        dbo.SF_CS_Precio_Persona(c.Id_Cliente, @Fecha_QA) AS Precio_CS,
        CASE WHEN c.Id_Cliente_Ref = 0 THEN 1 ELSE 0 END AS EsTitular
    INTO #IntegrantesGF
    FROM Clientes c
    WHERE (c.Id_Cliente_Ref = @Id_Cliente OR c.Id_Cliente = @Id_Cliente)
      AND c.Activo = 1
      AND c.Tipo_Persona = 'F'

    -- 8. Calcula ítem base y genera líneas de ítems para integrantes
    SET @item_base = ISNULL((SELECT MAX(Item) FROM Cbtes_Items WHERE Id_Trans = @trans),0) + 1

    SELECT
        ROW_NUMBER() OVER (ORDER BY Id_Cliente) + (@item_base - 1) AS ItemNuevo,
        i.Id_Cliente,
        i.Precio_CS
    INTO #ItemsNuevos
    FROM #IntegrantesGF i
    WHERE EsTitular = 0

    --  9. Crea registros para cada integrante

    INSERT INTO Cbtes_Items (
					Id_Trans,		Item,			Id_Producto,			Id_Estado_Cbte,
					Cantidad,		Precio_Grav,	Imp_Gravado,			Imp_Final,
					Precio,			Fecha_Venc,		Lote,					Flag_QA,
					Id_Cliente,		Bonif,			Descripcion_Producto,	Cantidad_Saldo,
					Fecha_QA,		Id_Usuario,		Porc_Iva,				Imp_Iva,
					Id_Cond_Pago
				)
    SELECT
					@trans,			i.ItemNuevo,	'CS',					@Id_Estado_Cbte,
					@Cantidad,		i.Precio_CS,	i.Precio_CS,			i.Precio_CS,
					i.Precio_CS,	@Fecha_Venc,	@Lote,					@Flag_QA,
					i.Id_Cliente,	@Bonif,			@Descripcion_Producto,	@Cantidad_saldo,
					@Fecha_QA,		@Id_Usuario,	@Porc_Iva,				@Imp_Iva,
					@Cond_Pago
    FROM #ItemsNuevos i
	IF @ModoDebug = 1
    BEGIN
		PRINT 'Se insertaron ' + CONVERT(VARCHAR(10),@@ROWCOUNT) + ' ítems de integrantes.'
	END
    --  10. Actualiza ítem del titular
	IF @Excluir_Titular = 0
	BEGIN
		UPDATE Cbtes_Items
		SET Precio_Grav = @Precio_CS_Titular,
			Imp_Gravado = @Precio_CS_Titular,
			Imp_Final = @Precio_CS_Titular,
			Precio = @Precio_CS_Titular
		WHERE Id_Trans = @trans AND Item = @Item
	END

    IF @ModoDebug = 1
	BEGIN
		PRINT 'Actualizado ítem del titular con precio CS.'
	END

    DROP TABLE #IntegrantesGF
    DROP TABLE #ItemsNuevos

	IF @ModoDebug = 1
    BEGIN
		PRINT 'Fin SP_Cbtes_Items_Integrantes'
		PRINT '---------------------------------------------------------------'
	END
END
-- >>> ALTER FIN


-- ------------------------------------------------------------------------------------
-- 3) VERIFICACIÓN (se corrió antes del COMMIT)
-- ------------------------------------------------------------------------------------
--   a) La definición nueva ya no tiene los dos RETURN de la 3.2 y sí tiene
--      @Excluir_Titular en los tres lugares esperados.
--   b) Prueba funcional sobre el cupón de octubre de FERRO (Id_Trans 6521321), dentro de
--      una transacción con ROLLBACK: se inserta una línea CS del titular, el trigger
--      tri_Cbtes_Items dispara el SP, y se comprueba que aparezcan las líneas de
--      541521 ORTIZ SUSANA y 901146 ABRAMOVITSCH FERRO IRENA PAZ.


-- ------------------------------------------------------------------------------------
-- 4) VUELTA ATRÁS
-- ------------------------------------------------------------------------------------
/*
    Para restaurar la versión anterior:

    DECLARE @def NVARCHAR(MAX) = OBJECT_DEFINITION(OBJECT_ID('dbo.zSP_Cbtes_Items_Integrantes_20260907'))
    SET @def = REPLACE(@def, '[dbo].[zSP_Cbtes_Items_Integrantes_20260907]',
                             '[dbo].[SP_Cbtes_Items_Integrantes]')
    SET @def = REPLACE(@def, 'CREATE PROCEDURE', 'ALTER PROCEDURE')
    EXEC sp_executesql @def
*/


-- ------------------------------------------------------------------------------------
-- 5) LO QUE ESTE CAMBIO NO RESUELVE
-- ------------------------------------------------------------------------------------
--   Los cupones de octubre ya emitidos NO se corrigen solos: el SP actúa cuando se
--   inserta el ítem del titular, o sea al generar el lote. Los 34 socios del período
--   2026-10 siguen sin su cuota y hay que resolverlos por administración (nota de
--   débito, o sumarlo a noviembre). Lo que este cambio garantiza es que el lote de
--   NOVIEMBRE, que todavía no se generó, salga bien.

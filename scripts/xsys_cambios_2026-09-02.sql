/* =============================================================================
   Deuda de cuotas de actividades — aplicado sobre xsys_geba el 02/09/2026.
   Registro de lo que quedó en la base; este archivo no se ejecuta solo.
   Ver también scripts/xsys_cambios_2026-08-31.sql y ..._2026-09-01.sql.

   Origen: planilla "Deuda_Actividades_al_25_08_2026.xlsx". Las hojas de la
   planilla traen sólo el nombre; el legajo salió de la hoja Deuda_Productos
   (columna Codigo) del mismo libro. Cruzaron las 164 personas, cada una con un
   legajo único y presente en el espejo, sin ambigüedades.

     2 y 3 cuotas   47 socios    $ 5.780.010   -> PASAN, el visor los marca
     4 cuotas      116 socios    $24.796.450   -> NO PASAN (motivo 118)

   (La planilla lista 117 filas en la hoja de 4 meses, pero YANORNO ISABELLA
   (892646) aparece dos veces con dos actividades: son 116 personas y los dos
   importes quedaron sumados en su fila.)

   Para dar por regularizado a alguien, sin borrar el antecedente:
     UPDATE CD_Clientes_Deuda_Actividades
        SET Activo = 0, Fecha_Baja = GETDATE() WHERE Id_Cliente = <id>
   Para levantar el bloqueo pero seguir avisando: Bloquea = 0.
   ============================================================================= */


/* ---------------------------------------------------------------------------
   1) La tabla
--------------------------------------------------------------------------- */
CREATE TABLE CD_Clientes_Deuda_Actividades (
    Id_Cliente   INT           NOT NULL PRIMARY KEY,
    Cuotas       SMALLINT      NULL,
    Importe      DECIMAL(18,2) NULL,
    Actividad    VARCHAR(60)   NULL,
    /* 1 = no puede pasar. 0 = pasa, pero el visor lo marca en amarillo. */
    Bloquea      TINYINT       NOT NULL DEFAULT 0,
    /* 0 = regularizado: deja de regir sin borrar el antecedente. */
    Activo       TINYINT       NOT NULL DEFAULT 1,
    Origen       VARCHAR(60)   NULL,
    Observacion  VARCHAR(200)  NULL,
    Fecha_Alta   DATETIME      NOT NULL DEFAULT GETDATE(),
    Fecha_Baja   DATETIME      NULL
)
GO
CREATE INDEX IX_CD_Deuda_Act_Bloquea
    ON CD_Clientes_Deuda_Actividades (Activo, Bloquea, Id_Cliente)
GO


/* ---------------------------------------------------------------------------
   2) El motivo del rechazo.
   OJO: CD_Motivos.Id_CD_Motivo es IDENTITY, hay que abrirla para fijar el 118.
--------------------------------------------------------------------------- */
SET IDENTITY_INSERT CD_Motivos ON
GO
INSERT INTO CD_Motivos
      (Id_CD_Motivo, Activo, Flag_Sistema, Tipo, Descripcion,
       Descripcion_Display, Descripcion_Pantalla, Descripcion_Personalizada)
VALUES (118, 1, 1, 'R', N'Deuda de Actividades', N'DEUDA DE ACTIVIDADES',
        N'Deuda de Actividades: pasá por Administración', '')
GO
SET IDENTITY_INSERT CD_Motivos OFF
GO


/* ---------------------------------------------------------------------------
   3) El rechazo en CP_SCA_RegistrarAcceso, justo después del rechazo por
      persona inactiva. Respaldo de la versión anterior:
      zCP_SCA_RegistrarAcceso_20260902

   El bloque agregado, tal como quedó:

      IF(@Flag_Evento IN (0,1))
      BEGIN
          IF EXISTS (SELECT 1 FROM CD_Clientes_Deuda_Actividades D
                     WHERE D.Id_Cliente = @Id_Cliente
                       AND ISNULL(D.Activo, 1) = 1
                       AND ISNULL(D.Bloquea, 0) = 1)
          BEGIN
              SET @Id_CD_Motivo = 118
              EXEC CP_SCA_RegistrarAcceso_Rta 0, '', 'ERROR', @Foto, @Foto_Bin,
                   @Id_Cliente, @Id_Cliente_Ref, @pTipoAcceso, @pTag, @pFecha,
                   @pId_Controlador, @pId_Acceso, @Id_CD_Motivo,
                   @pFlag_GrabaRegistro, @pFlag_Permite_Paso
              RETURN
          END
      END
--------------------------------------------------------------------------- */


/* ---------------------------------------------------------------------------
   4) IMPORTANTE — el molinete no alcanza.

   El facial NO consulta a xSys: valida contra la lista blanca que calcula
   geba_acs, que reimplementa la cascada en DOS lugares. Si el rechazo estuviera
   sólo en el SP, los 116 seguirían entrando por el facial. Se agregó el mismo
   motivo 118, en el mismo punto de la cascada, en:

     access_control/services/services.py   MSSQLAccessCheckService (de a uno)
     xsys/services/whitelist_bulk.py       compute_habilitacion_bulk (masivo)

   El barrido completo verifica 100/100 que las dos coincidan, y así quedó.

   Y OJO con los contenedores de larga vida (whitelist-full, sync, poller,
   cambios-poller): tienen el código en memoria y hay que reiniciarlos, si no
   siguen calculando con la versión vieja y vuelven a habilitar a todos. Pasó.
--------------------------------------------------------------------------- */

/* =============================================================================
   Molinete Intelektron 10.0.0.117 manejado por geba_acs (modo online)
   28/10/2026 - NO APLICADO (opcional). Requiere el OK del usuario y correr como sa.
   El molinete esta en produccion con geba_acs (modo real) desde el 28/10/2026 tarde.

   SITUACION
   La placa 10.0.0.117 (API-4002, fw 02.00.019, serie 2207AP42586, un solo
   lector Mifare) ES el controlador 47 de Clever: "JN_Freyre_Mol3(Garita)",
   Id_Acceso 12 (Newbery), Intelek_IP 192.168.3.28, Activo 1. Su ultimo paso
   en CD_ES es del 05/08/2026: la sacaron de Newbery y la app de Clever la
   sigue buscando en 192.168.3.28 sin encontrarla. Queremos que la decida
   geba_acs (manage.py intelektron_online) llamando al MISMO
   CP_SCA_RegistrarAcceso que usan los demas molinetes, para que la cascada,
   los motivos y CD_ES sean los mismos.

   PROBADO EN VIVO 28/10/2026 (modo observar, sin abrir):
   - itk_open a 10.0.0.117:3001 con callbacks funciona; con keepalive de 30 s
     la sesion queda estable (sin keepalive la placa corta cada 100 s).
   - Cada lectura llega como evento "No identificado" (106) con el tag en
     hex (EF2A984A -> Clientes.Credencial_Nro, socio activo) y la placa
     espera la respuesta; geba_acs contesta en ~100-270 ms.
   - La llamada al SP fallo por falta de EXECUTE (esperado): ver punto 2.

   DECISION 28/10/2026 (tarde): geba_acs valida con su REPLICA LOCAL de la
   cascada (access_control/services/validador_local.py) y registra en su propia
   base (acs_paso_molinete). NO escribe en xSys: no hace falta el GRANT EXECUTE
   del punto 2 y el UPDATE del punto 1 pasa a ser OPCIONAL (solo para que la
   app de Clever deje de buscar la placa en 192.168.3.28 y para que el acceso
   quede documentado en CD_Controladores). El Id_Acceso se le pasa al comando
   con --id-acceso / INTELEKTRON_ONLINE_ID_ACCESO.

   QUE HARIA FALTA EN LA BASE (opcional / solo si se vuelve al SP)
   1) Reusar la fila 47 de CD_Controladores: Intelek_IP = 10.0.0.117,
      Id_Acceso = la puerta donde esta hoy, Activo = 0. Con Activo = 0 la app
      CleverIntelekReiIn NO la toma (TraerControladoresIntelektron filtra
      ISNULL(CC.Activo,0) <> 0 AND Tipo_Cont = 'K'); el SP no mira Activo y
      resuelve el acceso con CF_SCA_IdAcceso(47). Tipo_Cont 'K' ya esta.
   2) EXECUTE sobre CP_SCA_RegistrarAcceso para el login geba_acs. La
      insercion en CD_ES la hace el SP por encadenamiento de propiedad (dbo).

   DATOS A CONFIRMAR ANTES DE APLICAR
   - @Id_Acceso: a que puerta pertenece fisicamente el 10.0.0.117. Pista: la
     credencial que paso en la prueba (socio concesionario de San Martin)
     paso el mismo dia por los controladores 59/60 del acceso 14 SM-Alcorta.
     (14 SM-Alcorta, 15 SM-Ombues, 16 SM-Noble, 26 SM_Ombues_2, 12 Newbery.)
     Define que categorias/contratos habilitan y si exige cuota.
   ============================================================================= */

USE xsys_geba;
GO

DECLARE @Id_Controlador SMALLINT = 47;
DECLARE @Id_Acceso      SMALLINT = 14;       /* <-- CONFIRMAR */
DECLARE @Ip             VARCHAR(20) = '10.0.0.117';

/* ---- guardas ------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM CD_Controladores
               WHERE Id_Controlador = @Id_Controlador AND Intelek_IP = '192.168.3.28' AND Tipo_Cont = 'K')
    RAISERROR('El controlador %d no es el que se espera (JN_Freyre_Mol3 192.168.3.28)', 16, 1, @Id_Controlador);
IF EXISTS (SELECT 1 FROM CD_Controladores WHERE Intelek_IP = @Ip AND Id_Controlador <> @Id_Controlador)
    RAISERROR('Ya hay otro controlador con la IP %s', 16, 1, @Ip);
IF NOT EXISTS (SELECT 1 FROM CD_Accesos WHERE Id_Acceso = @Id_Acceso AND Activo = 1)
    RAISERROR('Id_Acceso %d no existe o esta inactivo', 16, 1, @Id_Acceso);

/* ---- respaldo ------------------------------------------------------------ */
IF OBJECT_ID('dbo.zCD_Controladores_20261028') IS NULL
    SELECT * INTO dbo.zCD_Controladores_20261028 FROM CD_Controladores;

/* ---- simulacion: cambiar ROLLBACK por COMMIT para aplicar ---------------- */
BEGIN TRAN;

UPDATE CD_Controladores
SET Id_Acceso   = @Id_Acceso,
    Intelek_IP  = @Ip,
    Intelek_Puerto_Host = 3001,
    Descripcion = 'Molinete 10.0.0.117 (geba_acs online)',
    Observacion = 'Ex JN_Freyre_Mol3(Garita) 192.168.3.28 acceso 12. Manejado por geba_acs (intelektron_online). '
                + 'Activo=0 a proposito: la app de Clever no debe tomarlo. 28/10/2026',
    Activo      = 0
WHERE Id_Controlador = @Id_Controlador;

SELECT Id_Acceso, Id_Controlador, Tipo_Cont, Activo, Intelek_IP, Intelek_Puerto_Host, Descripcion
FROM CD_Controladores WHERE Id_Controlador = @Id_Controlador;

/* El SP tiene que resolver el acceso: */
SELECT dbo.CF_SCA_IdAcceso(@Id_Controlador) AS Id_Acceso_Resuelto;

/* Prueba de la cascada SIN grabar (GrabaRegistro = 0) con el tag de la prueba: */
-- EXEC dbo.CP_SCA_RegistrarAcceso @Id_Controlador, 'E', GETDATE(), '19000101', 'EF2A984A', 0, '', '', '', 0, 0, 0;

ROLLBACK TRAN;   /* <-- COMMIT TRAN para aplicar */
GO

/* ---- permiso para la aplicacion (fuera de la transaccion) ---------------- */
-- GRANT EXECUTE ON dbo.CP_SCA_RegistrarAcceso TO [geba_acs];

/* ---- vuelta atras -------------------------------------------------------- */
-- UPDATE c SET c.Id_Acceso = z.Id_Acceso, c.Intelek_IP = z.Intelek_IP, c.Intelek_Puerto_Host = z.Intelek_Puerto_Host,
--              c.Descripcion = z.Descripcion, c.Observacion = z.Observacion, c.Activo = z.Activo
-- FROM CD_Controladores c JOIN dbo.zCD_Controladores_20261028 z ON z.Id_Controlador = c.Id_Controlador
-- WHERE c.Id_Controlador = 47;
-- REVOKE EXECUTE ON dbo.CP_SCA_RegistrarAcceso TO [geba_acs];

/* =============================================================================
   CONFIGURACION DE LA PLACA (leida el 28/10/2026 por su web, puerto 80, con
   get_num_cfg.cgi; ids del JavaScript de la web y del enum ConfigNodoEnum de
   la app de Clever). Ya esta igual que las placas que atiende Clever: NO hay
   que tocarle nada para la prueba.

     id   nombre                              .117 hoy
     9    CFG_HOST_SND_MODE  "Modo Envio"     2  (Online/Offline)
     36   CFG_VALIDATE_ALWAYS                 1
     38   CFG_HOST_SND_RETRY                  3
     128  CFG_HOST_TIMEOUT (ms)               5000
     131  "Timeout Conexion" (seg)            100  -> por eso el keepalive de 30 s
     145  CFG_ECHO_TIMEOUT (min)              1
     102  CFG_IP_P0RT "Puerto Host"           3001
     24   CFG_COM_EVENT_1_READER (bits)       3  (1 Aceptado + 2 No identificado)
     46   CFG_COM_EVENT_2_READER (bits)       3
     16/17 rele y tiempo "Aceptado" lector 1  1 / 1 s   (si el aspa no libera: rele 2, 3 s como en agosto)
     18/19 rele y tiempo "Aceptado" lector 2  1 / 1 s
     29/30 tipo de lector 1 / 2               1 / 1
     7/8  sentido lector 1 / 2                1 / 1

   Lectura: GET http://IP/get_num_cfg.cgi?N|1&9|1&36|1&24|1&46|1&16|1&17
   Escritura (solo con OK): GET http://IP/set_num_cfg.cgi?N|1&16&2 ...
   Las placas que atiende Clever estan en 10.0.0.25/.30/.31/.64/.65/.66/.67/
   .69/.70/.73/.74/.93 y 192.168.3.x: NO tocarlas.
   ============================================================================= */

/*======================================================================================
  xSys - La app deja de conectarse como `sa` y pasa a un login propio acotado.
  Fecha: 2026-09-07

  Motivo: hasta ahora geba_acs leía xSys como `sa` (sysadmin). Mientras la integración
  era de sólo lectura el riesgo era el de una fuga de credenciales; desde que la
  pantalla de fichas ESCRIBE en `Clientes`, la aplicación tiene que poder hacer
  exactamente eso y nada más.

  Login creado: geba_acs   (contraseña generada al azar, cargada directo en el .env;
                            no figura en este archivo ni en ningún log)

  Backup del .env anterior: .env.bak_20260907_sa

  Las tres conexiones MSSQL de la app quedaron con este usuario:
      MSSQL_XSYS_USER          espejo local, diagnóstico, lista blanca, fichas
      MSSQL_ACCESS_LOG_USER    poller de CD_ES
      MSSQL_ACCESS_CHECK_*     hereda de ACCESS_LOG (no está en el .env)

  --------------------------------------------------------------------------------------
  QUÉ PUEDE HACER
  --------------------------------------------------------------------------------------
  1. LEER todo (db_datareader). La app consulta decenas de tablas y el sync recorre
     media base; acotar el SELECT tabla por tabla sería frágil y se rompería con
     cualquier consulta nueva. Es la parte más amplia del permiso y la que se puede
     seguir apretando más adelante si hace falta.

  2. EJECUTAR ocho funciones escalares, que son las que la app llama por nombre:
        CF_NC_A_FC
        CF_SCA_IdAcceso
        CF_SCA_IdentifIdCliente
        CF_SCA_ValidarContratosTipos
        CF_SCA_ValidarMaster
        CF_SCA_ValidarTipo
        CF_SCA_ValidarUltCuotaPaga
        CF_SCA_ValidarVencimientosPersona
     Las funciones internas que éstas invocan (SF_Categ, SF_Prod, etc.) andan por
     cadena de propiedad: todo pertenece a dbo, así que no hace falta otorgarlas.

  3. ACTUALIZAR `Clientes`, y sólo en las 38 columnas de la ficha editable (las de
     xsys.services.ficha.EDITABLES más Fecha_Modif). El permiso es por COLUMNA: aunque
     alguien encuentre una inyección en la app, no puede tocar Ult_Cuota_Paga,
     Credencial_Banda1 ni ninguna otra.

  --------------------------------------------------------------------------------------
  QUÉ NO PUEDE HACER (verificado contra la base, no supuesto)
  --------------------------------------------------------------------------------------
     INSERT / DELETE sobre Clientes ................. denegado
     UPDATE de columnas fuera de la lista ........... denegado (por columna)
     UPDATE / DELETE sobre Contratos, Cbtes_Items,
       CD_ES, CD_Clientes_Novedades ................. denegado
     EXEC CPJ_Cbtes_Lotes_Facturar_V2 (facturación) . denegado
     ALTER PROCEDURE / CREATE TABLE ................. denegado
     Ver el código de los stored procedures ......... OBJECT_DEFINITION devuelve NULL
     Crear logins o darse permisos .................. sin efecto
     Entrar a las otras 7 bases del servidor ........ denegado
       (banco, xsys, Clever_Prueba_Geba, Clever_Contable_200120,
        Xsys_Contable_Master, Xsys_Contable_300617, prueba_master)

  Nota sobre los GRANT: si el propio geba_acs ejecuta `GRANT UPDATE ON ... TO [geba_acs]`
  el motor NO devuelve error, pero tampoco otorga nada: no queda fila en
  sys.database_permissions y el UPDATE sigue denegado. Se comprobó de las dos maneras.

  --------------------------------------------------------------------------------------
  VERIFICACIÓN HECHA DESPUÉS DEL CAMBIO
  --------------------------------------------------------------------------------------
     xsys_sync completo ...... novedades, socios, fotos, whitelist, 16 movimientos,
                               27 accesos, 73 controladores, 47 motivos,
                               163 deudas de actividades, 1259 bajas en revisión
     xsys_cambios_poll ....... 290 socios y 979 contratos actualizados
     whitelist_full .......... 54.390 socios en 46 s, 17.799 habilitados,
                               0 correcciones (idéntico a la corrida con `sa`)
     poller de CD_ES ......... ingesta en vivo, sin errores
     pantalla de fichas ...... las cinco pestañas responden 200
     escritura ............... alta y reversión de un dato real en la ficha 569449

======================================================================================*/


-- ------------------------------------------------------------------------------------
-- LO QUE SE EJECUTÓ (desde Python, contra xsys_geba, como sa)
-- ------------------------------------------------------------------------------------

/*
CREATE LOGIN [geba_acs] WITH PASSWORD = N'<generada al azar>',
                             CHECK_POLICY = ON,
                             DEFAULT_DATABASE = [xsys_geba];

USE [xsys_geba];
CREATE USER [geba_acs] FOR LOGIN [geba_acs];
ALTER ROLE db_datareader ADD MEMBER [geba_acs];

GRANT EXECUTE ON dbo.[CF_NC_A_FC]                        TO [geba_acs];
GRANT EXECUTE ON dbo.[CF_SCA_IdAcceso]                   TO [geba_acs];
GRANT EXECUTE ON dbo.[CF_SCA_IdentifIdCliente]           TO [geba_acs];
GRANT EXECUTE ON dbo.[CF_SCA_ValidarContratosTipos]      TO [geba_acs];
GRANT EXECUTE ON dbo.[CF_SCA_ValidarMaster]              TO [geba_acs];
GRANT EXECUTE ON dbo.[CF_SCA_ValidarTipo]                TO [geba_acs];
GRANT EXECUTE ON dbo.[CF_SCA_ValidarUltCuotaPaga]        TO [geba_acs];
GRANT EXECUTE ON dbo.[CF_SCA_ValidarVencimientosPersona] TO [geba_acs];

GRANT UPDATE ON dbo.Clientes (
    [Activo], [Apellido], [Cp], [Credencial_Entrega], [Credencial_Nro], [Cuit],
    [Depto], [Direccion], [Doc_Nro], [Email], [Email_Web], [Entre_Calle_1],
    [Entre_Calle_2], [Estado_Civil], [Fecha_Alta], [Fecha_Baja], [Fecha_Nac],
    [Flag_Comunic_Email], [Flag_Comunic_Sms], [Id_Cliente_Ref], [Id_Cond_Vta],
    [Id_Estado_Cliente], [Id_Motivo_Est], [Id_Tipo_Cli], [Id_Tipo_Doc], [Legajo],
    [Localidad_Descrip], [Nombre], [Nro], [Observacion], [Pasap_Nro],
    [Provincia_Descrip], [Razon_Social], [Sexo], [Tel_Movil], [Tipo_Persona],
    [telefono], [Fecha_Modif]
) TO [geba_acs];
*/


-- ------------------------------------------------------------------------------------
-- CONTROL: qué permisos tiene hoy
-- ------------------------------------------------------------------------------------
SELECT p.permission_name, p.state_desc,
       objeto  = ISNULL(OBJECT_NAME(p.major_id), '(base)'),
       columna = ISNULL(c.name, '(todas)')
FROM   sys.database_permissions p
       JOIN sys.database_principals u ON u.principal_id = p.grantee_principal_id
       LEFT JOIN sys.columns c ON c.object_id = p.major_id AND c.column_id = p.minor_id
WHERE  u.name = 'geba_acs'
ORDER BY p.permission_name, objeto, columna

SELECT rol = r.name
FROM   sys.database_role_members m
       JOIN sys.database_principals r ON r.principal_id = m.role_principal_id
       JOIN sys.database_principals u ON u.principal_id = m.member_principal_id
WHERE  u.name = 'geba_acs'


-- ------------------------------------------------------------------------------------
-- SI SE AGREGA UN CAMPO EDITABLE A LA FICHA
-- ------------------------------------------------------------------------------------
/*
   Sumar un Campo a xsys/services/ficha.py NO alcanza: hay que otorgar también el
   UPDATE de esa columna, o el guardado va a fallar con
   "The UPDATE permission was denied on the column ...".

       GRANT UPDATE ON dbo.Clientes ([LaColumnaNueva]) TO [geba_acs];
*/


-- ------------------------------------------------------------------------------------
-- VUELTA ATRÁS
-- ------------------------------------------------------------------------------------
/*
   Restaurar el .env:   cp .env.bak_20260907_sa .env   y recrear los contenedores.

   Y si además se quiere eliminar el login:
       USE [xsys_geba]; DROP USER [geba_acs];
       DROP LOGIN [geba_acs];
*/

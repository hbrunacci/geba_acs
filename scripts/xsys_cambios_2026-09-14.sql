/*======================================================================================
  xSys - CORRECCIÓN: motivos de acceso corridos 9 lugares + flag de cuota en Aldao_Mitre
  Fecha: 2026-09-14 11:55  — aplicado sobre xsys_geba

  Registro de lo que quedó en la base; este archivo no se ejecuta solo.
  Continúa scripts/xsys_cambios_2026-09-09.sql
  Origen: auditoría de los rechazos del fin de semana 12-13/09/2026
          (scripts/accesos_rechazos_finde_2026-09-12_13.html)

  --------------------------------------------------------------------------------------
  PROBLEMA 1 - El molinete le miente al socio: "VISITA - Hockey sobre Césped"
  --------------------------------------------------------------------------------------
  CP_SCA_RegistrarAcceso asignaba:

        SET @Id_CD_Motivo = 300   /* La Persona no posee UCP al dia */
        SET @Id_CD_Motivo = 301   /* Habilit. por Produc. Pack Comprado */

  pero en el catálogo CD_Motivos esos números son otra cosa:

        300..308  bloque "VISITA - <deporte>"  (Hockey, Patines, Fútbol, Rugby,
                  Patín artístico, Tenis, Club 1 hs., Waterpolo)   Tipo H
        309       La Persona no posee UCP al día                   Tipo R
        310       Habilit. por Produc. Pack Comprado               Tipo H

  Los otros 35 motivos que usa el SP (101..217) coinciden EXACTAMENTE con el catálogo.
  Sólo estos dos estaban corridos, y los dos exactamente 9 lugares: alguien insertó el
  bloque de 9 filas "VISITA - …" y empujó las dos entradas preexistentes a 309 y 310 sin
  actualizar el SP.

  Efecto visible: el visor toma el texto de CD_Motivos (xsys/api_views.py, mensaje_pantalla),
  así que a todo socio rechazado por cuota impaga se le mostraba "VISITA - Hockey sobre
  Césped". Desde el 10/12/2024: 362 eventos, 153 de ellos el fin de semana del 12-13/09.

  NO cambia quién entra. El rechazo por cuota era correcto; lo que estaba mal era el
  cartel. El informe del 13/09 contó esos 71 socios como "mal rechazados" por error.

  --------------------------------------------------------------------------------------
  PROBLEMA 2 - Aldao_Mitre rechaza a los VITALICIO + 71 (sin resolver del todo)
  --------------------------------------------------------------------------------------
  Desde el 12/09 esa puerta rechaza con "_CUOTA VENCIDA" a socios VITALICIO + 71, que el
  club no factura y que hasta el 11/09 entraban por categoría ("HABILIT.X VITAL.").
  El 14/09, antes de este cambio: 73 rechazos sobre 31 socios distintos.

  Lo que se comprobó:
    - CF_SCA_ValidarUltCuotaPaga(socio, 2, fecha) devuelve 1 (habilitado) en los 89
      rechazos del sábado -> el SP los dejaría pasar.
    - La lista de categorías habilitadas de la puerta 2 tiene SÓLO las exentas
      (master, empleado, vitalicio +71, honorario, olímpico, profesores, proveedores),
      así que los socios comunes entran por cuota y los exentos por categoría.
    - "_CUOTA VENCIDA" es un mensaje de 2016 que ningún módulo de xSys genera y que
      SÓLO la puerta 2 sigue emitiendo; las demás dejaron de hacerlo en 2016.
    - SanMartin_Alcorta corre la misma familia de app vieja y sus vitalicios pasan bien.
    - Entre el 10 y el 14/09 no se modificó ningún objeto salvo CF_SCA_ValidarUltCuotaPaga
      (11/09), y Seg_Usuarios_Audit no registra cambios de configuración en CD_Accesos.

  Conclusión: decide el programa cliente de la máquina de Aldao (GEBAA), un build viejo
  que aplica su propia regla sobre Ult_Cuota_Paga y no conoce la exención. Eso NO se
  arregla desde la base. Lo de abajo es un intento acotado y reversible, no la solución.

  (Síntoma adicional de esa misma máquina: 3.915 rechazos "_CREDENCIAL INVALIDA" en 30 días.)

  --------------------------------------------------------------------------------------
  1) BACKUP
  --------------------------------------------------------------------------------------
        zCP_SCA_RegistrarAcceso_20260914

  Guardas previas: definición legible; cada patrón presente EXACTAMENTE una vez; el
  catálogo tiene 309 (Tipo R) y 310 (Tipo H); el backup no existía.

  --------------------------------------------------------------------------------------
  2) EL CAMBIO
  --------------------------------------------------------------------------------------

-- >>> CAMBIO 1 INICIO  (CP_SCA_RegistrarAcceso, sección "Rechaza por UCP")
--     antes:   SET @Id_CD_Motivo = 300 	/*La Persona no posee UCP al dia*/
--     después: SET @Id_CD_Motivo = 309 	/*La Persona no posee UCP al dia*/
-- >>> CAMBIO 1 FIN

-- >>> CAMBIO 2 INICIO  (misma SP, sección de habilitaciones)
--     antes:   SET @Id_CD_Motivo = 301 	/*Habilit. por Produc. Pack Comprado*/
--     después: SET @Id_CD_Motivo = 310 	/*Habilit. por Produc. Pack Comprado*/
-- >>> CAMBIO 2 FIN

-- >>> CAMBIO 3 INICIO  (intento acotado para Aldao_Mitre — APLICADO Y REVERTIDO)
--
--     >>> NO QUEDÓ EN LA BASE. Se aplicó 11:56, se revirtió 12:22. Ver "RESULTADO"
--     >>> al final de este bloque. La puerta 2 está de nuevo en 2, como las otras 26.
--
--     antes:   CD_Accesos.Flag_Ult_Cuota_Paga = 2  para Id_Acceso = 2
--     durante: CD_Accesos.Flag_Ult_Cuota_Paga = 1
--
--     Según la documentación del propio SP (líneas 249-253):
--       Tipo 0: no se valida por UCP en ese acceso
--       Tipo 1: con tener la UCP el socio queda habilitado (se evalúa en habilitaciones)
--       Tipo 2: el socio DEBE tener UCP, y además un producto que habilite el acceso
--
--     Con 1 la cuota deja de ser barrera previa y pasa a ser una vía de habilitación.
--     Los socios comunes NO están en la lista de categorías de esa puerta, así que un
--     moroso sigue sin entrar: cae en el motivo 112 "no cumple ninguna condición".
--     Es una apuesta: SanMartin_Alcorta tiene el mismo valor 2 y no falla, así que puede
--     no alcanzar.
--
--     RESULTADO: no sirvió. A las 12:15, diecinueve minutos después del cambio, la socia
--     CASTRO ELODINA HEBE (VITALICIO + 71) seguía recibiendo "_CUOTA VENCIDA" en esa
--     puerta; 5 rechazos sobre 2 socios después de las 11:56. Confirma que el programa
--     cliente de Aldao decide por su cuenta y no mira este flag —o lo lee sólo al
--     arrancar, cosa que no se puede descartar sin reiniciarlo—.
--
--     Se revirtió a las 12:22 porque dejarlo en 1 abre un hueco sin dar nada a cambio:
--     la puerta 2 tiene el producto CS en CD_Accesos_Prod, y con flag 1 la cuota deja de
--     ser obligatoria, así que un socio con cuota vencida pero producto CS activo podría
--     habilitarse por producto. Con flag 2 se exigen las dos cosas.
--
--     Si se quiere reintentar en serio, hay que cambiar el flag Y reiniciar el cliente de
--     Aldao a la vez, con alguien mirando la puerta.
-- >>> CAMBIO 3 FIN

  Ejecución: script de Python contra xSys, una sola transacción, verificación antes del
  COMMIT (ver aplicar_fix_motivos.py en el scratchpad de la sesión). Idempotente.

  --------------------------------------------------------------------------------------
  3) VERIFICACIÓN (antes del COMMIT)
  --------------------------------------------------------------------------------------
        SP con 309 una vez ........ OK
        SP con 310 una vez ........ OK
        SP sin 300/301 ............ OK
        backup conserva 300/301 ... OK
        puerta 2: flag 2 -> 1 ..... OK   (después revertido: ver CAMBIO 3)
        puertas con flag <> 2 ..... 1 (sólo la 2)   -> hoy 0, tras la vuelta atrás

  Estado final en la base: cambios 1 y 2 aplicados; cambio 3 revertido.

  --------------------------------------------------------------------------------------
  4) VUELTA ATRÁS
  --------------------------------------------------------------------------------------
  El flag, solo:

        UPDATE CD_Accesos SET Flag_Ult_Cuota_Paga = 2 WHERE Id_Acceso = 2;

  El SP, con los DOS replace y en este orden (si sólo se cambia CREATE por ALTER, se
  termina modificando el backup en vez del procedimiento original):

        DECLARE @def NVARCHAR(MAX) =
            OBJECT_DEFINITION(OBJECT_ID('dbo.zCP_SCA_RegistrarAcceso_20260914'));
        SET @def = REPLACE(@def, '[zCP_SCA_RegistrarAcceso_20260914]',
                                 '[CP_SCA_RegistrarAcceso]');
        SET @def = REPLACE(@def, 'CREATE PROCEDURE', 'ALTER PROCEDURE');
        EXEC sp_executesql @def;

  --------------------------------------------------------------------------------------
  5) LO QUE ESTE CAMBIO NO RESUELVE
  --------------------------------------------------------------------------------------
  - El motivo 301 quedaba fuera del catálogo y ahora apunta a 310, pero las filas
    300..308 ("VISITA - <deporte>") siguen sin que nadie las escriba: ningún módulo de
    xSys asigna esos números. Si el club pensaba usarlas, falta el código que lo haga.
  - El programa cliente de Aldao sigue siendo viejo. Si el cambio 3 no alcanza, hay que
    actualizarlo o migrar esa puerta al visor nuevo, como ya se hizo con las otras siete.
  - Los 362 eventos históricos ya registrados con motivo 300 quedan como están: el
    Id_CD_Motivo se congela en CD_ES al momento del paso. Para leer el histórico hay que
    saber que antes del 14/09/2026 un motivo 300 significa "no posee UCP al día".
======================================================================================*/

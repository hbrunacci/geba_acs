/*======================================================================================
  xSys - CORRECCIÓN: aprobar un cambio de datos borraba la relación del grupo familiar
  Fecha: 2026-09-09 19:28  — aplicado sobre xsys_geba

  Registro de lo que quedó en la base; este archivo no se ejecuta solo.
  Informe completo en scripts/xsys_informe_relacion_conyuge_2026-09-09.html
  Continúa scripts/xsys_cambios_2026-09-07b.sql

  --------------------------------------------------------------------------------------
  EL PROBLEMA
  --------------------------------------------------------------------------------------
  Cuando un socio actualiza sus datos, xSys deja una ficha temporal en Clientes_Temp con
  Flag_Modificacion = 1. Al aprobarla, la rama de modificación de los SP de aprobación
  vuelca esa ficha sobre Clientes con un UPDATE que enumera columna por columna, e
  incluye:

        Id_Cli_Relac = ct.Id_Cli_Relac,

  El formulario que genera esa ficha es de datos de contacto: manda trece campos
  (teléfono, celular, mail, domicilio, localidad…) y NINGUNO es la relación familiar.
  Así que ct.Id_Cli_Relac llega NULL y el UPDATE lo copia igual.

  Id_Cliente_Ref no se toca, de modo que el socio sigue colgando del titular y el grupo
  familiar se ve entero en pantalla. Lo único que se pierde es el rol: SF_Desc_GF da el
  20% de descuento sólo si Id_Cli_Relac = 2, así que el cónyuge pasa a pagar la cuota
  completa. No se nota hasta el lote siguiente, que se emite con más de un mes de
  anticipación, y para entonces el cupón ya se cobró por débito automático.

  Origen de las solicitudes: 59 de los 68 socios afectados en 2026 entraron por la App
  (Clientes_Temp.Observacion = 'Actualizado desde App'); el resto por mostrador. El bug
  no es de la App: es del SP, que interpreta el silencio del formulario como un borrado.

  --------------------------------------------------------------------------------------
  ALCANCE MEDIDO (al 2026-09-09, sobre lo facturado)
  --------------------------------------------------------------------------------------
  Criterio: socios cuya cuota social pasó de ser el 80% de la del titular a ser el 100%,
  comparando ítem contra ítem dentro del mismo cupón. Es evidencia de facturación, no de
  ficha: Clientes_Hist sólo conserva el valor anterior en 130 de 4.852 casos, porque los
  triggers viejos guardaban NULL en esa columna.

        75  socios con quiebre 80% -> 100% en 2026
        68  atribuibles a este bug      -> 40 sin resolver, 28 restaurados a mano
         6  bajas legítimas (hijos que cumplieron 22, relación 3 vigente)
         1  a revisar (relación 5)

        $ 5.632.964   cobrado de más, acumulado en los lotes de 2026
        $ 1.180.140   sólo en CSOC_2026-10
        hasta 8 meses de arrastre; el quiebre más viejo es CSOC_2026-02

  Últimos 3 meses (lotes generados entre 09/06 y 09/09): 17 socios, 12 sin resolver,
  $ 751.164. Además hay 12 socios con la relación rota DESPUÉS de generado el lote de
  octubre: todavía no facturaron mal, rompen en noviembre si no se los corrige antes.

  --------------------------------------------------------------------------------------
  1) BACKUP  (se ejecutó antes de los ALTER, en la misma transacción)
  --------------------------------------------------------------------------------------
  De cada SP se tomó OBJECT_DEFINITION y se creó una copia idéntica, renombrada:

        zCP_Clientes_Temp_Aprobar_20260909
        zCP_Clientes_Temp_Aprobar__20260909      (ojo: doble guión bajo)
        zCP_Persona_Temp_Aprobar_20260909

  Guardas previas, por SP: que exista y sea legible; que la línea a cambiar aparezca
  EXACTAMENTE una vez; que estén los alias cl/ct; que no estuviera ya corregido; y que
  el backup no existiera de antes.

  --------------------------------------------------------------------------------------
  2) EL CAMBIO
  --------------------------------------------------------------------------------------
  Una sola línea, idéntica en los tres SP, dentro del UPDATE de la rama de modificación
  (el de las altas usa INSERT y no se tocó: ahí tomar el valor del formulario sí
  corresponde).

-- >>> CAMBIO INICIO
--     antes:
--            Id_Cli_Relac		= ct.Id_Cli_Relac,
--     después:
              Id_Cli_Relac		= ISNULL(ct.Id_Cli_Relac, cl.Id_Cli_Relac),
-- >>> CAMBIO FIN

  ct es la ficha temporal, cl es la ficha real. El comportamiento sólo cambia cuando el
  valor entrante es NULL, que es el caso que rompía: ahora conserva el que ya tenía el
  socio. Si la solicitud trae un valor, se escribe ese valor, igual que antes.

  Se usó ALTER y no DROP+CREATE: el objeto conserva su object_id, así que se mantienen
  los permisos y no hay ventana en la que el procedimiento no exista.

  Los tres SP:
        CP_Clientes_Temp_Aprobar     (línea 171)
        CP_Clientes_Temp_Aprobar_    (línea 262)   <- el que modificó el proveedor en 2025
        CP_Persona_Temp_Aprobar      (línea 102)

  Se corrigieron los tres porque ninguno se invoca desde SQL —los llama la aplicación
  directamente— y el caché de planes no permite distinguir cuál usa la App. El cambio es
  inocuo en los que no se usen.

  Ejecución: script de Python contra xSys, en una transacción explícita, con verificación
  antes del COMMIT. Está en el scratchpad de la sesión como aplicar_fix.py; es idempotente
  (saltea el SP si ya tiene el ISNULL o si su backup ya existe).

  --------------------------------------------------------------------------------------
  3) VERIFICACIÓN
  --------------------------------------------------------------------------------------
  a) Textual, antes del COMMIT: las tres definiciones nuevas contienen el ISNULL y
     ninguna conserva la línea vieja; los tres backups sí la conservan.

  b) Funcional, sobre el caso testigo, en una transacción revertida:
     socio 832188 MOSSIER KARINA ANDREA (Id_Cli_Relac = 2) con su solicitud 85671, que es
     justamente una que trae Id_Cli_Relac NULL. Se ejecutó el UPDATE tal como quedó en el
     SP, y también la versión vieja para contraste:

          código viejo   filas=1   Id_Cli_Relac queda en NULL   <- reproduce el bug
          código nuevo   filas=1   Id_Cli_Relac queda en 2      <- corregido

     El resto de los campos se actualiza igual en ambos casos (se controló el teléfono).
     filas=1 en las dos variantes confirma, de paso, que el "UPDATE Clientes ... FROM
     Clientes cl" alcanza una sola fila y no hace un producto cartesiano.

     Después del ROLLBACK la ficha quedó como estaba (Id_Cli_Relac = 2).

  --------------------------------------------------------------------------------------
  4) VUELTA ATRÁS
  --------------------------------------------------------------------------------------
  Por SP, con los DOS replace y en este orden. Si sólo se cambia CREATE por ALTER, se
  termina modificando el backup en vez del procedimiento original:

        DECLARE @def NVARCHAR(MAX) =
            OBJECT_DEFINITION(OBJECT_ID('dbo.zCP_Clientes_Temp_Aprobar__20260909'));
        SET @def = REPLACE(@def, '[zCP_Clientes_Temp_Aprobar__20260909]',
                                 '[CP_Clientes_Temp_Aprobar_]');
        SET @def = REPLACE(@def, 'CREATE PROCEDURE', 'ALTER PROCEDURE');
        EXEC sp_executesql @def;

  --------------------------------------------------------------------------------------
  5) LO QUE ESTE CAMBIO NO RESUELVE
  --------------------------------------------------------------------------------------
  Frena la sangría, no repara lo ya roto. Los 40 socios con la relación en cero SIGUEN en
  cero: hay que volver a ponerles Id_Cli_Relac = 2. Ese backfill NO se hizo — está a la
  espera del OK del departamento de Socios, que además tiene que decidir si lo cobrado de
  más se refactura o se acredita en cuenta corriente. La lista caso por caso está en
  scripts/xsys_informe_relacion_conyuge_2026-09-09.csv (columna estado = 'pendiente').

  Tampoco corrige los cupones ya emitidos: el precio se congela en Cbtes_Items al generar
  el lote. Y si el lote de noviembre sale antes del backfill, los 40 pendientes más los 12
  armados vuelven a facturar mal.

  Queda además un universo mayor sin tocar: 4.852 socios activos tienen Id_Cliente_Ref
  distinto de cero y la relación en cero. La mayoría no se puede clasificar con la
  auditoría actual y no entran en este recuento, porque no hay con qué probar qué relación
  tenían. Los 68 de este informe son los que dejaron rastro en la facturación.
======================================================================================*/

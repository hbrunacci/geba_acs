/*======================================================================================
  xSys - Los alumnos del IGSM entran por su categoría, como los docentes
  Fecha: 2026-09-14  — aplicado sobre xsys_geba

  Registro de lo que quedó en la base; este archivo no se ejecuta solo.

  --------------------------------------------------------------------------------------
  DE DÓNDE SALIÓ
  --------------------------------------------------------------------------------------
  MAGNIN ESTEFANIA (ficha 903005) no podía pasar por ningún molinete. Rechazo con
  motivo 112, "No cumple ninguna condición habilitante". No tenía que ver con el corte
  por cuota del 12/09: da ucp = 1 porque ALUMNO IGSM es Flag_Tipo = 'N' y quedó exenta.
  Sus rechazos venían del 02/09 y el 09/09, anteriores a ese cambio.

  La causa: `CD_Accesos_Cli_Tipos` tenía cargada la categoría DOCENTE IGSM (1135) en
  siete accesos, pero ALUMNO IGSM (1127) en NINGUNO. Los alumnos entraban por otra vía,
  el contrato INSTITUTO (tipo 55), con el motivo 204 "Habilit. por Tipo de Contrato".

  Medido al 14/09 sobre los 199 alumnos activos:

        80   con contrato INSTITUTO vigente   -> 65 pasaron en los últimos 60 días
       119   sin contrato, nunca tuvieron     ->  1 intentó pasar (ella)

  --------------------------------------------------------------------------------------
  QUÉ SE HIZO
  --------------------------------------------------------------------------------------
  Se copiaron las siete filas de DOCENTE IGSM para ALUMNO IGSM:

      INSERT INTO CD_Accesos_Cli_Tipos (Id_Acceso, Id_Tipo_Cli, Flag_Habilitado)
      SELECT  d.Id_Acceso, 1127, d.Flag_Habilitado
      FROM    CD_Accesos_Cli_Tipos d
      WHERE   d.Id_Tipo_Con = 1135
        AND   NOT EXISTS (SELECT 1 FROM CD_Accesos_Cli_Tipos x
                          WHERE x.Id_Acceso = d.Id_Acceso AND x.Id_Tipo_Cli = 1127)

  Accesos: 13 CS-20-AL, 14 SM-Alcorta, 15 SM-Ombues, 16 SM-Noble, 21 ROEL Mora,
  22 Acceso CS, 27 CONTROL MANUAL. Los mismos siete, ni uno más.

  Se copia `Flag_Habilitado` en vez de escribir un valor, porque la función lo mira:

      CF_SCA_ValidarTipo ... AND ISNULL(CACT.Flag_Habilitado, 'H') = 'H'

  En las filas de docentes está en NULL, que la función toma como habilitado. Inventar
  una 'S' habría dejado la categoría cargada y sin efecto.

  --------------------------------------------------------------------------------------
  VERIFICACIÓN
  --------------------------------------------------------------------------------------
  a) Antes del COMMIT, en transacción, `CF_SCA_ValidarTipo` sobre la ficha 903005:

        accesos 13, 14, 15, 16, 21, 22, 27 -> 1127   (habilita)
        acceso 4 (SanMartin_Alcorta)       -> 0      (sigue sin habilitar, como debe)

  b) Después del COMMIT, recálculo completo de la lista blanca (55.330 fichas, 37 s):

        devuelven acceso .... 167
        pierden acceso ......   0
        ALUMNO IGSM habilitados: 199 de 199 activos
        motivo: "Habilitado por categoría de socio" (205)

     Son 167 y no 119 porque entre los 80 que ya entraban por contrato había varios
     cuyo tipo de contrato no está mapeado al acceso 22, que es contra el que se
     calcula la lista blanca del facial.

  --------------------------------------------------------------------------------------
  LO QUE ESTO CAMBIA, Y QUEDA ASENTADO
  --------------------------------------------------------------------------------------
  Antes el acceso de un alumno caducaba solo: cuando el instituto cerraba el contrato
  INSTITUTO, dejaba de entrar. Ahora entra por la categoría, que no vence. Los 119 que
  nunca tuvieron contrato —fichas cargadas entre 2023 y 2026 que no volvieron a
  matricularse— quedan habilitados igual.

  Para que la lista no se ensucie con el tiempo, la baja del alumno tiene que pasar por
  `Clientes.Activo = 0` o por el cambio de categoría. El contrato ya no alcanza.

  --------------------------------------------------------------------------------------
  CÓMO VOLVER ATRÁS
  --------------------------------------------------------------------------------------
      DELETE FROM CD_Accesos_Cli_Tipos WHERE Id_Tipo_Cli = 1127

  Y después recalcular la lista blanca, o el facial sigue con la lista vieja:

      docker compose exec web python manage.py xsys_whitelist_full

======================================================================================*/


-- ------------------------------------------------------------------------------------
-- CONTROL: en qué accesos está habilitada cada categoría del instituto
-- ------------------------------------------------------------------------------------
SELECT  a.Id_Tipo_Cli, categoria = RTRIM(t.Descripcion),
        a.Id_Acceso, acceso = RTRIM(ac.Descripcion), a.Flag_Habilitado
FROM    CD_Accesos_Cli_Tipos a
        LEFT JOIN CD_Accesos ac ON ac.Id_Acceso = a.Id_Acceso
        LEFT JOIN Clientes_Tipos t ON t.Id_Tipo_Cli = a.Id_Tipo_Cli
WHERE   a.Id_Tipo_Cli IN (1127, 1135)
ORDER BY a.Id_Tipo_Cli, a.Id_Acceso


-- ------------------------------------------------------------------------------------
-- CONTROL: alumnos activos, con y sin contrato INSTITUTO vigente
-- (los que no lo tienen ahora entran igual; sirve para que el instituto revise
--  si alguno debería estar dado de baja)
-- ------------------------------------------------------------------------------------
SELECT  c.Id_Cliente, c.Doc_Nro,
        quien = RTRIM(ISNULL(c.Apellido,'')) + ', ' + RTRIM(ISNULL(c.Nombre,'')),
        c.Fecha_Alta,
        contrato_vigente = CASE WHEN EXISTS (
            SELECT 1 FROM Contratos o
            WHERE o.Id_Cliente = c.Id_Cliente AND o.Id_Tipo_Con = 55
              AND ISNULL(o.Activo,1) = 1 AND o.Fecha_Baja IS NULL) THEN 'si' ELSE 'NO' END,
        ultimo_paso = (SELECT MAX(e.Fecha) FROM CD_ES e WHERE e.Id_Cliente = c.Id_Cliente)
FROM    Clientes c
WHERE   c.Activo = 1 AND c.Id_Tipo_Cli = 1127
ORDER BY contrato_vigente, c.Fecha_Alta

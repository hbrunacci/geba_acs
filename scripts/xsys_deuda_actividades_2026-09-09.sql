/*======================================================================================
  xSys - Liberación automática de los bloqueados por deuda de actividades
  Fecha: 2026-09-09

  --------------------------------------------------------------------------------------
  EL AGUJERO
  --------------------------------------------------------------------------------------
  El bloqueo por deuda de actividades vive en `CD_Clientes_Deuda_Actividades`, que se
  cargó a mano desde una planilla el 02/09/2026 (163 filas, todas con
  Origen = 'planilla_20260825'). `CP_SCA_RegistrarAcceso` la lee y frena en el molinete
  a los que tienen Bloquea = 1, con el motivo 118 "Deuda de Actividades".

  Se revisó la base entera buscando qué escribe esa tabla:

      SELECT o.name, o.type_desc
      FROM sys.sql_modules m JOIN sys.objects o ON o.object_id = m.object_id
      WHERE m.definition LIKE '%CD_Clientes_Deuda_Actividades%'

  Devuelve UN solo objeto: `CP_SCA_RegistrarAcceso`, y sólo la lee. No hay trigger, no
  hay stored procedure, no hay job. **Pagar no desbloqueaba a nadie.** El socio
  regularizaba en Tesorería, su cuenta corriente quedaba en cero, y el molinete lo
  seguía frenando hasta que alguien se acordara de editar la fila a mano.

  Medido el 09/09/2026, siete días después de la carga:

      bloqueados (Bloquea=1) ..... 116   de los cuales 20 ya no debían nada
      avisados   (Bloquea=0) .....  47
      ------------------------------------------------------------------
      liberables hoy .............  20   (deuda de actividades vencida = 0)
      bajan de bloqueo a aviso ...  15   (pagaron parte: de 4 cuotas a 1-3)
      siguen bloqueados ..........  85

  --------------------------------------------------------------------------------------
  QUÉ SE HIZO
  --------------------------------------------------------------------------------------
  Un proceso propio, `xsys_deuda_actividades_revisar`, en el contenedor
  `deuda-actividades`. Cada 10 minutos consulta EN VIVO la cuenta corriente de las ~163
  personas de la tabla y afloja al que corresponde.

  Es un proceso aparte de la foto de deuda del tablero a propósito. La foto recorre la
  cuenta corriente entera —338.000 comprobantes, unos 5 minutos— y corre 4 veces por
  día. Para dejar entrar a alguien que acaba de pagar eso es caro y lento a la vez. Esta
  consulta filtra por `Id_Cliente IN (...)` sobre 163 personas y vuelve en **0,14 s**.

  Criterio, el mismo que ya venía usando el club (4 cuotas frena, 2 y 3 avisan):

      0 cuotas vencidas    -> Activo = 0, Fecha_Baja = GETDATE(). Sale de la tabla.
      1 a 3 cuotas         -> Bloquea = 0. Pasa, el visor lo marca en amarillo.
      4 o más              -> sin cambios.

  NUNCA bloquea a nadie nuevo ni sube a nadie de aviso a bloqueo. Sólo afloja. Liberar a
  quien pagó corrige un error del sistema; frenar a alguien es una decisión del club, y
  ésa sigue viniendo por la planilla.

  Sólo cuenta lo VENCIDO (`CC.Fecha < primero del mes que viene`). El club factura por
  adelantado, así que el cupón del mes siguiente ya existe impago: bloquear por eso
  sería frenar a alguien por una cuota que todavía no venció.

  Qué cuenta como "deuda de actividades" sale de `xsys.services.tableros`
  (TIPOS_CON_ACTIVIDADES), el mismo mapeo que usa el tablero. Si el tablero dijera que
  alguien no debe y el molinete lo siguiera frenando, uno de los dos tendría razón y no
  se podría saber cuál.

  --------------------------------------------------------------------------------------
  PERMISO OTORGADO A geba_acs
  --------------------------------------------------------------------------------------
  El usuario de la aplicación es de sólo lectura salvo excepciones acotadas. Para esto
  necesitaba escribir, y se le dio lo mínimo: cuatro columnas de una tabla.

      GRANT UPDATE ON dbo.CD_Clientes_Deuda_Actividades
            (Activo, Bloquea, Fecha_Baja, Observacion)
      TO [geba_acs]

  Verificado después de otorgarlo: el UPDATE sobre esas columnas pasa, y sobre `Cuotas`
  e `Importe` sigue denegado. O sea que el proceso puede liberar a alguien pero no puede
  alterar cuánto se dice que debe.

  Para revocarlo:

      REVOKE UPDATE ON dbo.CD_Clientes_Deuda_Actividades
             (Activo, Bloquea, Fecha_Baja, Observacion)
      FROM [geba_acs]

  --------------------------------------------------------------------------------------
  CÓMO VOLVER ATRÁS
  --------------------------------------------------------------------------------------
  El proceso deja huella en cada fila que toca: `Observacion` arranca con "auto:" y
  `Fecha_Baja` queda cargada. Para deshacer una corrida:

      UPDATE CD_Clientes_Deuda_Actividades
         SET Activo = 1, Bloquea = 1, Fecha_Baja = NULL
       WHERE Observacion LIKE 'auto:%'
         AND CONVERT(DATE, Fecha_Baja) = '2026-09-09'

  (Ojo: eso vuelve a bloquear también a los que bajaron a aviso. Si sólo se quiere
   deshacer las liberaciones, agregar AND Activo = 0.)

  Y para apagar el automatismo sin tocar la base:  docker compose stop deuda-actividades

======================================================================================*/


-- ------------------------------------------------------------------------------------
-- CONTROL: cómo está la tabla hoy
-- ------------------------------------------------------------------------------------
SELECT  bloquea = Bloquea,
        activo  = Activo,
        personas = COUNT(*),
        automaticas = SUM(CASE WHEN Observacion LIKE 'auto:%' THEN 1 ELSE 0 END)
FROM    CD_Clientes_Deuda_Actividades
GROUP BY Bloquea, Activo
ORDER BY 1, 2


-- ------------------------------------------------------------------------------------
-- CONTROL: quiénes siguen bloqueados y cuánto deben de verdad hoy
-- (es la misma cuenta que hace el proceso; sirve para auditarlo a mano)
-- ------------------------------------------------------------------------------------
SELECT  D.Id_Cliente,
        quien = RTRIM(ISNULL(C.Apellido, '')) + ', ' + RTRIM(ISNULL(C.Nombre, '')),
        cuotas_planilla = D.Cuotas,
        cuotas_hoy = ISNULL(V.cuotas, 0),
        importe_hoy = ISNULL(V.importe, 0),
        D.Observacion
FROM    CD_Clientes_Deuda_Actividades D
        LEFT JOIN Clientes C ON C.Id_Cliente = D.Id_Cliente
        LEFT JOIN (
            SELECT  CC.Id_Cliente, cuotas = COUNT(*), importe = SUM(CC.Saldo)
            FROM    Clientes_CtaCte CC
                    JOIN Cbtes B     ON B.Id_Trans = CC.Id_Trans
                    JOIN Contratos O ON O.Id_Contrato = B.Id_Contrato AND B.Id_Contrato > 0
            WHERE   CC.Importe > 0 AND CC.Saldo > 0
              AND   B.Id_Tipo_Cbte <> 'CPRO'
              AND   O.Id_Tipo_Con IN (3,9,12,22,24,25,26,27,28,29,31,36,38,41,46,56)
              AND   CC.Fecha < DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()) + 1, 0)
            GROUP BY CC.Id_Cliente
        ) V ON V.Id_Cliente = D.Id_Cliente
WHERE   D.Activo = 1 AND D.Bloquea = 1
ORDER BY ISNULL(V.cuotas, 0), quien

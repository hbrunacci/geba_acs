/*======================================================================================
  xSys - La deuda de actividades que frena en el molinete cuenta sólo el ejercicio
  Fecha: 2026-09-15  — cambio de aplicación, sin DDL sobre xSys

  Continúa scripts/xsys_deuda_actividades_2026-09-09.sql

  --------------------------------------------------------------------------------------
  DE DÓNDE SALIÓ
  --------------------------------------------------------------------------------------
  MEDRANO GUTIERREZ ESTEBAN (ficha 896498, socio 895910) no podía entrar. Debía cuatro
  cuotas de hockey sobre ruedas, justo el umbral de bloqueo:

        01/05/2024   $ 18.400    <- suelta, de dos años antes
        01/07/2026   $ 67.200
        01/08/2026   $ 68.500
        01/09/2026   $ 69.900

  El cupón de 2024 era el cuarto. Sin él quedaba en tres, que es aviso y no bloqueo.

  --------------------------------------------------------------------------------------
  LA REGLA NUEVA
  --------------------------------------------------------------------------------------
  Para el umbral de bloqueo sólo cuentan los cupones de actividades del ejercicio en
  curso. La ventana pasa a ser:

        [ XSYS_DEUDA_ACT_DESDE , primero del mes que viene )

  El piso es nuevo; el techo ya estaba (el club factura por adelantado y el cupón del mes
  siguiente no vencio todavía).

  El piso es una FECHA FIJA, no "el año en curso", y es a propósito: con el año en curso,
  cada 1 de enero la deuda de diciembre dejaría de contar y se liberaría a todo el mundo
  de golpe. Al empezar el ejercicio siguiente hay que correrla a mano, y se puede hacer
  por variable de entorno sin desplegar:

        XSYS_DEUDA_ACT_DESDE=2027-01-01

  Lo que NO cambia: el umbral sigue en 4 cuotas (4 frena, 1 a 3 avisan), el proceso sigue
  sin bloquear a nadie nuevo, y la deuda vieja sigue existiendo en la cuenta corriente y
  en el tablero. Lo único que cambia es qué cuenta para frenar en el molinete.

  --------------------------------------------------------------------------------------
  QUÉ SE TOCÓ
  --------------------------------------------------------------------------------------
        acs/settings.py                        XSYS_DEUDA_ACT_DESDE = "2026-01-01"
        xsys/services/deuda_actividades.py     desde_ejercicio() + piso en la consulta
        xsys/management/commands/...revisar.py el log dice desde cuándo cuenta
        xsys/tests/test_deuda_actividades_revisar.py   29 pruebas, en verde

  La marca que queda en la Observación ahora aclara el ejercicio —"quedan 3 cuota(s) de
  2026 vencida(s)"— porque el socio puede seguir debiendo cupones viejos y la fila no
  tiene que dar a entender que no debe nada.

  --------------------------------------------------------------------------------------
  EFECTO MEDIDO (15/09/2026, sobre las 139 filas activas)
  --------------------------------------------------------------------------------------
        22   quedan en 0 cuotas del ejercicio   -> se liberan, salen de la tabla
        13   bajan de 4 a 1-3 cuotas            -> pasan a aviso
        ---
        35   filas actualizadas en la corrida

  Recálculo de la lista blanca posterior: 17 recuperan el acceso, ninguno lo pierde.
  Bloqueados por deuda de actividades: de 76 a 59.

  MEDRANO GUTIERREZ quedó en aviso, con 3 cuotas de 2026 por $ 205.600.

  --------------------------------------------------------------------------------------
  CÓMO VOLVER ATRÁS
  --------------------------------------------------------------------------------------
  El cambio es de aplicación. Para que vuelva a contar toda la historia alcanza con
  correr el piso a una fecha vieja y reiniciar el proceso:

        XSYS_DEUDA_ACT_DESDE=1900-01-01
        docker compose restart deuda-actividades

  Eso NO vuelve a bloquear solo a los 35: el proceso nunca sube a nadie de aviso a
  bloqueo. Para reponer el bloqueo hay que editar las filas, que quedaron marcadas:

        UPDATE CD_Clientes_Deuda_Actividades
           SET Activo = 1, Bloquea = 1, Fecha_Baja = NULL
         WHERE Observacion LIKE '%auto:%15/09/2026%'

======================================================================================*/


-- ------------------------------------------------------------------------------------
-- CONTROL: cómo quedó la tabla
-- ------------------------------------------------------------------------------------
SELECT  bloquea = Bloquea, activo = Activo, personas = COUNT(*),
        automaticas = SUM(CASE WHEN Observacion LIKE '%auto:%' THEN 1 ELSE 0 END)
FROM    CD_Clientes_Deuda_Actividades
GROUP BY Bloquea, Activo
ORDER BY 1, 2


-- ------------------------------------------------------------------------------------
-- CONTROL: deuda de actividades por socio, separando el ejercicio de lo viejo
-- (es la misma cuenta que hace el proceso; sirve para auditarlo a mano)
-- ------------------------------------------------------------------------------------
SELECT  D.Id_Cliente,
        quien = RTRIM(ISNULL(C.Apellido, '')) + ', ' + RTRIM(ISNULL(C.Nombre, '')),
        D.Bloquea,
        cuotas_2026  = ISNULL(V.cuotas_2026, 0),
        importe_2026 = ISNULL(V.importe_2026, 0),
        cuotas_viejas = ISNULL(V.cuotas_viejas, 0),
        importe_viejo = ISNULL(V.importe_viejo, 0)
FROM    CD_Clientes_Deuda_Actividades D
        LEFT JOIN Clientes C ON C.Id_Cliente = D.Id_Cliente
        LEFT JOIN (
            SELECT  CC.Id_Cliente,
                    cuotas_2026   = SUM(CASE WHEN CC.Fecha >= '2026-01-01' THEN 1 ELSE 0 END),
                    importe_2026  = SUM(CASE WHEN CC.Fecha >= '2026-01-01' THEN CC.Saldo ELSE 0 END),
                    cuotas_viejas = SUM(CASE WHEN CC.Fecha <  '2026-01-01' THEN 1 ELSE 0 END),
                    importe_viejo = SUM(CASE WHEN CC.Fecha <  '2026-01-01' THEN CC.Saldo ELSE 0 END)
            FROM    Clientes_CtaCte CC
                    JOIN Cbtes B     ON B.Id_Trans = CC.Id_Trans
                    JOIN Contratos O ON O.Id_Contrato = B.Id_Contrato AND B.Id_Contrato > 0
            WHERE   CC.Importe > 0 AND CC.Saldo > 0
              AND   B.Id_Tipo_Cbte <> 'CPRO'
              AND   O.Id_Tipo_Con IN (3,9,12,22,24,25,26,27,28,29,31,36,38,41,46,56)
              AND   CC.Fecha < DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()) + 1, 0)
            GROUP BY CC.Id_Cliente
        ) V ON V.Id_Cliente = D.Id_Cliente
WHERE   D.Activo = 1
ORDER BY cuotas_viejas DESC, cuotas_2026 DESC

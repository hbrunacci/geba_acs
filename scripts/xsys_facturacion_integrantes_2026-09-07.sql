/*======================================================================================
  xSys - Facturación de cuota social: los integrantes del grupo familiar dejaron de
         facturarse a partir del período OCTUBRE 2026.

  Fecha del análisis : 2026-09-07
  Disparador         : el socio 569449 FERRO MIGUEL ANGEL no recibió la cuota de su
                       hija ABRAMOVITSCH FERRO IRENA PAZ (901146) en octubre, y por lo
                       tanto tampoco se la cobró el débito automático.

  ESTE ARCHIVO NO SE EJECUTÓ. Es el registro del diagnóstico y la corrección propuesta.
  Todas las consultas del análisis fueron de sólo lectura.

  --------------------------------------------------------------------------------------
  1. QUÉ PASÓ
  --------------------------------------------------------------------------------------
  La cuota de cada integrante del grupo familiar NO la genera el lote de facturación.
  El lote (CPJ_Cbtes_Lotes_Facturar_V2 -> CPJ_Cbtes_Lotes_Facturar_V2_) sólo emite el
  ítem del TITULAR, tomándolo de Contratos_Prod con Flag_Facturable = 1.

  Los ítems de los integrantes los agrega el trigger tri_Cbtes_Items sobre Cbtes_Items,
  que llama a SP_Cbtes_Items_Integrantes. Ese SP explota el grupo familiar
  (Clientes.Id_Cliente_Ref = titular) y le inserta una línea 'CS' a cada integrante con
  el precio de dbo.SF_CS_Precio_Persona().

  El 2026-08-05 15:36 se modificó SP_Cbtes_Items_Integrantes agregando la sección 3.2:

        -- 3.2 Excluye Cuota Voluntaria (Contrato Tipo 37) y Vitalicios +71 (1010)
        IF EXISTS (SELECT 1 FROM Contratos
                   WHERE Id_Cliente = @Id_Cliente AND Id_Tipo_Con = 37 AND Activo = 1
                     AND Fecha_Desde <= @Fecha_QA
                     AND (Fecha_Hasta >= @Fecha_QA OR Fecha_Hasta IS NULL))
            RETURN                              -- <== sale del SP entero

        IF EXISTS (SELECT 1 FROM Clientes
                   WHERE Id_Cliente = @Id_Cliente AND Id_Tipo_Cli = 1010)
            RETURN                              -- <== sale del SP entero

  La intención era no cobrarle la cuota AL TITULAR cuando es vitalicio +71 o cuando
  tiene cuota social voluntaria. Pero el RETURN corta el procedimiento completo, y las
  líneas de los integrantes se generan MÁS ABAJO (pasos 7 a 9). Resultado: cuando el
  titular cae en cualquiera de las dos condiciones, TODO el grupo familiar se queda sin
  cuota, no sólo el titular.

  Comparación contra la versión anterior (zSP_Cbtes_Items_Integrantes20251103): la
  sección 3.1/3.2 es el único cambio funcional. Todo lo demás son PRINTs de debug.

  --------------------------------------------------------------------------------------
  2. POR QUÉ APARECE RECIÉN EN OCTUBRE
  --------------------------------------------------------------------------------------
  Los lotes de cuota social se generan con más de un mes de anticipación:

        CSOC_2026-09   generado el 2026-07-27 22:15   -> ANTES del cambio  -> OK
        <cambio del SP:            2026-08-05 15:36>
        CSOC_2026-10   generado el 2026-08-24 22:25   -> DESPUÉS           -> falla

  El lote de NOVIEMBRE todavía no se generó (al 2026-09-07 no hay fila en Cbtes_Lotes).
  Si el SP no se corrige antes, noviembre va a repetir el error.

  --------------------------------------------------------------------------------------
  3. EL CASO FERRO
  --------------------------------------------------------------------------------------
  Grupo familiar del titular 569449 FERRO MIGUEL ANGEL:
      569449  FERRO MIGUEL ANGEL              VITALICIO +71 (1010)   titular
      541521  ORTIZ SUSANA                    VITALICIO +71 (1010)   integrante
      901146  ABRAMOVITSCH FERRO IRENA PAZ    CADETE (1001)          integrante

  El titular dispara LAS DOS guardas:
      - Id_Tipo_Cli = 1010 (VITALICIO + 71)
      - contrato 628591, Id_Tipo_Con = 37 (CUOTA SOCIAL VOLUNTARIA / PROFORMA),
        activo desde 2021-03-02, sin fecha de fin.

  CUP 1559017 - período 2026-09 (Id_Trans 6462458), total 143.200,00
      1  569449  CS    CUOTA SOCIAL                 0,00
      2  569449  5542  JN ROP.MAD.M.PB 0362    21.300,00
      3  541521  5612  JN ROP.MET.PREF. F PB   19.200,00
      4  541521  CS    CUOTA SOCIAL                 0,00
      5  901146  CS    CUOTA SOCIAL           102.700,00   <== la cuota de Irena

  CUP 1568096 - período 2026-10 (Id_Trans 6521321), total 41.400,00
      1  569449  CS    CUOTA SOCIAL                 0,00
      2  569449  5542  JN ROP.MAD.M.PB 0362    21.800,00
      3  541521  5612  JN ROP.MET.PREF. F PB   19.600,00
                                                            <== faltan los ítems 4 y 5

  El contrato de Irena (701661, CUOTA SOCIAL, activo desde 2023-06-15) está impecable:
  sin fecha de baja, sin Fecha_Cob_Hasta, ficha activa, categoría CADETE. El precio que
  le correspondía en octubre es 105.900,00 (dbo.SF_CS_Precio_Persona(901146,'20261001')).

  Aclaración: que Irena tenga Contratos_Prod.Flag_Facturable = 0 es lo NORMAL para un
  integrante. Los integrantes nunca entran por el lote; entran por este SP.

  --------------------------------------------------------------------------------------
  4. ALCANCE
  --------------------------------------------------------------------------------------
  Integrantes activos cuyo titular dispara alguna guarda:  405
        388  las dos guardas
         17  sólo vitalicio 1010

  De esos, los que SÍ tenían cuota con importe en septiembre y NO tienen nada en
  octubre:  34 socios, 3.464.940,00 a precio de octubre.

  Total de ítems CS del período:  septiembre 15.032 / octubre 14.821.
  El total facturado igual subió (1.550 M -> 1.580 M) por el aumento de la cuota, así
  que el faltante no se ve en el acumulado.

  --------------------------------------------------------------------------------------
  5. LOS 16 TITULARES QUE TAMBIÉN "SE CAYERON": REVISADOS UNO POR UNO, TODOS LEGÍTIMOS
  --------------------------------------------------------------------------------------
  Ninguno de estos es un error. Se revisaron los 16 y cada uno tiene su motivo:

  (a) Cumplieron 71 años — 8 socios, todos nacidos en septiembre de 1955.
      268892 CLARAMUNT, 269201 HABIB, 269708 RAFFAELLI, 269945 SUAREZ MARCELA,
      300741 COLLAZO, 304097 GIL, 502930 NALVANTI, 632515 GROISMAN.
      La ficha les sigue diciendo VITALICIO 66/70 (1070), pero dbo.SF_Prod resuelve el
      producto por EDAD a la fecha de facturación: hasta septiembre devuelve 1269 y
      desde octubre 1270 (vitalicio +71), que es exento. Cinco de ellos tienen igual su
      cupón de octubre con la línea de cuota en 0,00.
      Lo mismo 626230 CORREA, que pasó a VITALICIO + 71 (1010) el 2026-08-20.

  (b) Licencia sin acceso con 100% de bonificación (contrato tipo 48), cargadas por el
      usuario 146:
          877209 BELOTTO   contrato 758546   01/07/2026 al 31/12/2026   alta 28/07
          909696 AYALA     contrato 758689   01/08/2026 al 31/01/2027   alta 05/08
          886285 LESCANO   contrato 758795   01/09/2026 al 28/02/2027   alta 10/08
      Sus cupones de agosto y septiembre quedaron anulados (estado 3).

  (c) 603124 CASABAL — contrato 667085 DESCUENTO POR DISCAPACIDAD, 100%, 2021 a 2036.
      (d) 890968 LUCCHETTI — su propio contrato de cuota social tiene Porc_Bonif = 100.
      En ambos el cupón da cero y el lote no lo emite (@pIncl_Cbtes_Cero = 0).

  (e) 731644 SANTIAGO SUSANA — NO le falta la cuota: el cupón 1566774 del lote
      CSOC_2026-10 se emitió el 05/09 por 171.800,00 (cuota 150.000 + cochera), pero con
      Periodo = 2026-09 en vez de 2026-10, y por eso no aparecía en los listados por
      período. Es uno de sólo 2 casos así en el lote de octubre (en el de septiembre
      hubo 31). Vale la pena mirarlo, pero es una anomalía de sellado de período, no un
      faltante de facturación.

  (f) 918954 CHEDIEK — su contrato de cuota social venció el 2025-12-31 y nunca se
      renovó, así que el lote lo excluye correctamente. Se le viene facturando a mano
      (cupones 1566494 y 1566495, sin lote). Esto es para que Socios renueve el contrato.

  Otros 5 casos que aparecían como "caídos" son altas posteriores al 2026-08-24 (fecha en
  que se generó el lote de octubre) y por lo tanto son normales: 947544 MORA,
  947515 BUGALLO, 947029 GIULIANETTI, 947025 MARTINEZ PERES, 900409 LIPSHITZ.

  CONCLUSIÓN: el único defecto real es el del punto 1. Los 34 integrantes del punto 4 son
  todo el daño.

======================================================================================*/


/*--------------------------------------------------------------------------------------
  A. VERIFICACIÓN — reproduce el diagnóstico sin tocar nada
--------------------------------------------------------------------------------------*/

-- A.1  Las dos guardas sobre el titular de Irena
SELECT  C.Id_Cliente, C.Id_Tipo_Cli,
        guarda_1010 = CASE WHEN C.Id_Tipo_Cli = 1010 THEN 'dispara' ELSE 'no' END,
        guarda_37   = CASE WHEN EXISTS (SELECT 1 FROM Contratos O
                                        WHERE O.Id_Cliente = C.Id_Cliente
                                          AND O.Id_Tipo_Con = 37 AND O.Activo = 1
                                          AND O.Fecha_Desde <= '20261001'
                                          AND (O.Fecha_Hasta >= '20261001' OR O.Fecha_Hasta IS NULL))
                           THEN 'dispara' ELSE 'no' END
FROM    Clientes C
WHERE   C.Id_Cliente = 569449

-- A.2  Los ítems de los dos cupones, lado a lado
SELECT  periodo = CONVERT(CHAR(7), B.Periodo, 120), I.Item, I.Id_Cliente,
        quien = RTRIM(ISNULL(C.Apellido,'')) + ' ' + RTRIM(ISNULL(C.Nombre,'')),
        I.Id_Producto, I.Imp_Final
FROM    Cbtes B
        JOIN Cbtes_Items I ON I.Id_Trans = B.Id_Trans
        LEFT JOIN Clientes C ON C.Id_Cliente = I.Id_Cliente
WHERE   B.Id_Trans IN (6462458, 6521321)
ORDER BY B.Periodo, I.Item

-- A.3  Los 34 integrantes sin cuota en octubre, con lo que habría que facturarles
SELECT  C.Id_Cliente, C.Doc_Nro,
        quien = RTRIM(ISNULL(C.Apellido,'')) + ' ' + RTRIM(ISNULL(C.Nombre,'')),
        categoria = T.Descripcion,
        titular = C.Id_Cliente_Ref,
        quien_titular = RTRIM(ISNULL(R.Apellido,'')) + ' ' + RTRIM(ISNULL(R.Nombre,'')),
        precio_octubre = dbo.SF_CS_Precio_Persona(C.Id_Cliente, '20261001')
FROM    Clientes C
        JOIN Clientes R ON R.Id_Cliente = C.Id_Cliente_Ref
        LEFT JOIN Clientes_Tipos T ON T.Id_Tipo_Cli = C.Id_Tipo_Cli
WHERE   C.Activo = 1 AND C.Tipo_Persona = 'F' AND ISNULL(C.Id_Cliente_Ref,0) <> 0
  AND ( R.Id_Tipo_Cli = 1010
        OR EXISTS (SELECT 1 FROM Contratos O
                   WHERE O.Id_Cliente = R.Id_Cliente AND O.Id_Tipo_Con = 37 AND O.Activo = 1
                     AND O.Fecha_Desde <= '20261001'
                     AND (O.Fecha_Hasta >= '20261001' OR O.Fecha_Hasta IS NULL)) )
  AND EXISTS     (SELECT 1 FROM Cbtes_Items I JOIN Cbtes B ON B.Id_Trans = I.Id_Trans
                  WHERE I.Id_Cliente = C.Id_Cliente AND I.Id_Producto = 'CS'
                    AND B.Id_Tipo_Cbte = 'CUP'
                    AND B.Periodo >= '20260901' AND B.Periodo < '20261001'
                    AND I.Imp_Final > 0)
  AND NOT EXISTS (SELECT 1 FROM Cbtes_Items I JOIN Cbtes B ON B.Id_Trans = I.Id_Trans
                  WHERE I.Id_Cliente = C.Id_Cliente AND I.Id_Producto = 'CS'
                    AND B.Id_Tipo_Cbte = 'CUP'
                    AND B.Periodo >= '20261001' AND B.Periodo < '20261101')
ORDER BY precio_octubre DESC


/*--------------------------------------------------------------------------------------
  B. CORRECCIÓN PROPUESTA — respeta la intención del cambio del 05/08 sin voltear al
     grupo familiar: se excluye al TITULAR de que se le pise el precio, y se siguen
     generando las líneas de los integrantes.

     NO EJECUTAR sin coordinar con quien mantiene el ERP. Antes de aplicar:

         SELECT OBJECT_DEFINITION(OBJECT_ID('SP_Cbtes_Items_Integrantes'))
         -- y guardar como zSP_Cbtes_Items_Integrantes_20260907
--------------------------------------------------------------------------------------*/

/*
  En vez de las dos guardas con RETURN de la sección 3.2, declarar una variable:

        DECLARE @Excluir_Titular BIT = 0

        -- 3.2 El titular no paga cuota si tiene Cuota Voluntaria (tipo 37) o es
        --     Vitalicio +71 (1010). Los integrantes SÍ siguen facturándose.
        IF EXISTS (SELECT 1 FROM Contratos
                   WHERE Id_Cliente = @Id_Cliente AND Id_Tipo_Con = 37 AND Activo = 1
                     AND Fecha_Desde <= @Fecha_QA
                     AND (Fecha_Hasta >= @Fecha_QA OR Fecha_Hasta IS NULL))
            SET @Excluir_Titular = 1

        IF EXISTS (SELECT 1 FROM Clientes
                   WHERE Id_Cliente = @Id_Cliente AND Id_Tipo_Cli = 1010)
            SET @Excluir_Titular = 1

  y usarla en los dos únicos lugares donde se toca el ítem del titular:

     -- paso 6, rama "sin integrantes"
        IF @Cant_Integrantes = 0
        BEGIN
            IF @Excluir_Titular = 0
                UPDATE Cbtes_Items
                   SET Precio_Grav = @Precio_CS_Titular, Imp_Gravado = @Precio_CS_Titular,
                       Imp_Final   = @Precio_CS_Titular, Precio      = @Precio_CS_Titular
                 WHERE Id_Trans = @trans AND Item = @Item
            RETURN
        END

     -- paso 10, actualización del ítem del titular
        IF @Excluir_Titular = 0
            UPDATE Cbtes_Items
               SET Precio_Grav = @Precio_CS_Titular, Imp_Gravado = @Precio_CS_Titular,
                   Imp_Final   = @Precio_CS_Titular, Precio      = @Precio_CS_Titular
             WHERE Id_Trans = @trans AND Item = @Item

  El resto del procedimiento queda igual.
*/


/*--------------------------------------------------------------------------------------
  C. QUÉ HACER CON OCTUBRE
--------------------------------------------------------------------------------------*/
/*
  Los cupones de octubre ya están en estado 2 (COMPLETO) y con saldo 0, o sea cerrados y
  ya enviados al débito. No se los puede completar agregando ítems: hay que decidir por
  administración entre

     (a) emitir una nota de débito por la cuota de octubre a cada uno de los 34, o
     (b) sumar el importe al cupón de noviembre como concepto aparte, o
     (c) refacturar el lote de octubre (CPJ_Cbtes_Lotes_Facturar_V2 con @pRefacturar = 1)
         para los contratos afectados — la opción más invasiva y la que menos recomiendo
         con el débito ya presentado.

  Sea cual sea, el SP tiene que corregirse ANTES de que se genere el lote de noviembre
  (por la cadencia de los lotes anteriores, alrededor del 24 al 28 de septiembre).
*/

/*======================================================================================
  xSys - Alta en `Clientes` de 10 empleados que estaban sólo en la tabla `Empleados`.
  Fecha: 2026-09-08

  Disparador: se preguntó por Vicky Pérez (DNI 42541555), que no podía pasar los
  molinetes. Estaba cargada, pero en `Empleados` —el legajo de personal del ERP— y no
  en `Clientes`. Los lectores identifican contra `Clientes` (dbo.CF_SCA_IdentifIdCliente
  busca por Doc_Nro y, como alternativa, por Credencial_Nro): para el molinete ese
  documento no correspondía a nadie y devolvía el motivo 103 "La Persona es inválida".

  Al revisarlo apareció que no era sólo ella: de los 18 empleados de esa tabla con
  documento cargado, 12 no tenían ficha activa. Diez de ellos no tenían ficha en
  absoluto y son los que se dieron de alta acá.

  --------------------------------------------------------------------------------------
  QUIÉNES
  --------------------------------------------------------------------------------------
      Ficha    Documento   Nombre                            Empleado Nº
      947553   35330020    BAZUALDO, MATIAS RICARDO              69
      947554   39419072    BENITEZ, BRIAN                        32
      947555   47382733    CACERES, CANDELA AYELEN               70
      947556   38733076    GOMEZ, LUCIA                          67
      947557   39226196    LOYZATE, ROMINA ANDREA                65
      947558   37216083    MACRI BLANCO, GABRIEL ALEJANDRO       60
      947559   42435143    ORTIZ, JONATHAN ALEJANDRO             73
      947560   42541555    PEREZ, VICTORIA                       66
      947561   40642075    SUAREZ, MARIA SOL                     68
      947562   35069771    TALAVAN, MARIA DEL PILAR              33

  SEGUNDA TANDA (mismo día, con los DNI que faltaban):

      947563   38663967    MARTIN, KAREN MICAELA                 57
          Estaba en `Empleados` con documento 1 (comodín), por eso no aparecía al
          buscarla por DNI. Había intentado entrar tres veces y las tres la rechazó:
          07/08 11:13 por SM-Noble y 28/08 13:08 por SM-Alcorta (dos intentos).
          Respaldo: zClientes_AltaEmpleados_20260908b

      899344   33556431    RODRIGUEZ, DENISE ANDREA
          NO fue un alta: su ficha ya existía, con el documento TRUNCADO —decía
          3355643, el mismo número sin el último dígito— y dada de baja el 25/06/2022
          a las 18:56, cincuenta y ocho minutos después de haberla creado. Nunca pasó
          por un molinete.
          Se corrigió por la vía de la aplicación (xsys.services.ficha), no con SQL
          suelto: son cuatro columnas dentro de las 38 que el usuario `geba_acs` puede
          actualizar, así que quedó auditado campo por campo en xsys_socio_edicion.
              Nº de documento    3355643    -> 33556431
              Activo             no         -> sí
              Fecha de baja      2022-06-25 -> (vacía)
              Motivo del estado  Baja       -> NO DEFINIDO / NO APLICABLE

  TERCERA TANDA (mismo día, dos nombres más que aportó el club):

      947564   33018238    LAGE, EZEQUIEL GUILLERMO              75
      947565   44830524    ZARATE, VALENTIN EMILIANO             71
          Los dos estaban en `Empleados` SIN documento (campo vacío), así que no
          aparecían en ninguna búsqueda por DNI y no tenían ficha en `Clientes`.
          Zarate venía siendo rechazado: cuatro intentos, los cuatro con motivo 103
          "La Persona es inválida" —11/06 20:31 por SM-Noble, 24/07 14:29 y 16:28 por
          SM-Alcorta y 31/07 12:25 por SM-Ombues—. De Lage no hay intentos registrados.
          En la misma transacción se les completó el `Documento_Nro` en `Empleados`,
          para que el legajo y la ficha coincidan.
          Respaldo: zClientes_AltaEmpleados_20260908c (y zEmpleados_20260908 para el
          legajo, que ya existía de la corrección de esa tabla).

      947566   28057175    ESCUDERO, XIMENA YAMILA               74
          Idéntico caso, un rato después: legajo 74 sin documento y sin ficha. Estaba
          intentando entrar ESE MISMO DÍA —dos rechazos por SM-Noble, 11:12 y 11:34,
          los dos con motivo 103—. Mismo procedimiento: ficha + documento en el legajo
          en una sola transacción.
          Respaldo: zClientes_AltaEmpleados_20260908d

      947567   42100802    GARCIA, MACARENA                     (sin legajo)
          ATENCIÓN, este caso es distinto a los 14 anteriores: NO tiene fila en la tabla
          `Empleados`. No está ni por nombre ni por documento ni por ninguna variante de
          GARCIA / MACARENA. El único respaldo de que es personal del club es el dato que
          pasó el club. También estaba rebotando: intento del 08/09 11:43 por SM-Alcorta,
          motivo 103.
          Se le creó la ficha porque el club lo pidió, y la `Observacion` deja constancia
          de que no viene del legajo. NO se le creó la fila en `Empleados`: inventar un
          legajo es inventar datos de RRHH, igual criterio que con Denise Rodríguez.
          Respaldo: zClientes_AltaEmpleados_20260908e

  Quedaron AFUERA, a propósito:
      GIMENEZ MAURO y MAIDANA CINTHIA — su documento en `Empleados` es 9999999, un
      comodín que además apunta a una ficha de baja ajena (la 35176). Sin el documento
      real no se puede dar de alta a nadie sin arriesgar que el molinete confunda
      personas, que es justo el problema que se viene arrastrando.
      Otros 58 registros de `Empleados` no tienen documento cargado.

  --------------------------------------------------------------------------------------
  CÓMO SE ARMÓ LA FICHA
  --------------------------------------------------------------------------------------
  Los valores salen de mirar las fichas de empleado ya existentes, no de inventar:

      Id_Tipo_Cli        1006  EMPLEADO
      Activo             1
      Tipo_Persona       'F'
      Id_Tipo_Doc        '1'   DNI
      Sexo               'N'   sin especificar (es el valor de 403 de los 427 empleados)
      Id_Iva             'CF'  (los 427 empleados activos lo tienen)
      Id_Lista_Precio    32    (409 de 427)
      Id_Motivo_Est      0     NO DEFINIDO
      Id_Cliente_Ref     0     sin grupo familiar
      Razon_Social       "APELLIDO NOMBRE"
      Observacion        deja la trazabilidad: de qué empleado de la tabla salió
      Fecha_Nac          NO se cargó: las de `Empleados` son basura (1970-01-01,
                         2000-01-01, y la de Pérez decía 2025-01-02)
      Ult_Cuota_Paga     NO se cargó. Un empleado no paga cuota, y ponerle una fecha
                         sería inventar un pago. Se verificó que no hace falta: la
                         categoría habilita 19 de los 20 accesos por sí sola.

  --------------------------------------------------------------------------------------
  EL NÚMERO DE SOCIO LO ASIGNÓ EL ERP (y salió raro, ver más abajo)
  --------------------------------------------------------------------------------------
  Se dejó `Id_Cliente_Externo` vacío para que lo asignara `tri_clientes` vía
  `dbo.SF_Nro_socio`, que para empleados hace:

      SELECT MAX(CONVERT(INT, ISNULL(Id_Cliente_Externo,'0'))) + 1
      FROM Clientes WHERE Id_Tipo_Cli IN (1005, 1006)

  Los tres empleados anteriores habían recibido 906542, 906543 y 906544. Estos diez
  recibieron 24549138 a 24549147, porque la ficha 916662 (MENDEZ LEONARDO, alta
  18/06/2025) tiene `Id_Cliente_Externo = 24549137` — le cargaron el DNI donde va el
  número de socio y eso corrió la serie entera.

  Se decidió NO elegir los números a mano: es exactamente lo que habría asignado el ERP
  si el alta la hacía Socios desde la aplicación, y numerar por fuera de su regla es
  peor que el síntoma. No afecta el acceso (los lectores no miran este campo).

  PENDIENTE PARA EL CLUB: corregirle el `Id_Cliente_Externo` a la ficha 916662 y volver
  la serie de empleados a 906545. Mientras no se haga, cada alta de empleado nueva va a
  seguir tomando números con pinta de DNI.

  --------------------------------------------------------------------------------------
  VERIFICACIÓN (dentro de la transacción, antes del COMMIT)
  --------------------------------------------------------------------------------------
  Para cada una de las diez:
      · dbo.CF_SCA_IdentifIdCliente(documento) devuelve SU ficha
      · dbo.CF_SCA_ValidarTipo devuelve 1006 en los 7 accesos de control
        (Aldao_Mitre, SanMartin_Alcorta, NEWBERY, SM-Alcorta, SM-Ombues, SM-Noble, CS)
      · Activo = 1 y categoría = 1006
  Si alguna fallaba, ROLLBACK y no quedaba nada. Pasaron las diez.

  Después del COMMIT:
      · espejo local actualizado (10 socios) y lista blanca recalculada:
        las diez quedaron "Habilitado por categoría de socio"
      · diagnóstico completo por documento: "Entra por 19 acceso(s). Categoría habilitante."

  Antes de esto se hizo un ensayo completo del INSERT con ROLLBACK, para ver qué dejaban
  los triggers (`tri_clientes` → `SP_Clientes_Validar`, `Tri_Clientes_VencCSAG`,
  `Tri_Clientes_VencCSE`, `CT_Clientes_Hist`). El alta NO generó novedades para los
  lectores faciales (CD_Clientes_Novedades quedó en 0 filas), consistente con que ese
  canal está cortado.

  --------------------------------------------------------------------------------------
  SUPUESTO QUE HAY QUE VALIDAR
  --------------------------------------------------------------------------------------
  Se dieron de alta porque figuran en la tabla `Empleados`, PERO esa tabla casi no usa
  el campo `Id_Estado_Empleado`: de los diez, sólo tres lo tienen cargado (BENITEZ y
  TALAVAN EFECTIVO, SUAREZ NO DEFINIDO) y los otros siete están en blanco. O sea que
  NO se pudo confirmar por sistema que las diez personas sigan trabajando en el club.
  Si alguna ya no está, hay que darle la baja: hoy tiene la puerta abierta.

  --------------------------------------------------------------------------------------
  VUELTA ATRÁS
  --------------------------------------------------------------------------------------
  La tabla zClientes_AltaEmpleados_20260908 guarda las diez fichas creadas.

      DELETE FROM Clientes
      WHERE Id_Cliente IN (SELECT Id_Cliente FROM zClientes_AltaEmpleados_20260908)

  (Borrar dispara los triggers de baja; si preferís algo más suave, alcanza con
   UPDATE Clientes SET Activo = 0, Fecha_Baja = GETDATE() sobre esos mismos ids.)

======================================================================================*/


-- ------------------------------------------------------------------------------------
-- EL INSERT QUE SE EJECUTÓ (uno por persona, dentro de una única transacción)
-- ------------------------------------------------------------------------------------
/*
    -- Id_Cliente = (SELECT MAX(Id_Cliente) + 1 FROM Clientes), recalculado en cada fila

    INSERT INTO Clientes (
        Id_Cliente, Doc_Nro, Id_Tipo_Doc, Apellido, Nombre, Razon_Social,
        Sexo, Tipo_Persona, Activo, Id_Tipo_Cli, Id_Cliente_Ref, Id_Motivo_Est,
        Fecha_Alta, Id_Iva, Id_Lista_Precio, Id_Cliente_Externo, Observacion
    ) VALUES (
        @nuevo, @documento, '1', @apellido, @nombre, @razon_social,
        'N', 'F', 1, 1006, 0, 0, GETDATE(), 'CF', 32, '',
        'Alta desde geba_acs 08/09/2026 - empleado NN de la tabla Empleados'
    )
*/


-- ------------------------------------------------------------------------------------
-- CONTROL: cómo quedaron
-- ------------------------------------------------------------------------------------
SELECT  C.Id_Cliente, C.Id_Cliente_Externo, C.Doc_Nro,
        quien = RTRIM(ISNULL(C.Apellido,'')) + ', ' + RTRIM(ISNULL(C.Nombre,'')),
        C.Activo, C.Id_Tipo_Cli, T.Descripcion,
        CONVERT(CHAR(19), C.Fecha_Alta, 120) AS alta,
        resuelve_el_lector = dbo.CF_SCA_IdentifIdCliente(CAST(C.Doc_Nro AS VARCHAR(20))),
        CAST(C.Observacion AS VARCHAR(120)) AS observacion
FROM    Clientes C
        LEFT JOIN Clientes_Tipos T ON T.Id_Tipo_Cli = C.Id_Tipo_Cli
WHERE   C.Id_Cliente IN (SELECT Id_Cliente FROM zClientes_AltaEmpleados_20260908)
ORDER BY C.Apellido


-- ------------------------------------------------------------------------------------
-- LO QUE QUEDA PENDIENTE
-- ------------------------------------------------------------------------------------
/*
   1. Conseguir el documento real de GIMENEZ MAURO y MAIDANA CINTHIA (hoy 9999999) y
      darles el alta igual que a los demás.

   1.b Corregir en la tabla `Empleados` el documento de MARTIN KAREN MICAELA
      (Id_Empleado 57), que figura como 1. Su DNI real es 38663967. No se tocó porque
      `Empleados` es tabla de RRHH y el acceso no la usa, pero mientras diga 1 esa
      persona va a seguir sin aparecer en cualquier búsqueda por documento.
      Lo mismo con RODRIGUEZ DIEGO (Id_Empleado 45), también con documento 1.

   2. Corregir el Id_Cliente_Externo de la ficha 916662 (MENDEZ LEONARDO) para
      destrabar la serie de números de empleado.

   3. Confirmar con RRHH que las diez personas siguen trabajando en el club.

   4. Las otras 58 filas de `Empleados` no tienen documento. Si esa tabla se usa como
      padrón de personal, conviene completarla; si no se usa, conviene decirlo, porque
      hoy es la única pista de que alguien es empleado y no tiene ficha.
*/

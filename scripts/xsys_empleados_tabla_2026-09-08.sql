/*======================================================================================
  xSys - Revisión de la tabla `Empleados` (legajo de personal)
  Fecha: 2026-09-08

  Se pidió "arreglar la tabla de empleados". Se revisó entera y se corrigió lo único
  que se podía establecer con certeza. El resto NO se tocó, y este archivo explica por
  qué: hacerlo habría implicado adivinar, que es justamente el origen de los problemas
  de acceso que se vienen resolviendo esta semana.

  Respaldo completo previo: zEmpleados_20260908 (las 76 filas, tal como estaban).

  --------------------------------------------------------------------------------------
  ESTADO DE LA TABLA
  --------------------------------------------------------------------------------------
  76 filas. Antes de este cambio, sólo 11 tenían un documento real.

      documento real ....... 11  ->  17  (después de las correcciones)
      comodín .............. 60  ->  57
      sin documento ......... 5  ->   2

  Las 17 con documento real tienen las 17 una ficha activa en `Clientes`.

  Los comodines son: 1 (43 filas), 111111, 11111, 1111, 111, 11, 28, 999, 9999999,
  99999999 y 11111111. Las fechas de nacimiento también son de relleno: 1950-01-01,
  1900-01-01, 1980-01-01, 2000-01-01, 2011-11-11, 2020-01-01.

  --------------------------------------------------------------------------------------
  LO QUE SE CORRIGIÓ (1 fila)
  --------------------------------------------------------------------------------------
      Id_Empleado 57 · MARTIN, KAREN MICAELA
          Documento_Nro:  1  ->  38663967

  Es el único caso con certeza: el DNI lo aportó el club y ese mismo día se le creó la
  ficha 947563 en `Clientes` como EMPLEADO con ese documento. Ahora las dos tablas
  coinciden.

      UPDATE Empleados SET Documento_Nro = 38663967 WHERE Id_Empleado = 57

  --------------------------------------------------------------------------------------
  SEGUNDA PASADA: CRUCE LAXO (2 filas más)
  --------------------------------------------------------------------------------------
  El primer cruce exigía coincidencia EXACTA de apellido + nombre y por eso se le
  escapaban los casos donde el legajo abrevia ("SERGIO E" contra "SERGIO EMANUEL") o
  la ficha agrega un segundo nombre. Se repitió por apellido y por apellido + alguna
  palabra del nombre. De las 63 filas sin documento:

      16   el apellido NO existe en Clientes (sobre 212.786 fichas con documento)
      35   el apellido existe pero ningún nombre coincide
       8   un solo candidato
       4   varios candidatos

  De los 8 candidatos únicos, sólo dos resisten el escrutinio y se aplicaron:

      Id_Empleado 22 · DENIS SANDOVAL, SERGIO E        1        -> 19066830
          Ficha 911096, categoría EMPLEADO, activa, con 80 pasadas por molinete entre
          abril/2024 y agosto/2026. El apellido compuesto aparece dos veces en toda la
          base y la otra es la misma persona sin documento. No hay ambigüedad posible.
          (Ojo: su documento de 19 millones no condice con su fecha de nacimiento de
           1993. Está así en Clientes desde 2024; se copió tal cual para que las dos
           tablas coincidan, pero conviene que RRHH lo verifique.)

      Id_Empleado 62 · MAIDANA, CINTHIA                9999999  -> 30065560
          Ficha 917977 MAIDANA CINTHIA CAROLINA, única con ese nombre y apellido en la
          base, activa, con 11 pasadas. El documento (30 millones) es coherente con su
          año de nacimiento (1983).

  Los otros 6 candidatos únicos NO se aplicaron: sus fichas son de INVITADOS o de un
  CADETE de baja, sin nada que confirme que sean la misma persona.
      emp 58 ARNABAL LEANDRO      -> ficha INVITADOS con 4 pasadas, todas el mismo día
      emp  5 SILVA NATALIA        -> INVITADOS
      emp 25 DIAZ, GABRIELA       -> INVITADOS
      emp 51 CARRIEL JOSE         -> INVITADOS, documento extranjero 95900960
      emp 12 MENECLIER NATALIA    -> INVITADOS
      emp 47 DOMINGUEZ FRANCISCO  -> CADETE de baja, nacido en 2000

  --------------------------------------------------------------------------------------
  TERCERA PASADA: TRES DNI QUE APORTÓ EL CLUB (3 filas más)
  --------------------------------------------------------------------------------------
      Id_Empleado 75 · LAGE, EZEQUIEL GUILLERMO      (vacío)  -> 33018238
      Id_Empleado 71 · ZARATE, VALENTIN              (vacío)  -> 44830524
      Id_Empleado 74 · ESCUDERO, XIMENA YAMILA       (vacío)  -> 28057175

  Estos tres no salieron de ningún cruce: los pasó el club. Estaban entre las 5 filas con
  el documento directamente vacío, así que no aparecían al buscar por DNI ni tenían ficha
  en `Clientes`. Se les completó el legajo y se les creó la ficha en la misma transacción
  (fichas 947564, 947565 y 947566, ver scripts/xsys_alta_empleados_2026-09-08.sql).

  Dos de los tres venían rebotando en el molinete con motivo 103: Zarate cuatro veces, la
  última el 31/07, y Escudero dos veces el mismo 08/09 por SM-Noble.

  --------------------------------------------------------------------------------------
  HALLAZGO: UN EMPLEADO QUE NO PUEDE ENTRAR
  --------------------------------------------------------------------------------------
      Id_Empleado 59 · ANDRADA, GONZALO EZEQUIEL · DNI 38525321 · estado EFECTIVO

  Tiene ficha (918026) y el documento coincide exacto con el legajo, pero la ficha está
  en categoría INVITADO SC en vez de EMPLEADO. Diagnóstico: "No entra por ninguna
  puerta: no cumple ninguna condición habilitante".

  NO se cambió. Pasarlo a EMPLEADO es darle acceso a 20 puertas, y eso es una decisión
  del club, no una corrección de datos. Si se confirma que trabaja ahí, la corrección
  es cambiarle la categoría desde la pantalla de fichas.

  --------------------------------------------------------------------------------------
  POR QUÉ NO SE COMPLETARON LAS OTRAS 61
  --------------------------------------------------------------------------------------
  Se intentaron tres caminos para recuperar los documentos sin pedírselos a nadie:

  1) Por CUIL. Sólo 3 filas tienen CUIL cargado (GOMEZ LUCIA, SUAREZ MARIA SOL y
     CACERES CANDELA AYELEN) y las tres YA tenían su documento. Sirvió igual como
     control: el DNI que contiene el CUIL coincide exactamente con el documento
     cargado en las tres, lo que valida las altas hechas ese día.

  2) Por credencial (`Cod_Tarjeta`). No sirve: 62 filas tienen '1' y el resto también
     son comodines. No hay una sola tarjeta real.

  3) Por nombre contra `Clientes`. Es el camino tentador y es el peligroso. De las 64
     filas incompletas, sólo 5 dieron una coincidencia única, y al mirarlas de cerca 4
     son coincidencias falsas:

         emp 12  MENECLIER NATALIA    -> ficha 937375, categoría INVITADOS, alta 16/05/2026
         emp 20  ROMERO MATIAS        -> ficha 931865, INVITADOS  (hay 133 ROMERO en la base)
         emp 47  DOMINGUEZ FRANCISCO  -> ficha 876625, CADETE de baja, nacido en 2000
                                         (hay 87 DOMINGUEZ en la base)
         emp 51  CARRIEL JOSE         -> ficha 944633, INVITADOS, documento 95900960
                                         (rango de documento extranjero)

     La quinta era MARTIN KAREN MICAELA, que sí es correcta porque su ficha la creamos
     nosotros ese mismo día con el DNI que aportó el club.

     Copiar el documento de un INVITADO homónimo al legajo de un empleado es
     exactamente el mecanismo que deja gente afuera del molinete —o peor, que hace
     entrar a la persona equivocada—. No se aplicó ninguna de las cuatro.

  --------------------------------------------------------------------------------------
  OTRAS COSAS QUE SE VIERON Y NO SE TOCARON
  --------------------------------------------------------------------------------------
  · Id_Empleado 0 es una fila completamente vacía.
  · Id_Empleado 28 "CONTADURIA CONTABLE" y 53 "PAGO FACIL" no son personas.
  · Id_Empleado 41 figura como Apellido=EMILIANO / Nombre=ESCOBAR, y el 48 como
    Apellido=DANIEL / Nombre=BANEGA. Parecen invertidos, pero no hay con qué
    confirmarlo (ninguno de los dos aparece en `Clientes`, en ningún orden).
  · Id_Empleado 45 "RODRIGUEZ DIEGO" sigue con documento 1.
  · Id_Empleado 62 "MAIDANA CINTHIA" y 63 "GIMENEZ MAURO" siguen con 9999999.
  · RODRIGUEZ, DENISE ANDREA es empleada con ficha en `Clientes` (899344) pero NO tiene
    fila en `Empleados`. No se agregó: crear un legajo es inventar datos de RRHH.
  · Lo mismo con GARCIA, MACARENA (DNI 42100802, ficha 947567 creada el 08/09): el club
    la informó como personal y no figura en `Empleados` bajo ninguna variante del nombre.
    Son dos personas que trabajan en el club y que esta tabla no conoce, lo que confirma
    que hoy `Empleados` no sirve como padrón de personal.
  · El campo `Id_Estado_Empleado` está cargado en menos de la mitad de las filas, así
    que la tabla no sirve hoy para saber quién sigue trabajando en el club.

  --------------------------------------------------------------------------------------
  CÓMO SE TERMINA DE ARREGLAR
  --------------------------------------------------------------------------------------
  Se dejó `scripts/empleados_sin_documento_2026-09-08.csv` con las 76 filas y dos
  columnas para completar: el DNI real y si la persona sigue trabajando. Con ese
  archivo devuelto se cargan los 59 documentos que faltan de una pasada, se dan de alta
  en `Clientes` los que falten y se dan de baja los que ya no estén.

  --------------------------------------------------------------------------------------
  VUELTA ATRÁS
  --------------------------------------------------------------------------------------
      UPDATE E SET E.Documento_Nro = Z.Documento_Nro
      FROM Empleados E JOIN zEmpleados_20260908 Z ON Z.Id_Empleado = E.Id_Empleado

======================================================================================*/


-- ------------------------------------------------------------------------------------
-- CONTROL: cómo está la tabla hoy
-- ------------------------------------------------------------------------------------
SELECT  calidad = CASE
            WHEN Documento_Nro IS NULL OR Documento_Nro = 0        THEN 'sin documento'
            WHEN Documento_Nro < 1000000                           THEN 'comodin'
            WHEN Documento_Nro IN (9999999, 99999999, 11111111)    THEN 'comodin'
            ELSE 'documento real' END,
        n = COUNT(*)
FROM    Empleados
GROUP BY CASE
            WHEN Documento_Nro IS NULL OR Documento_Nro = 0        THEN 'sin documento'
            WHEN Documento_Nro < 1000000                           THEN 'comodin'
            WHEN Documento_Nro IN (9999999, 99999999, 11111111)    THEN 'comodin'
            ELSE 'documento real' END
ORDER BY n DESC


-- Los que tienen documento real: ¿tienen ficha activa en Clientes?
SELECT  E.Id_Empleado,
        quien = RTRIM(ISNULL(E.Apellido,'')) + ', ' + RTRIM(ISNULL(E.Nombre,'')),
        E.Documento_Nro,
        ficha = C.Id_Cliente,
        categoria = T.Descripcion,
        C.Activo
FROM    Empleados E
        LEFT JOIN Clientes C ON C.Doc_Nro = E.Documento_Nro AND ISNULL(C.Activo,0) = 1
        LEFT JOIN Clientes_Tipos T ON T.Id_Tipo_Cli = C.Id_Tipo_Cli
WHERE   E.Documento_Nro > 1000000
  AND   E.Documento_Nro NOT IN (9999999, 99999999, 11111111)
ORDER BY E.Apellido

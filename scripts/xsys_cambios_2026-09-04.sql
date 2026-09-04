/* =============================================================================
   Carga del documento de dos docentes IGSM — aplicado sobre xsys_geba el
   04/09/2026. Registro de lo que quedó en la base; este archivo no se ejecuta
   solo. Ver también scripts/xsys_cambios_2026-08-31.sql, ..._09-01 y ..._09-02.

   ES LA PRIMERA VEZ QUE ESCRIBIMOS EN Clientes. Hasta acá todo lo nuestro había
   sido de sólo lectura sobre esa tabla, o tablas propias (CD_Clientes_*).

   ---------------------------------------------------------------------------
   POR QUÉ
   ---------------------------------------------------------------------------
   El 04/09 a las 07:54 alguien tecleó 23.329.185 en el Molinete 2 de Alcorta y
   el molinete contestó "La Persona es inválida" (motivo 103). El número no
   estaba en Clientes: CF_SCA_IdentifIdCliente devolvía 0.

   La persona SÍ existe, pero su ficha se dio de alta sin el documento. El
   18/12/2025, entre las 08:49 y las 09:50, se cargaron a mano 35 docentes IGSM
   (categoría 1135), uno cada minuto y medio, y SEIS quedaron con Doc_Nro = 0:

       925191  BERNARDEZ, ANDREA CECILIA   activa
       925194  VOLTA, CARLOS FABIAN        activa
       925213  LOMBARDO, EMILIANO ERNESTO  de baja el 27/08/2026
       925214  DI SANTO, LUCIANA LAURA     de baja el 27/08/2026
       925221  PREITI, DANIELA ANDREA      de baja el 27/08/2026
       925224  MORALES, FACUNDO GASTON     de baja el 27/08/2026

   Ninguno de los seis pasó jamás por un molinete: no dejaron de entrar, nunca
   pudieron. Acá se corrigen los dos que siguen activos.

   ---------------------------------------------------------------------------
   DE DÓNDE SALE EL DATO — leer antes de repetir esto con otro
   ---------------------------------------------------------------------------
   NO hay nada en xSys que ate esos documentos a esas fichas: la ficha no tiene
   documento, ni legajo, ni CUIT, ni documentación adjunta. El puente es el
   NOMBRE, contra z_Empleados_20260827 (copia de la nómina de personal cargada
   en la base el 27/08/2026 09:30):

       legajo 491  BERNARDEZ, ANDREA CECILIA  DNI 23329185  CUIL 23-23329185-4
       legajo 550  VOLTA, CARLOS FABIAN       DNI 21539688  CUIL 20-21539688-7

   Cada uno es el único con ese apellido y nombre en Clientes, y la fecha de
   nacimiento de la ficha (26/04/1973 y 24/04/1970) es coherente con el rango de
   emisión de esos documentos. Es una inferencia razonable, no una prueba: la
   confirmación con el documento a la vista es de la oficina de Socios.

   ---------------------------------------------------------------------------
   RESPALDO
   ---------------------------------------------------------------------------
   Las dos filas completas, tal como estaban, en:  zClientes_SinDoc_20260904

   Para deshacer:
     UPDATE Clientes SET Doc_Nro = 0 WHERE Id_Cliente IN (925191, 925194)
   ============================================================================= */


/* ---------------------------------------------------------------------------
   1) Respaldo
--------------------------------------------------------------------------- */
SELECT * INTO dbo.zClientes_SinDoc_20260904
FROM Clientes WHERE Id_Cliente IN (925191, 925194)
GO


/* ---------------------------------------------------------------------------
   2) El cambio. Corrió dentro de una transacción, con tres cortes previos:
      la ficha existe, todavía tiene Doc_Nro = 0, y ninguna OTRA ficha usa ese
      documento. Se confirmó recién después de verificar que
      CF_SCA_IdentifIdCliente devolviera la ficha correcta.

   Sólo se tocó Doc_Nro. Id_Tipo_Doc ya era 1 (DNI). Legajo y Cuit quedaron
   vacíos a propósito: están vacíos en TODOS los docentes IGSM, así que llenarlos
   en dos sería inventar una convención que el club no usa.
--------------------------------------------------------------------------- */
UPDATE Clientes SET Doc_Nro = 23329185 WHERE Id_Cliente = 925191  /* BERNARDEZ */
GO
UPDATE Clientes SET Doc_Nro = 21539688 WHERE Id_Cliente = 925194  /* VOLTA */
GO


/* ---------------------------------------------------------------------------
   3) Efectos de los triggers: NINGUNO. Se midió en un ensayo con ROLLBACK antes
      de aplicar de verdad.

   Clientes tiene cinco triggers que disparan en UPDATE (tri_clientes,
   CT_Clientes_Hist, CT_Clientes_Hist_Upd, CT_Clientes_NULLS y
   CT_Clientes_CD_Clientes_Novedades). El que preocupaba era tri_clientes: si
   Id_Cliente_Externo está en 0 le asigna número de socio con SF_Nro_socio. No lo
   hizo — quedaron los dos en 0, como el resto de la tanda. Tampoco se agregó
   fila en Clientes_Hist ni novedad en CD_Clientes_Novedades.

   Que NO se genere novedad significa que el lector facial no se entera. Para
   estos dos no cambia nada: no están enrolados y su única forma de entrar es
   tecleando el DNI, que se valida contra xSys en vivo. El espejo local se
   refrescó a mano con sync_socios_by_ids.
--------------------------------------------------------------------------- */


/* ---------------------------------------------------------------------------
   4) LO QUE ESTO NO ARREGLA
--------------------------------------------------------------------------- */
-- Ahora el molinete los identifica, pero por SM-Alcorta siguen sin pasar.
-- CF_SCA_ValidarTipo exige una fila en CD_Accesos_Cli_Tipos para (acceso,
-- categoría), y la categoría 1135 sólo tiene cuatro:
--
--     13 CS-20-AL | 21 ROEL Mora | 22 Acceso CS | 27 CONTROL MANUAL
--
-- SM-Alcorta (14) y SM-Ombues (15) NO están. Por eso el mensaje que van a
-- recibir cambia de "La Persona es inválida" (103) a "No cumple ninguna
-- condición habilitante" (112), que es lo que vienen recibiendo los demás
-- docentes: en 30 días, 24 rechazos contra 19 pasos, y esos 19 entraron por otra
-- vía (figuran además como EMPLEADO, o tienen cuota social paga). Ninguno entró
-- por ser docente.
--
-- Si el club decide que DOCENTE IGSM debe abrir esas puertas, es agregar la fila
-- correspondiente en CD_Accesos_Cli_Tipos, igual que el 01/09 se hizo con el
-- contrato INSTITUTO para los alumnos:
--
--   INSERT INTO CD_Accesos_Cli_Tipos (Id_Acceso, Id_Tipo_Cli, Flag_Habilitado)
--   VALUES (14, 1135, NULL)   -- NULL vale 'H': así lo lee CF_SCA_ValidarTipo
--
-- NO se aplicó: es una decisión de la casa, no un error de datos.

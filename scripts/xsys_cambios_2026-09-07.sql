/* =============================================================================
   Alumnos del IGSM con la ficha sin documento — aplicado sobre xsys_geba el
   07/09/2026. Registro de lo que quedó en la base; este archivo no se ejecuta
   solo. Continúa scripts/xsys_cambios_2026-09-04.sql.

   ---------------------------------------------------------------------------
   EL SÍNTOMA
   ---------------------------------------------------------------------------
   El socio 518611 no podía entrar; en la puerta le decían que era del
   profesorado. Motivo 104, "La Persona desactivada", seis veces entre el 02/09
   y el 07/09 en Alcorta y Ombúes.

   ---------------------------------------------------------------------------
   LA CAUSA — vale para toda esta familia de casos
   ---------------------------------------------------------------------------
   La persona tiene DOS fichas:

     518611  ANZISI, VALENTIN ANDRES  ACTIVO MENOR  de baja 30/06/2023  DNI 41915049
     911049  ANZISI, VALENTIN ANDRES  ALUMNO IGSM   activa              DNI 0
                                      contrato INSTITUTO 755832, vigente al 31/12/2026

   La ficha buena —la del profesorado, con el contrato que SÍ abre San Martín—
   no tiene documento, y tampoco credencial: es inalcanzable desde un molinete.
   Al teclear el DNI, la búsqueda caía en la ficha vieja de socio, que está dada
   de baja. De ahí el "Persona desactivada": el molinete tenía razón, sólo que
   estaba mirando la ficha equivocada.

   ---------------------------------------------------------------------------
   POR QUÉ ALCANZA CON COPIAR EL DOCUMENTO A LA FICHA BUENA
   ---------------------------------------------------------------------------
   No hay que borrárselo a la vieja. CF_SCA_IdentifIdCliente resuelve los
   documentos repetidos con:

       SELECT TOP 1 ... ORDER BY C.Activo DESC,
                                 <prioridad por Clientes_Tipos.Flag_Tipo>,
                                 C.Ult_Cuota_Paga DESC

   Activo DESC va PRIMERO, así que con las dos fichas compartiendo el documento
   gana la activa. (Importa: por Flag_Tipo ganaría la vieja — ACTIVO MENOR es 'P',
   prioridad 1, y ALUMNO IGSM es 'N', prioridad 4. Es el Activo el que decide.)

   Y el documento repetido no es una anomalía que estemos introduciendo: en la
   base hay 22.193 documentos repetidos entre 50.568 fichas, y 6.556 de esos
   casos son exactamente el par "una activa + una de baja". La función está
   escrita para eso.
   ============================================================================= */


/* ---------------------------------------------------------------------------
   1) ANZISI — respaldo en zClientes_SinDoc_20260907
   Identificación: mismo apellido, mismo nombre y MISMA FECHA DE NACIMIENTO
   (15/04/1999) en las dos fichas.
--------------------------------------------------------------------------- */
SELECT * INTO dbo.zClientes_SinDoc_20260907
FROM Clientes WHERE Id_Cliente IN (518611, 911049)
GO
UPDATE Clientes SET Doc_Nro = 41915049 WHERE Id_Cliente = 911049
GO


/* ---------------------------------------------------------------------------
   2) BAUD — respaldo en zClientes_SinDoc_20260907b
   Mismo caso: 889976 (de baja 19/02/2021, DNI 42454920) contra 902742 (activa,
   ALUMNO IGSM, contrato INSTITUTO 757516, sin documento).

   OJO, acá la fecha de nacimiento NO coincide, y se aplicó igual. Por qué:
     - las dos fichas tienen el MISMO MAIL: agustinabaud@gmail.com
     - la fecha de la ficha vieja (18/02/2000) cuadra con el documento
     - la de la ficha nueva es 12/03/2003, o sea el mismo día y mes que su
       propia Fecha_Alta (12/03/2023): es un error de tipeo al cargarla, no
       la fecha de otra persona.
   El corte de seguridad del script exigió que el mail coincidiera.
--------------------------------------------------------------------------- */
SELECT * INTO dbo.zClientes_SinDoc_20260907b
FROM Clientes WHERE Id_Cliente IN (889976, 902742)
GO
UPDATE Clientes SET Doc_Nro = 42454920 WHERE Id_Cliente = 902742
GO


/* ---------------------------------------------------------------------------
   3) LOS TRES QUE NO SE PUDIERON CERRAR
--------------------------------------------------------------------------- */
-- Su documento NO está en ninguna tabla de xSys: no hay ficha gemela, ni nómina,
-- ni adherentes, ni invitados, ni CRM. Se buscó. Inventar el número no es una
-- opción, así que hay que pedírselo a la persona:
--
--     863892  ALE, MATIAS         tiene credencial BC34B47D — pero no registra
--                                 un paso desde 2017, todos rechazos
--     900613  FAINBERG, MARTINA   sin credencial: hoy no tiene NINGUNA forma de entrar
--     912955  VICENT, CATALINA    sin credencial: ídem
--
-- Para que no quede en un informe que se pierde, los tres quedaron cargados como
-- aviso en la pantalla "Avisos a socios" de geba_acs (SocioAviso), que es la que
-- mira la oficina. Cuando consigan el número, es el mismo UPDATE de arriba.


/* ---------------------------------------------------------------------------
   4) LO QUE ESTO DEJA VER
--------------------------------------------------------------------------- */
-- De los 110 alumnos con contrato INSTITUTO vigente, 103 NO TIENEN CREDENCIAL.
-- Para casi todos, la única forma de entrar es tecleando el DNI. Una ficha sin
-- documento los deja afuera sin excepción, y no hay plan B.
--
-- Conviene revisar el padrón del instituto cada vez que se abre una cohorte: el
-- alta se hace a mano y el documento es el campo que se saltea. Es el mismo
-- patrón que el 04/09 con los docentes (6 de 35 cargados sin documento el
-- 18/12/2025) — ahí eran 35 altas a mano, una cada minuto y medio.

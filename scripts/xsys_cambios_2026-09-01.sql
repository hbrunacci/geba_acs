/* =============================================================================
   Cambios aplicados sobre xsys_geba el 01/09/2026.
   Registro de lo que quedo en la base; este archivo no se ejecuta solo.
   Ver tambien scripts/xsys_cambios_2026-08-31.sql.
   ============================================================================= */


/* -----------------------------------------------------------------------------
   1) El contrato INSTITUTO (tipo 55) no habilitaba ninguna puerta.

   CD_Accesos_Cont_Tipos no tenia una sola fila para el tipo 55, asi que los
   alumnos del profesorado no entraban por su inscripcion: solo entraban los que
   ademas tenian la cuota social paga. Con 199 alumnos IGSM activos y 80 con
   contrato INSTITUTO vigente, en 30 dias eso dio 44 rechazos "No cumple ninguna
   condicion habilitante" de 21 personas.

   Se copian las filas del tipo 44 (CONTRATO PROFESORES) tal cual, para que
   INSTITUTO se comporte exactamente igual y no quede una variante nueva.
   Respaldo de la tabla entera: zCD_Accesos_Cont_Tipos_20260901.

   OJO, dos cosas que no son obvias:
   - Habilitar por CONTRATO no mira si el contrato esta pago. Un alumno con la
     cuota del instituto atrasada entra igual. Los rechazos (SUSPENSION 47,
     LICENCIA SIN ACCESO 48) siguen corriendo antes y lo siguen frenando, y un
     socio inactivo tampoco entra: ese rechazo va primero.
   - CF_SCA_ValidarContratosTipos une CD_Accesos_Cont_Tipos SOLO por Id_Tipo_Con
     -el filtro de puerta es un IN aparte sobre el tipo-, asi que
     Flag_Habilitado no se respeta puerta por puerta: alcanza con que el tipo
     tenga una fila en 'H' en cualquier acceso. Por eso el acceso 21, que quedo
     con Flag_Habilitado = ' ', igual habilita. Pasa lo mismo con PROFESORES
     desde siempre; se copio la rareza a proposito para no divergir.
----------------------------------------------------------------------------- */

SELECT * INTO zCD_Accesos_Cont_Tipos_20260901 FROM CD_Accesos_Cont_Tipos
GO

INSERT INTO CD_Accesos_Cont_Tipos
      (Id_Acceso, Id_Tipo_Con, Flag_Habilitado, Dias_Aviso, Observacion_Prox_Venc,
       Flag_Lunes, Flag_Martes, Flag_Miercoles, Flag_Jueves, Flag_Viernes,
       Flag_Sabado, Flag_Domingo)
SELECT Id_Acceso, 55, Flag_Habilitado, Dias_Aviso, Observacion_Prox_Venc,
       Flag_Lunes, Flag_Martes, Flag_Miercoles, Flag_Jueves, Flag_Viernes,
       Flag_Sabado, Flag_Domingo
FROM CD_Accesos_Cont_Tipos
WHERE Id_Tipo_Con = 44
GO
/* Quedan los accesos 12 NEWBERY, 14 SM-Alcorta, 15 SM-Ombues, 16 SM-Noble,
   21 ROEL Mora, 23 SM-Noble Bicis y 26 SM_Ombues_2. Para revertir:
   DELETE FROM CD_Accesos_Cont_Tipos WHERE Id_Tipo_Con = 55                    */


/* -----------------------------------------------------------------------------
   2) Limpieza de indices HIPOTETICOS en CD_ES.

   La tabla figuraba con 161 indices, pero 153 eran hipoteticos
   (sys.indexes.is_hypothetical = 1): metadata que deja el Database Tuning
   Advisor cuando una sesion de analisis se corta. No tienen paginas asignadas,
   no se mantienen en los INSERT y el optimizador no los usa para ejecutar, asi
   que borrarlos no cambia ningun plan. Se comprobo antes de tocar que ninguna
   particion les correspondia y que ningun SP los nombra en un hint.

   Los 153 nombres quedaron en
   scripts/xsys_indices_hipoteticos_CD_ES_2026-09-01.txt

   Los 8 REALES no se tocaron. Uso medido en 33 dias (motor arrancado 30/07):

     PK_CDES_PorId (clustered)   1631 MB   4.730.435 seeks   6.970.895 scans
     IX_CDES_PorCli_Cubierto      583 MB   1.939.239 seeks
     IX_CDES_PorTar               528 MB         662 seeks
     IX_CDES_PorCli               204 MB   2.170.595 seeks
     IX_CDES_PorCont              186 MB          68 seeks
     IX_CDES_PorFecha             156 MB         743 seeks
     IX_CDES_CD_Motivos           118 MB           5 seeks
     IX_CDES_PorTipoCont          110 MB           0 seeks

   Pendiente de decidir (no se hizo):
   - IX_CDES_PorTipoCont (0 seeks) e IX_CDES_CD_Motivos (5 seeks) son 228 MB
     por casi nada, pero 33 dias no cubren un cierre anual.
   - IX_CDES_PorCli es prefijo estricto de IX_CDES_PorCli_Cubierto: se pueden
     consolidar, pero implica reconstruir 583 MB y va a ventana.
   - Quedan 4.543 indices hipoteticos en otras 39 tablas (Cbtes 1.006,
     Clientes 1.000, Cbtes_Items 565...) y 352 estadisticas _dta_stat huerfanas.
----------------------------------------------------------------------------- */

/* DROP INDEX [<nombre>] ON dbo.CD_ES   -- x153, ver el .txt                   */

EXEC sp_rename
     'CD_ES._dta_index_CD_ES_5_2002158228__K5_K6_K8_K9_K4_K1_9987_4364_8066_1912_4149_5201',
     'IX_CDES_PorCli_Cubierto',
     'INDEX'
GO
/* Con 1,94 millones de seeks es de los mas usados de la tabla y con el nombre
   viejo parecia basura del Tuning Advisor: alguien lo iba a borrar creyendo
   que limpiaba.                                                              */

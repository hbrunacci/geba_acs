/*======================================================================================
  xSys - Los accesos vuelven a exigir la cuota al día, pero sólo a quien la paga
  Fecha: 2026-09-12 00:30  — aplicado sobre xsys_geba

  Registro de lo que quedó en la base; este archivo no se ejecuta solo.

  --------------------------------------------------------------------------------------
  DE DÓNDE SALIÓ
  --------------------------------------------------------------------------------------
  Tres socios con la cuota vencida pasaron por el facial de Ombúes el 11/09 y el visor
  los mostró como "Acceso Concedido · Cuota Vencida". No era una falla de sincronización:
  la lista blanca estaba fresca y los tres figuraban habilitados.

  Los tres habían pagado hasta julio. Con la regla del club —fin del mes pagado + 40
  días— el permiso se les caía el 09/09. Pasaron igual porque el acceso por el que
  entraron no miraba la cuota: de los 27 accesos configurados, sólo cuatro tenían
  `Flag_Ult_Cuota_Paga` distinto de cero (SanMartin_Noble y las tres barreras de autos),
  y de esos cuatro el único con tránsito real era una barrera. Todo el resto —Alcorta,
  Ombúes, Aldao, Newbery y el acceso 22, que es contra el que se calcula la lista blanca
  del facial— habilitaba por contrato o producto comprado, pago o no.

  --------------------------------------------------------------------------------------
  POR QUÉ NO ALCANZABA CON PRENDER LA BANDERA
  --------------------------------------------------------------------------------------
  Dos razones, las dos medidas antes de tocar nada.

  1) Los siete accesos con tránsito que tenían la bandera en cero tenían también
     CERO días de gracia. Prender sólo la bandera habría hecho que "al día" significara
     tener paga la cuota del mes en curso: de las 19.700 personas que entraron en 30
     días, 19.661 quedaban afuera, incluidas las que estaban perfectamente al día.

  2) `CP_SCA_RegistrarAcceso` evalúa el rechazo por UCP (motivo 300) ANTES de toda la
     cascada de habilitaciones —master, links, contratos, categoría, productos—. Y
     `CF_SCA_ValidarUltCuotaPaga` devolvía 0 para cualquiera sin cuota registrada. O sea
     que la bandera en 2 frenaba a quien entra por otra vía: se midió contra la función
     real, no contra el espejo, y daban 0 los invitados (1.016 en 30 días), las
     bicicletas (399), los concesionarios (61), los alumnos (57) y docentes (8) del
     IGSM, los empleados (8), un vitalicio, un socio honorario y un ACCESO MASTER.

  Total del daño colateral con la gracia corregida: 3.207 personas frenadas, de las
  cuales sólo 1.655 eran socios que realmente debían.

  --------------------------------------------------------------------------------------
  Y TAMPOCO ALCANZABA CON MIRAR Flag_Tipo
  --------------------------------------------------------------------------------------
  El corte P/S saca a los no socios, pero deja adentro a los vitalicios. Y a los
  vitalicios +71 el club NO les factura la cuota: de 3.677 fichas activas, reciben cupón
  682 (18%), contra el 93-100% del resto de las categorías de socio. Su `Ult_Cuota_Paga`
  quedó congelado donde estuvo el último pago, así que 3.599 de ellos figuran atrasados
  sin deber nada. Exigírsela habría dejado afuera a casi toda la categoría.

  Se probó también decidirlo por contrato, que habría sido lo más prolijo: no sirve.
  Los 3.677 vitalicios +71 tienen contrato de cuota social igual que todo el mundo; el
  club mantiene el contrato y no lo factura. La única señal que separa a los que pagan
  de los que no es la facturación misma.

  Criterio adoptado, medido sobre los lotes CSOC_2026-08/09/10:

        VITALICIO + 71     682 de 3.677 facturados   ( 18%)  -> exenta
        SOCIO OLIMPICO       1 de    19               (  5%)  -> exenta
        SOCIO HONORARIO      1 de    11               (  9%)  -> exenta
        las otras 14 categorías de socio               93-100% -> se les exige

  --------------------------------------------------------------------------------------
  1) BACKUP
  --------------------------------------------------------------------------------------
        zCF_SCA_ValidarUltCuotaPaga_20260911

  Guardas previas: que la función exista y sea legible; que no tuviera ya la guarda;
  que el ancla apareciera exactamente una vez; que el backup no existiera de antes.

  --------------------------------------------------------------------------------------
  2) EL CAMBIO EN LA FUNCIÓN
  --------------------------------------------------------------------------------------
  Se agrega, dentro de la rama que evalúa la cuota y antes de calcular la fecha límite:

-- >>> CAMBIO INICIO
      IF (@Flag_Ult_Cuota_Paga = 2)
      BEGIN
          DECLARE @Id_Tipo_Cli SMALLINT
          DECLARE @Flag_Tipo   CHAR(1)

          SELECT  @Id_Tipo_Cli = C.Id_Tipo_Cli, @Flag_Tipo = T.Flag_Tipo
          FROM    Clientes C
                  LEFT JOIN Clientes_Tipos T ON T.Id_Tipo_Cli = C.Id_Tipo_Cli
          WHERE   C.Id_Cliente = @pId_Cliente

          IF (ISNULL(@Flag_Tipo, '') NOT IN ('P','S')
              OR @Id_Tipo_Cli IN (1010, 1100, 1126))
              RETURN 1
      END
-- >>> CAMBIO FIN

  La guarda corre SÓLO con la bandera en 2, que es el rechazo. Con la bandera en 1 —que
  hoy no usa ningún acceso— la cuota es condición habilitante y devolver 1 dejaría pasar
  a quien no corresponde; por eso el `IF` y no una salida incondicional.

  --------------------------------------------------------------------------------------
  3) EL CAMBIO EN LOS ACCESOS
  --------------------------------------------------------------------------------------
      UPDATE CD_Accesos
         SET Flag_Ult_Cuota_Paga = 2, Meses_Gracia = 0, Dias_Gracia = 40
       WHERE Activo = 1                                  -- 27 filas

  0 meses / 40 días es la gracia que ya usaban SanMartin_Noble y las tres barreras de
  autos; el resto quedaba en 0/0 y el acceso 22 y Pileta en 2 meses / 10 días.

  Estado anterior, para volver atrás:

      flag=2, 0 meses, 40 días : 5, 18, 19, 25
      flag=0, 2 meses, 10 días : 22, 24
      flag=0, 0 meses,  0 días : todos los demás

  --------------------------------------------------------------------------------------
  4) VERIFICACIÓN
  --------------------------------------------------------------------------------------
  a) Textual, antes del COMMIT: la función nueva tiene la guarda y conserva el cálculo
     de la fecha límite; el backup NO tiene la guarda (o sea que no se alteró el objeto
     equivocado).

  b) Funcional, contra el acceso 18, que ya estaba en 2 con 0/40:

        invitado sin cuota                      -> 1   (pasa)
        vitalicio +71 atrasado                  -> 1   (pasa)
        socio de categoría facturable, moroso   -> 0   (frena)
        socio de categoría facturable, al día   -> 1   (pasa)

     Los dos socios de prueba se buscan en vivo por fecha, no por número de ficha: el
     primer intento usó un moroso fijo que había pagado esa misma mañana y el control
     dio falso negativo.

  c) Después del COMMIT, recálculo completo de la lista blanca (54.795 fichas, 43 s):

        verificación masivo vs de-a-uno ... 100/100 idénticas
        habilitados ...................... 16.768
        pierden el acceso ................  1.050
        ganan el acceso ..................      0

     Motivos de rechazo después del cambio:

        No cumple ninguna condición habilitante ... 34.622
        Persona inactiva .......................... 1.851
        La persona no posee UCP al día ............ 1.441   <- nuevo
        Deuda de Actividades ......................   114

  De los tres casos que originaron todo esto: FREGONI (883023) y BANCHERO (845915)
  quedaron frenados por UCP; RAMOGNINO (779177) sigue habilitado porque pagó el 11/09 y
  su última cuota es agosto.

  --------------------------------------------------------------------------------------
  5) EL ESPEJO DEL VISOR
  --------------------------------------------------------------------------------------
  `xsys/api_views.py::_CATEGORIAS_SIN_CUOTA` es la lista equivalente del lado de la
  aplicación: decide a quién NO se le pinta "Cuota Vencida". Se amplió para que coincida
  con la guarda nueva (se agregaron las categorías que no son de socio y las tres de
  socio exentas). Si las dos listas no coinciden, el visor contradice al molinete y el
  operador no tiene forma de saber cuál miente. La regla quedó escrita también en el
  encabezado de `xsys/services/cuota.py`.

  Prueba de regresión: `test_al_que_no_paga_cuota_no_se_le_pinta_cuota_vencida`.

  --------------------------------------------------------------------------------------
  6) LO QUE ESTE CAMBIO NO RESUELVE
  --------------------------------------------------------------------------------------
  - Los 682 vitalicios +71 a los que SÍ se les factura quedan exentos igual, porque la
    exención es por categoría y no por ficha. Es el error conservador: deja entrar a
    alguien que debe, en vez de frenar a 3.054 que no deben nada. Para afinarlo haría
    falta decidir por ficha —quién recibió cupón CS en el último lote—, y eso es una
    consulta sobre Cbtes_Items en el camino caliente del molinete.

  - 55 socios de categoría facturable no tienen `Ult_Cuota_Paga` cargado (50 vitalicios
    +71 que además son de categoría exenta, 2 socios honorarios, 1 activo mayor, 1
    infantil, 1 pre infantil). Los tres últimos quedan frenados por ficha incompleta, no
    por deuda: hay que revisarlos en Socios.

  --------------------------------------------------------------------------------------
  7) CÓMO VOLVER ATRÁS
  --------------------------------------------------------------------------------------
  La función, con los DOS replace y en este orden (si sólo se cambia CREATE por ALTER se
  termina modificando el backup en vez del original):

      DECLARE @def NVARCHAR(MAX) =
          OBJECT_DEFINITION(OBJECT_ID('dbo.zCF_SCA_ValidarUltCuotaPaga_20260911'));
      SET @def = REPLACE(@def, '[zCF_SCA_ValidarUltCuotaPaga_20260911]',
                               '[CF_SCA_ValidarUltCuotaPaga]');
      SET @def = REPLACE(@def, 'CREATE FUNCTION', 'ALTER FUNCTION');
      EXEC sp_executesql @def;

  Los accesos, al estado anterior:

      UPDATE CD_Accesos SET Flag_Ult_Cuota_Paga = 0, Meses_Gracia = 0, Dias_Gracia = 0
       WHERE Activo = 1 AND Id_Acceso NOT IN (5, 18, 19, 22, 24, 25);
      UPDATE CD_Accesos SET Flag_Ult_Cuota_Paga = 0, Meses_Gracia = 2, Dias_Gracia = 10
       WHERE Id_Acceso IN (22, 24);
      UPDATE CD_Accesos SET Flag_Ult_Cuota_Paga = 2, Meses_Gracia = 0, Dias_Gracia = 40
       WHERE Id_Acceso IN (5, 18, 19, 25);

  Y después recalcular la lista blanca, o el facial sigue con la lista vieja:

      docker compose exec web python manage.py xsys_whitelist_full

  Para aflojar sin volver atrás del todo, subir Dias_Gracia en CD_Accesos alcanza: la
  lista blanca se recalcula sola y el corte se corre para todos.

======================================================================================*/


-- ------------------------------------------------------------------------------------
-- CONTROL: cómo quedaron los accesos
-- ------------------------------------------------------------------------------------
SELECT  Id_Acceso, Descripcion, Flag_Ult_Cuota_Paga, Meses_Gracia, Dias_Gracia
FROM    CD_Accesos
WHERE   Activo = 1
ORDER BY Id_Acceso


-- ------------------------------------------------------------------------------------
-- CONTROL: a quién frena hoy la cuota, por categoría
-- (es la misma cuenta que hace el molinete; sirve para auditarla a mano)
-- ------------------------------------------------------------------------------------
SELECT  categoria = CAST(t.Descripcion AS varchar(34)),
        activos   = COUNT(*),
        frenados  = SUM(CASE WHEN dbo.CF_SCA_ValidarUltCuotaPaga(c.Id_Cliente, 22, GETDATE()) = 0
                             THEN 1 ELSE 0 END)
FROM    Clientes c
        LEFT JOIN Clientes_Tipos t ON t.Id_Tipo_Cli = c.Id_Tipo_Cli
WHERE   c.Activo = 1
GROUP BY CAST(t.Descripcion AS varchar(34))
HAVING  SUM(CASE WHEN dbo.CF_SCA_ValidarUltCuotaPaga(c.Id_Cliente, 22, GETDATE()) = 0
                 THEN 1 ELSE 0 END) > 0
ORDER BY frenados DESC


-- ------------------------------------------------------------------------------------
-- CONTROL: socios de categoría facturable sin Ult_Cuota_Paga (ficha incompleta)
-- ------------------------------------------------------------------------------------
SELECT  c.Id_Cliente, c.Id_Cliente_Externo,
        quien = RTRIM(ISNULL(c.Apellido,'')) + ', ' + RTRIM(ISNULL(c.Nombre,'')),
        categoria = RTRIM(t.Descripcion)
FROM    Clientes c JOIN Clientes_Tipos t ON t.Id_Tipo_Cli = c.Id_Tipo_Cli
WHERE   c.Activo = 1
  AND   CAST(t.Flag_Tipo AS varchar(2)) IN ('P','S')
  AND   c.Id_Tipo_Cli NOT IN (1010, 1100, 1126)
  AND   c.Ult_Cuota_Paga IS NULL
ORDER BY categoria, quien

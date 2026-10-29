# Molinetes Intelektron manejados por geba_acs: de 1 a N placas

Paso a paso acordado el 28/10/2026. Lo marcado **HECHO** quedó ejecutado ese
día; lo demás depende de hardware o de infraestructura que hoy no hay.

## 1. Un proceso para todas las placas — HECHO, pero NO SIRVE con la librería actual
`manage.py intelektron_online` acepta una lista de placas
(`--placa ip[:puerto]/ctrl[/acceso[/rele[/seg]]]`, repetible, o `--placas "a;b;c"`).
Un solo proceso mantiene una sesión por placa, con el **mismo validador** y la
misma conexión a xSys; las decisiones se serializan con un lock (120–200 ms
cada una). La librería se inicializa una sola vez por proceso.

**Visto el 29/10/2026 con dos placas reales (Alcorta Molinete 3 (.117) + Ombúes Molinete 1 (.66)):**
`libitkcom` usa UN solo enlace (link id 1) por proceso aunque se llame a
`itk_open` dos veces con distinta IP. Cada reconexión de una placa cerraba
la conexión de la otra: cortes cada 30 s en las dos, cero lecturas en la .66.
Lo confirma su log (`/tmp/itk_online.log`: "close link id = 1 / open
10.0.0.117 / close link id = 1 / open 10.0.0.66", alternando).
**Regla hasta nuevo aviso: una placa por contenedor.** Compose tiene
`intelektron-online` (Alcorta Molinete 3, `INTELEKTRON_ONLINE_PLACAS`) e
`intelektron-online-ombues` (`INTELEKTRON_ONLINE_PLACAS_OMBUES`); para una
tercera placa se copia el bloque con otra variable. Cuesta ~55 MB por
contenedor y el arranque tarda ~45 s (migrate + collectstatic del entrypoint).
Pendiente: probar si `itk_open` con `source_node` distinto por placa da link
ids distintos (Clever pasa un nodo origen distinto por placa: `Intelek_NroNodo`),
o preguntar a Intelektron.

Para sumar una placa nueva: su propio servicio en compose y su variable en
`.env`, `docker compose --profile intelektron up -d <servicio>`. Antes, la
placa tiene que: estar en Modo Envío Online, mandar "Aceptado + No
identificado" al host (bits 3 en el param 24), tener fila en CD_Controladores
(Tipo_Cont 'K', Activo 0) o acceso explícito en la spec, saber qué relé
libera el aspa (en la .117 es relé 2 / 3 s por pulso directo; en la .66 basta
la config "Aceptado" de la placa, relé 1), y **tener "IP Servidor" = 10.0.0.20**
(params 522–525 por web: `set_num_cfg.cgi?4|1&522&10|1&523&0|1&524&0|1&525&20`).
Sin ese filtro cualquier instancia de la app de Clever (hay otra fuera de esta
PC: CD_Servers lista MOLI-SM 10.0.0.2 y JN-SOCIOS 192.168.3.80) se queda con
la placa en cuanto la ve libre, porque la app carga TODOS los controladores
activos, no sólo los de su sede. Las placas que instaló Clever ya lo tienen
(la .67 sí; la .66 y la .117 estaban en 0.0.0.0).

**Keepalive:** `itk_set_keepalive` recibe MILISEGUNDOS. Hasta el 29/10 se le
pasaba 30 (= 30 ms): un EcoTest cada ~130 ms, 7 por segundo, 200 MB de log
por día en `/tmp` del contenedor. Corregido (×1000 en `OnlineSession`).

## 2. Estado por placa y salud del servicio — HECHO
Línea de estado por placa cada 60 s (viva, eventos, abiertos, denegados,
reconexiones, caídas de socket). El bucle toca `/tmp/intelektron_online.alive`
y el contenedor tiene healthcheck sobre su edad (`docker compose ps` lo
muestra como healthy/unhealthy). Watchdog de socket + cause 3 = reconexión
inmediata (ya estaba).

## 3. Antirrebote compartido — HECHO (dentro del proceso)
Clave (controlador, tag, sentido): la misma credencial en dos placas distintas
se evalúa en cada una, como hace Clever.

## 4. Antipassback entre placas — PENDIENTE (no hace falta en Alcorta)
El validador replica el antipassback de Clever leyendo CD_ES, donde nuestras
pasadas NO están. Si una puerta con Flag_AntiPassBack=1 pasa a geba_acs, hay
que sumar `acs_paso_molinete` a esa consulta (y aceptar que los molinetes de
Clever de la misma puerta no verán nuestras pasadas, salvo que se vuelva a
escribir en CD_ES, decisión del usuario).

## 5. Reconexiones cada ~90 s — PENDIENTE (consultar a Intelektron)
La .117 cierra la sesión ~90 s después de cada lectura aunque el keepalive
de 30 s esté activo. Se reconecta sola en <1 s y no se pierden lecturas
salvo las que caen justo en ese segundo. Con diez placas es una reconexión
cada pocos segundos: conviene preguntar a Intelektron si hay un parámetro
(¿"Timeout Conexión" id 131 = 100 s?) o si el EcoTest no cuenta como
actividad.

## 6. Dónde corre — PENDIENTE (infraestructura)
Hoy: la PC 10.0.0.20 con Docker (3,8 GB para la VM; el servicio usa ~55 MB
por proceso, no por placa). Para diez puertas hace falta una máquina
dedicada (o el servidor GEBAA, al lado de la base de Clever, como corre la
app de Clever), con reinicio automático al bootear y alerta cuando el
servicio queda unhealthy o sin placas vivas (el archivo alive dice
`vivas/total`).

## 7. Qué pasa si Clever no responde — DECISIÓN PENDIENTE
Hoy: error de base → "SIN CONEXION" → se deniega. Alternativas: (a) seguir
así (fail-closed, igual que los molinetes de Clever sin app); (b) lista
blanca local (`XsysWhitelist` del espejo, se actualiza cada pocos minutos)
como respaldo cuando xSys no contesta. (b) es media hora de trabajo pero
cambia la política de seguridad: lo decide el club.

## 8. Prueba de carga — PENDIENTE (necesita más placas)
Con una sola placa no se puede medir contención real. Cuando haya 2–3,
repetir `validador_local_comparar` por acceso y mirar `duracion_ms` en
`acs_paso_molinete` (hoy promedio 190 ms, máximo 480 ms).

# Deploy con Docker (Windows)

Stack: **web** (Django + gunicorn, con ODBC Driver 18 para llegar a xSys) +
**db** (PostgreSQL 16) + **sync** (sincronización incremental de xSys cada X horas).

## Requisitos
- **Docker Desktop** en Windows (backend WSL2, contenedores Linux).
- El host debe tener **acceso de red al MSSQL de xSys** (`192.168.0.6:49331`) y, si se
  usan faciales, a BioStar (`10.0.0.27`). En el server de producción ya está en esa red.

## Puesta en marcha
1. Copiar el template de entorno y completarlo:
   ```powershell
   copy .env.dist .env
   ```
   Editar `.env` y setear al menos:
   - `DJANGO_SECRET_KEY` (una clave random)
   - `POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD`
   - `DJANGO_ALLOWED_HOSTS` (ej: `192.168.0.50,localhost`)
   - `MSSQL_XSYS_PASSWORD` (password del `sa` de xSys) — puerto default 49331, Encrypt=no ya configurados.
   - (opcional) `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD` para crear el admin al arrancar.

   > `docker-compose.yml` pisa `POSTGRES_HOST=db` y `POSTGRES_PORT=5432` (red interna),
   > así que no hace falta tocarlos en `.env`.

2. Construir y levantar:
   ```powershell
   docker compose up -d --build
   ```
   El entrypoint espera a Postgres, corre `migrate` y `collectstatic` solo.

3. Abrir **http://localhost:8000/** (o la IP del server). Login con el superusuario.

4. **Carga inicial del espejo xSys** (una vez, con la red a xSys disponible):
   ```powershell
   docker compose exec web python manage.py xsys_init
   ```
   Después, el servicio `sync` corre `xsys_sync` cada `XSYS_SYNC_INTERVAL` seg (default 6h).
   Miniaturas de fotos ya cargadas: `docker compose exec web python manage.py xsys_thumbnails`.

## Operación
- Logs: `docker compose logs -f web` (o `sync`, `db`).
- Backup de la base: la data vive en el volumen `postgres_data`.
- Actualizar la app: `git pull` y `docker compose up -d --build`.
- Parar todo: `docker compose down` (agregar `-v` también borra la base — cuidado).

## Auto-deploy (sin tocar el server a mano)

### El problema que resuelve

Sin esto, actualizar el server significa que alguien tiene que conectarse a la
máquina Windows a mano cada vez y correr `git pull` + `docker compose up -d
--build`. El auto-deploy elimina ese paso manual: la máquina se actualiza sola
cuando aparece un commit nuevo en `origin/main`.

### Por qué polling y no un webhook de GitHub

La alternativa "moderna" sería que GitHub le pegue un webhook HTTP al server
apenas se hace push. Se descartó porque **esta red no tiene forwarding entrante
confiable** hacia la máquina — el firewall/router de GEBA no expone un puerto
público estable hacia ella (verificado: al escanear la IP pública del club solo
respondía FTP, nada más). Habilitar eso implicaría tocar el router y abrir un
puerto a internet hacia un server de producción, que es justo lo que se quiere
evitar.

La solución en cambio es **polling**: la máquina Windows pregunta ella misma,
cada tanto, "¿hay algo nuevo en GitHub?". Eso solo necesita salida a internet
(que ya funciona), nunca entrada. Es menos "instantáneo" (hasta N minutos de
demora) pero muchísimo más simple y no depende de tocar el firewall.

### Cómo funciona (arquitectura)

```
 Windows Task Scheduler
        │  cada 5 min (configurable)
        ▼
 scripts/auto_deploy.ps1
        │
        ├─ git fetch origin main
        ├─ ¿HEAD local == origin/main?  ──sí──▶ no hace nada, termina
        │
        no
        ▼
 git reset --hard origin/main   (pisa cualquier cambio local)
        ▼
 docker compose up -d --build   (reconstruye la imagen si el código cambió,
        │                        reinicia los contenedores)
        ▼
 log en auto_deploy.log
```

Dos archivos nuevos en `scripts/`:

- **`auto_deploy.ps1`** — el chequeo + deploy en sí. Está pensado para
  ejecutarse solo (sin intervención humana), así que:
  - Si no hay commits nuevos, no hace nada y no escribe nada en el log (para
    no llenarlo de líneas inútiles cada 5 minutos).
  - Usa `git reset --hard` en vez de `git pull`: esta máquina es **solo un
    destino de deploy**, nunca origina cambios, así que no hace falta (ni se
    quiere) resolver merges — siempre termina siendo un espejo exacto de
    `origin/main`.
  - Tiene un archivo de lock (`auto_deploy.lock`) para que si una corrida
    todavía está haciendo un build lento, la siguiente ejecución programada no
    se superponga. Si el lock queda pisado por un crash, se ignora solo
    después de 30 minutos (no se puede trabar para siempre).
- **`register_auto_deploy_task.ps1`** — script de instalación: crea la Tarea
  Programada de Windows que llama a `auto_deploy.ps1` cada N minutos. Se corre
  **una sola vez**, a mano, al configurar el server.

### Requisitos previos

- El deploy manual con Docker (secciones de arriba) ya tiene que estar
  funcionando en esta máquina — el auto-deploy no reemplaza esa base, solo
  automatiza el `git pull` + `docker compose up -d --build` que hoy se hace a
  mano.
- El repo tiene que estar clonado con git en esta máquina (no como un zip
  descargado) y con `origin` apuntando al remoto real de GitHub.
- PowerShell con permisos de administrador, al menos para el paso de
  instalación (registrar la tarea).

### Instalación paso a paso (una sola vez)

1. Abrir PowerShell **como Administrador** en el server Windows.
2. Pararse en la carpeta del repo:
   ```powershell
   cd C:\ruta\al\repo
   ```
3. Confirmar que el repo está limpio y en la rama correcta antes de dejarlo en
   modo automático (de acá en más, cualquier cambio local sin commitear se
   pierde en la primera corrida):
   ```powershell
   git status
   git checkout main
   ```
4. Registrar la tarea:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\register_auto_deploy_task.ps1
   ```
   Por default corre cada 5 minutos. Para otro intervalo:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\register_auto_deploy_task.ps1 -IntervalMinutes 10
   ```

### Verificar que quedó bien instalado

```powershell
Get-ScheduledTask -TaskName "GEBA-ACS-AutoDeploy" | Get-ScheduledTaskInfo
```
Muestra la última y próxima corrida. Para forzar una corrida ya mismo (útil
para probar sin esperar los 5 minutos):
```powershell
Start-ScheduledTask -TaskName "GEBA-ACS-AutoDeploy"
```
Después revisar `auto_deploy.log` en la raíz del repo — si no hay commits
nuevos el archivo queda vacío/sin cambios, lo cual es el comportamiento
esperado, no un error.

### Probar el flujo completo

1. Desde otra máquina (tu entorno de desarrollo, no el server), hacer un
   commit chico y pushearlo a `main`.
2. En el server, forzar la corrida (`Start-ScheduledTask`, arriba) o esperar
   al próximo ciclo.
3. Revisar `auto_deploy.log` — debería aparecer una línea "Cambios
   detectados... Desplegando..." seguida de "Deploy OK".
4. Confirmar en `docker compose logs -f web` que el contenedor se reconstruyó
   y volvió a levantar.

### Operación día a día

- **Log**: `auto_deploy.log` en la raíz del repo (ignorado por git). Cada
  entrada tiene timestamp.
- **Pausar temporalmente** sin desinstalar:
  ```powershell
  Disable-ScheduledTask -TaskName "GEBA-ACS-AutoDeploy"
  Enable-ScheduledTask -TaskName "GEBA-ACS-AutoDeploy"   # para reactivar
  ```
- **Desinstalar del todo**:
  ```powershell
  Unregister-ScheduledTask -TaskName "GEBA-ACS-AutoDeploy" -Confirm:$false
  ```
- **Cambiar de rama** (ej. pasar de `main` a otra): editar `-Branch` en la
  llamada dentro de la tarea programada, o desregistrar y volver a registrar
  pasando el parámetro a `auto_deploy.ps1` (el script acepta `-Branch` y
  `-Remote`, pero `register_auto_deploy_task.ps1` hoy no los expone — si hace
  falta, hay que editar el `$action` de ese script para agregarlos al
  `-Argument`).

### Troubleshooting

| Síntoma | Causa probable | Qué hacer |
| --- | --- | --- |
| El log no aparece nunca, ni con commits nuevos pusheados | La tarea no está corriendo | `Get-ScheduledTaskInfo` — revisar `LastTaskResult` y `LastRunTime` |
| `git fetch falló` en el log | Sin internet, o credenciales de git vencidas (si el remoto es privado y usa un token) | Probar `git fetch` a mano en esa misma sesión de usuario |
| `git reset --hard falló` | Repo corrupto o carpeta `.git` con permisos raros | Revisar a mano con `git status` |
| `docker compose up --build falló` | Build roto (error de sintaxis, dependencia rota, etc.) | `docker compose logs -f web`, y correr el build a mano para ver el error completo: `docker compose up -d --build` |
| El deploy corre pero el sitio no refleja el cambio | El browser está cacheando estáticos viejos | Forzar refresh; si persiste, `docker compose exec web python manage.py collectstatic --noinput` |
| La tarea quedó "trabada" (no corre nunca más) | Lock viejo de una corrida que crasheó sin limpiar | Se autolibera solo a los 30 min; si hace falta ya, borrar `auto_deploy.lock` a mano |

### Cómo revertir un deploy que rompió algo

El script siempre deja el repo en el estado exacto de `origin/main`. Para
volver atrás:
```powershell
cd C:\ruta\al\repo
git log --oneline -10          # identificar el commit bueno anterior
git reset --hard <sha-bueno>
docker compose up -d --build
```
Ojo: en la próxima corrida programada, si `origin/main` sigue teniendo el
commit malo, el auto-deploy va a volver a traerlo. El rollback real hay que
hacerlo en GitHub (revert del commit, o mover `main`), no solo local.

### Limitaciones conocidas

- **No es instantáneo**: hasta `IntervalMinutes` de demora entre el push y el
  deploy real.
- **Requiere sesión de Windows iniciada**: la tarea corre con el usuario
  logueado (`LogonType S4U`) porque Docker Desktop necesita esa sesión activa.
  No se puede correr como `SYSTEM` en background puro salvo que se migre de
  Docker Desktop a Docker Engine standalone (WSL2 sin la capa de Desktop).
- **No hay rollback automático si el build falla**: si `docker compose up
  --build` falla, el repo ya quedó en el commit nuevo (el `reset --hard` corre
  antes que el build) pero los contenedores viejos siguen corriendo con la
  imagen anterior hasta que el build funcione. El sitio no se cae, pero
  tampoco actualiza hasta que se resuelva el error de build.
- **Sin notificación**: si algo falla, solo queda registrado en el log — nadie
  se entera hasta que lo revisa. Si se necesita alertas activas (mail,
  Telegram, etc.) hay que agregarlas al script.

## Notas
- Los estáticos los sirve **whitenoise** dentro de gunicorn (no hace falta nginx).
- Las **fotos de socios se guardan en la base** (bytea), no en disco: no hay volumen de media.
- La app funciona **sin** conexión a xSys (sirve el espejo local); el `sync` sólo la necesita
  en cada corrida y degrada limpio si no hay ruta.
- `entrypoint.sh` debe quedar con finales de línea **LF** (lo garantiza `.gitattributes`);
  si se edita en Windows con CRLF, el contenedor no arranca.

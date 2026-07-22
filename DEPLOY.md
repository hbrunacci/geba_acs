# Deploy de `geba_acs` (espejo xSys) en Windows con Docker + PostgreSQL

Runbook para poner en producción el sistema en la PC Windows que va a alojarlo.
El objetivo es que corra **sin VPN** (la PC está en la misma red que el MSSQL de
xSys) y que las pantallas de los puestos consulten a **esta** máquina, no al MSSQL.

> Todo lo pesado (Python, `pyodbc`, el **ODBC Driver 18**, Postgres) va **dentro
> de contenedores Docker**. La Windows host solo necesita **Docker Desktop** y
> **Git**. No hace falta instalar Python ni el ODBC Driver en Windows.

---

## 0. Arquitectura de lo que se levanta

`docker compose` levanta 4 servicios:

| Servicio | Qué hace | Puerto |
|----------|----------|--------|
| `db`     | PostgreSQL 16 (datos del espejo local) | `5434→5432` (solo local) |
| `web`    | La app Django (gunicorn) — UI, API, pantallas | **`8000`** |
| `sync`   | Sincroniza el espejo xSys cada 6 h (socios/fotos/lista blanca) | — |
| `poller` | **Único** cliente que lee `CD_ES` del MSSQL cada 1 s y lo replica local | — |

Las pantallas de los molinetes apuntan a `http://<IP-de-esta-PC>:8000/xsys/puerta/`.

---

## 1. Prerrequisitos en la PC Windows

### 1.1 Docker Desktop (con WSL2)
1. Instalar **Docker Desktop for Windows**: <https://www.docker.com/products/docker-desktop/>
2. Durante la instalación dejar activado el backend **WSL 2** (recomendado).
   Si pide instalar/actualizar WSL, aceptar y reiniciar.
3. Abrir Docker Desktop y esperar a que diga **"Engine running"**.
4. Verificar en PowerShell:
   ```powershell
   docker version
   docker compose version
   ```
   Ambos deben responder sin error.

### 1.2 Git
Instalar **Git for Windows**: <https://git-scm.com/download/win>

> ⚠️ **Importante (fin de línea):** este repo trae `entrypoint.sh` con saltos de
> línea Unix (LF). Si Git los convierte a CRLF al clonar en Windows, el
> contenedor falla con `bad interpreter`. Antes de clonar, ejecutá:
> ```powershell
> git config --global core.autocrlf false
> ```

### 1.3 (Opcional pero recomendado) Node.js + Claude Code
Solo si querés tener a Claude Code corriendo **en esta máquina** para asistir el
deploy y el troubleshooting. Ver **Apéndice A** al final. `npm` "no da resultado"
porque Node.js todavía no está instalado — el apéndice lo cubre.
**No es necesario para que el sistema funcione**; el deploy se puede hacer solo
con los pasos 2–7.

---

## 2. Obtener el código

En PowerShell, parado en la carpeta donde quieras alojar el proyecto (ej. `C:\geba`):

```powershell
cd C:\geba
git clone https://github.com/hbrunacci/geba_acs.git
cd geba_acs
```

> Se usa la URL **HTTPS** (no la SSH `git@github.com:...`) para no depender de
> tener claves SSH configuradas en esta PC. Puede pedir usuario/token de GitHub.

---

## 3. Configurar el archivo `.env`

El repo trae `.env.dist` como plantilla. Copialo a `.env` y editá los valores
reales (nunca commitear `.env` — ya está en `.gitignore` y `.dockerignore`).

```powershell
copy .env.dist .env
notepad .env
```

Valores que **hay que** cambiar sí o sí:

```ini
# --- Django ---
DJANGO_SECRET_KEY=<una-clave-larga-y-aleatoria>
DJANGO_DEBUG=0
# La IP de ESTA PC en la red + localhost (o dejar * si es una LAN cerrada)
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,10.0.0.27
# Necesario para el login del admin accediendo por IP:
DJANGO_CSRF_TRUSTED_ORIGINS=http://10.0.0.27:8000

# --- PostgreSQL (contenedor local; poné una password propia) ---
POSTGRES_DB=geba_acs
POSTGRES_USER=geba_acs
POSTGRES_PASSWORD=<password-postgres>

# --- MSSQL xSys (lectura del ERP; el puerto REAL es 49331, no 1433) ---
MSSQL_XSYS_ENABLED=1
MSSQL_XSYS_HOST=192.168.0.6
MSSQL_XSYS_PORT=49331
MSSQL_XSYS_DATABASE=xsys_geba
MSSQL_XSYS_USER=sa
MSSQL_XSYS_PASSWORD=<password-mssql>
MSSQL_XSYS_ENCRYPT=no
MSSQL_XSYS_TRUST_CERT=yes

# --- MSSQL Access Log (poller de CD_ES) — mismas credenciales/host ---
MSSQL_ACCESS_LOG_ENABLED=1
MSSQL_ACCESS_LOG_HOST=192.168.0.6
MSSQL_ACCESS_LOG_PORT=49331
MSSQL_ACCESS_LOG_DATABASE=xsys_geba
MSSQL_ACCESS_LOG_USER=sa
MSSQL_ACCESS_LOG_PASSWORD=<password-mssql>

# --- BioStar 2 (si se usa lookup facial) ---
BIOSTAR_BASE_URL=https://10.0.0.27
BIOSTAR_USERNAME=admin
BIOSTAR_PASSWORD=<password-biostar>
```

> La IP de esta PC se asume `10.0.0.27` (verificala con `ipconfig`). Si esta
> máquina es **la misma** que hospeda BioStar (BioStar también responde en
> `10.0.0.27`), no hay conflicto de puertos —BioStar usa 443/HTTPS y esta app el
> 8000— pero tenelo presente al configurar firewall/antivirus.
> `MSSQL_ACCESS_LOG_PORT` en el `.env.dist` viene en `1433`; **cambialo a `49331`**
> igual que el bloque xSys, o el poller no conecta.

---

## 4. Verificar conectividad al MSSQL (antes de levantar nada)

Desde PowerShell, confirmar que esta PC llega al SQL Server por el puerto real:

```powershell
Test-NetConnection 192.168.0.6 -Port 49331
```

Debe decir `TcpTestSucceeded : True`. Si da `False`, el problema es de red/firewall
(no de la app): revisar que la PC esté en la red correcta y sin VPN interfiriendo,
antes de seguir.

> ⚠️ Esta PC (`10.0.0.x`) y el MSSQL (`192.168.0.6`) están en **subredes
> distintas**. Para que `TcpTestSucceeded` sea `True` tiene que existir **ruteo
> entre `10.0.0.0/24` y `192.168.0.0/24`** (gateway/firewall que las una). Si el
> test da `False`, es exactamente ese ruteo lo que falta —no algo de la app— y hay
> que resolverlo a nivel de red antes de continuar.

---

## 5. Build + arranque

```powershell
docker compose build
docker compose up -d
```

El `entrypoint.sh` corre solo, dentro del contenedor `web`:
espera a Postgres → `migrate` → `collectstatic`. Puede tardar en la primera vez.

Verificar que los 4 servicios estén arriba:

```powershell
docker compose ps
```

`db` y `web` deben figurar `healthy`. Ver logs si algo no arranca:

```powershell
docker compose logs -f web
docker compose logs -f poller
```

---

## 6. Carga inicial del espejo (`xsys_init`)

El `poller` ya empieza a traer accesos en vivo. Falta la **población inicial**
(socios, fotos, lista blanca). Se corre **una vez** dentro del contenedor `web`:

**Opción rápida (recomendada para arrancar ya):** siembra la lista blanca desde
xSys sin recalcular socio por socio; el `sync` la irá recalculando después.

```powershell
docker compose exec web python manage.py xsys_init --seed-whitelist --no-recompute-whitelist --with-movements
```

**Opción completa (más lenta, recalcula todo — ideal de noche):**

```powershell
docker compose exec web python manage.py xsys_init --with-movements
```

Al terminar imprime un resumen con los conteos (socios, fotos, whitelist, etc.).

### Crear el usuario admin (para configurar puertas/molinetes)

```powershell
docker compose exec web python manage.py createsuperuser
```

---

## 7. Verificación end-to-end

1. **App viva:** abrir en el navegador de la PC `http://localhost:8000/` — debe cargar.
2. **Admin:** `http://localhost:8000/admin/` → login con el superusuario.
3. **Datos espejados:** en el admin, entrar a *Interfaz xSys* y confirmar que hay
   socios y fotos cargados.
4. **Poller en vivo:** `docker compose logs -f poller` debe mostrar líneas
   `+N ingresos` cuando alguien pasa por un molinete.
5. **Pantalla de puesto:** desde una PC de los molinetes abrir
   `http://<IP-de-esta-PC>:8000/xsys/puerta/`, usar el **menú hamburguesa** para
   elegir la puerta (ej. SM-Alcorta) y verificar que aparecen las columnas por
   molinete con foto + mensaje + ícono de rostro/credencial.

> Cada pantalla genera y guarda su **token** en el navegador. La asociación
> pantalla→puerta se hace desde ese menú. Los **grupos de molinetes** (columnas)
> se administran logueado como admin.

---

## 8. Operación diaria

```powershell
# Ver estado
docker compose ps

# Logs
docker compose logs -f web
docker compose logs -f poller
docker compose logs -f sync

# Reiniciar todo (ej. tras cambiar el .env)
docker compose up -d

# Detener / arrancar
docker compose stop
docker compose start

# Forzar una sincronización manual del espejo (sin esperar las 6 h)
docker compose exec web python manage.py xsys_sync
```

### Actualizar a una versión nueva del código

```powershell
git pull
docker compose build
docker compose up -d
```

### Backup de la base local (Postgres)

```powershell
docker compose exec db pg_dump -U geba_acs geba_acs > backup_geba_acs.sql
```

> Los datos de Postgres viven en el volumen `postgres_data`; sobreviven a
> `docker compose down`. Solo se borran con `docker compose down -v` (⚠️ no usar
> salvo que quieras empezar de cero).

---

## 9. Troubleshooting

| Síntoma | Causa probable / solución |
|---------|---------------------------|
| `web` reinicia en loop, log dice `entrypoint.sh: bad interpreter` | El `.sh` quedó en CRLF. Ejecutar `git config --global core.autocrlf false` y volver a clonar (paso 1.2). |
| `xsys_init`/`poller` no conectan al MSSQL | `Test-NetConnection 192.168.0.6 -Port 49331` (paso 4). Si es `False`, es red/firewall. Si es `True`, revisar usuario/password en `.env`. |
| Error de handshake TLS contra el MSSQL | Confirmar `MSSQL_XSYS_ENCRYPT=no` y `MSSQL_XSYS_TRUST_CERT=yes` (obligatorios en el 49331). |
| El navegador dice `DisallowedHost` / `Bad Request (400)` | Agregar la IP de la PC a `DJANGO_ALLOWED_HOSTS` en `.env` y `docker compose up -d`. |
| El login del admin falla con error CSRF por IP | Setear `DJANGO_CSRF_TRUSTED_ORIGINS=http://<IP>:8000` en `.env`. |
| Puerto 8000 ocupado | Cambiar el mapeo del servicio `web` en `docker-compose.yml` (`"8080:8000"`) y usar ese puerto en las pantallas. |
| No aparecen ingresos en la pantalla | Ver `docker compose logs -f poller`; confirmar que trae eventos y que la pantalla tiene la puerta correcta seleccionada. |
| Postgres: `FATAL: password authentication failed for user "geba_acs"` (o `role ... does not exist`) | El volumen `postgres_data` ya se había inicializado con **otras** credenciales. Ver nota abajo. |

### ⚠️ El volumen de PostgreSQL solo se inicializa una vez

PostgreSQL crea el usuario/base de `POSTGRES_USER` / `POSTGRES_DB` / `POSTGRES_PASSWORD`
**únicamente la primera vez** que el volumen `postgres_data` está vacío. Si después
cambiás esas variables en el `.env`, el contenedor **no** las re-aplica: sigue con
las credenciales viejas y verás `password authentication failed` o `role ... does
not exist`.

- **Si la base está vacía o es descartable** (típico en la primera puesta a punto):
  recreá el volumen limpio.
  ```powershell
  docker compose down -v      # ⚠️ BORRA los datos de Postgres
  docker compose up -d
  ```
  Después hay que volver a correr `xsys_init` (paso 6), porque la base arranca vacía.

- **Si la base ya tiene datos de producción que no querés perder**, NO uses `down -v`.
  En su lugar, cambiá la contraseña del rol dentro del contenedor para que coincida
  con el `.env`:
  ```powershell
  docker compose exec db psql -U <usuario-viejo> -d postgres -c "ALTER USER geba_acs WITH PASSWORD 'la-del-.env';"
  ```

> Moraleja: definí `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` en el `.env`
> **antes** del primer `docker compose up`, y no los cambies después a la ligera.

---

## Apéndice A — Instalar Claude Code en esta PC Windows

Opcional, para tener asistencia de IA con acceso real a la terminal de esta máquina.

1. **Instalar Node.js LTS** (esto es lo que falta cuando `npm` "no da resultado"):
   descargar el instalador MSI de <https://nodejs.org/> (versión **LTS**) e instalarlo
   con las opciones por defecto. Cerrar y reabrir PowerShell y verificar:
   ```powershell
   node --version
   npm --version
   ```
2. **Instalar Claude Code:**
   ```powershell
   npm install -g @anthropic-ai/claude-code
   ```
3. **Abrirlo dentro del repo y loguearte:**
   ```powershell
   cd C:\geba\geba_acs
   claude
   ```
   Seguí el flujo de login la primera vez.
4. Una vez dentro, puede leer este `DEPLOY.md` y ayudarte a ejecutar y diagnosticar
   los pasos 2–8 directamente sobre esta máquina.

> Nota: esa instancia de Claude Code arranca sin el contexto de esta conversación;
> este archivo `DEPLOY.md` es justamente el hand-off para que retome el trabajo.

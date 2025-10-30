# Control de accesos GEBA

Este proyecto implementa una API basada en Django y Django REST Framework para gestionar el ingreso a las sedes de un club social y deportivo. Se incluyen endpoints protegidos para administrar personas, sedes, accesos, dispositivos, eventos, invitaciones y la lista blanca de autorizaciones. La solución se organiza en tres aplicaciones: `people` (personas e invitaciones), `institutions` (estructura física y eventos) y `access_control` (reglas de ingreso).

## Requisitos

- Python 3.12 o superior
- PostgreSQL 14+ (se provee un contenedor vía Docker Compose)
- `pip` actualizado

## Configuración rápida

1. Copiar el archivo `.env.example` a `.env` y ajustar los valores según el entorno.
2. Levantar la base de datos PostgreSQL con Docker Compose:

   ```bash
   docker compose up -d
   ```

3. Ejecutar el script de inicialización para crear el entorno virtual, instalar dependencias y aplicar migraciones:

   ```bash
   ./scripts/setup.sh
   ```

   En Windows, utilice el script equivalente para PowerShell:

   ```powershell
   pwsh -ExecutionPolicy Bypass -File .\scripts\setup.ps1
   ```

   (Si no cuenta con PowerShell 7, puede reemplazar `pwsh` por `powershell`.)

   Ambos scripts aceptan las variables `PYTHON_BIN` y `VENV_DIR` para personalizar la ruta del intérprete y el nombre del entorno virtual.

4. Crear un superusuario estableciendo previamente la contraseña como variable de entorno (o ejecutando el comando sin la opción `--noinput`):

   ```bash
   source .venv/bin/activate
   DJANGO_SUPERUSER_PASSWORD=adminpass python manage.py createsuperuser --username admin --email admin@example.com --noinput
   ```

5. Ejecutar el servidor de desarrollo:

   ```bash
   python manage.py runserver
   ```

## Autenticación

Todos los endpoints de la API exigen autenticación. Utilice el endpoint `/api/auth/token/` para obtener un token con un usuario válido:

```http
POST /api/auth/token/
Content-Type: application/json

{"username": "admin", "password": "adminpass"}
```

Incluya el token en el encabezado `Authorization: Token <token>` para consumir las APIs.

## Endpoints principales

Los recursos disponibles (todos vía `ModelViewSet`) son:

- `/api/persons/` — Personas y sus datos básicos (socios, empleados, proveedores, invitados).
- `/api/sites/` — Sedes de la institución.
- `/api/access-points/` — Accesos por sede.
- `/api/access-devices/` — Dispositivos (molinetes/puertas) y tipos de lector.
- `/api/events/` — Eventos con ventanas de vigencia y categorías permitidas.
- `/api/guest-invitations/` — Invitaciones para personas registradas como invitados.
- `/api/whitelist/` — Autorizaciones por persona, acceso y evento.
- `/api/external-access/latest/` — Últimos ingresos registrados en el sistema externo MSSQL.

Todos los modelos están disponibles desde el administrador de Django (`/admin/`). El proyecto utiliza los grupos y usuarios estándar de Django para gestionar permisos de acceso.

## Pruebas automatizadas

Se incluye un conjunto de pruebas para todos los endpoints. Ejecútelas con:

```bash
python manage.py test
```

## Base de datos

Por defecto, el proyecto usa SQLite para facilitar la ejecución local. Al definir las variables `POSTGRES_*` se activará la base de datos PostgreSQL. Consulte `docker-compose.yml` y `.env.example` para más detalles.

## Integración con los registros externos de acceso

El endpoint `/api/external-access/latest/` devuelve los últimos ingresos sincronizados en la tabla local `access_control_externalaccesslogentry`. Los datos provienen de la tabla `CD_ES` de la base de datos MSSQL utilizada por el sistema de control físico.

Para mantener la información actualizada sin bloquear las solicitudes HTTP se incluye un proceso asíncrono que replica periódicamente los movimientos externos. Inícielo con:

```bash
python manage.py sync_external_access_logs --interval 15 --limit 100
```

El parámetro `--interval` define el tiempo (en segundos) entre sincronizaciones y `--limit` la cantidad máxima de registros a importar por ciclo. Puede omitir ambos parámetros para usar los valores por defecto (30 segundos y el límite configurado en Django). El endpoint acepta además el parámetro opcional `limit` para ajustar la cantidad de resultados devueltos en cada respuesta.

Configure las siguientes variables de entorno para definir la conexión al origen MSSQL:

| Variable | Valor por defecto | Descripción |
| --- | --- | --- |
| `MSSQL_ACCESS_LOG_ENABLED` | `1` | Habilita (1) o deshabilita (0) la integración. |
| `MSSQL_ACCESS_LOG_HOST` | `192.168.0.6` | Dirección IP o nombre del servidor MSSQL. |
| `MSSQL_ACCESS_LOG_PORT` | `1433` | Puerto TCP del servicio MSSQL. |
| `MSSQL_ACCESS_LOG_DATABASE` | `xsys_geba` | Base de datos a consultar. |
| `MSSQL_ACCESS_LOG_USER` | `sa` | Usuario con permisos de lectura. |
| `MSSQL_ACCESS_LOG_PASSWORD` | `kvy2012*.` | Contraseña del usuario. |
| `MSSQL_ACCESS_LOG_TABLE` | `CD_ES` | Tabla donde se almacenan los accesos. |
| `MSSQL_ACCESS_LOG_DRIVER` | `{ODBC Driver 18 for SQL Server}` | Driver ODBC a utilizar. |
| `MSSQL_ACCESS_LOG_DEFAULT_LIMIT` | `10` | Cantidad predeterminada de registros a replicar y devolver. |

Es necesario tener instalado el paquete `pyodbc` y el driver ODBC correspondiente para establecer la conexión.

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

Todos los modelos están disponibles desde el administrador de Django (`/admin/`). El proyecto utiliza los grupos y usuarios estándar de Django para gestionar permisos de acceso.

## Pruebas automatizadas

Se incluye un conjunto de pruebas para todos los endpoints. Ejecútelas con:

```bash
python manage.py test
```

## Base de datos

Por defecto, el proyecto usa SQLite para facilitar la ejecución local. Al definir las variables `POSTGRES_*` se activará la base de datos PostgreSQL. Consulte `docker-compose.yml` y `.env.example` para más detalles.

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

## Notas
- Los estáticos los sirve **whitenoise** dentro de gunicorn (no hace falta nginx).
- Las **fotos de socios se guardan en la base** (bytea), no en disco: no hay volumen de media.
- La app funciona **sin** conexión a xSys (sirve el espejo local); el `sync` sólo la necesita
  en cada corrida y degrada limpio si no hay ruta.
- `entrypoint.sh` debe quedar con finales de línea **LF** (lo garantiza `.gitattributes`);
  si se edita en Windows con CRLF, el contenedor no arranca.

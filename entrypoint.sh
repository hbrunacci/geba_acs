#!/usr/bin/env bash
set -e

# Espera a que Postgres acepte conexiones (si está configurado).
if [ -n "${POSTGRES_DB:-}" ]; then
    echo "Esperando Postgres en ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}..."
    python - <<'PY'
import os, socket, time
host = os.getenv("POSTGRES_HOST", "db")
port = int(os.getenv("POSTGRES_PORT", "5432"))
for _ in range(60):
    try:
        with socket.create_connection((host, port), timeout=2):
            break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit(f"Postgres no respondió en {host}:{port}")
print("Postgres OK")
PY
fi

# Migraciones + estáticos (idempotentes).
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Superusuario opcional (si se pasan las variables DJANGO_SUPERUSER_*).
if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
    python manage.py createsuperuser --noinput || true
fi

exec "$@"

# Imagen de la app geba_acs (Django + xSys).
# Incluye el Microsoft ODBC Driver 18, imprescindible para que pyodbc llegue a la
# base MSSQL de xSys. Corre en Docker Desktop (Windows/Linux, contenedor Linux).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# --- Dependencias de sistema + ODBC Driver 18 (repo Microsoft, Debian 12) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg apt-transport-https ca-certificates \
        build-essential unixodbc-dev libpq5 \
    && curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get purge -y --auto-remove curl gnupg apt-transport-https \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Dependencias Python (capa cacheable) ---
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# --- Código ---
COPY . .

# Usuario no-root + entrypoint ejecutable
RUN chmod +x /app/entrypoint.sh \
    && useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "acs.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]

#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=${VENV_DIR:-.venv}

if [ ! -d "${VENV_DIR}" ]; then
    echo "Creating virtual environment in ${VENV_DIR}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1090
source "${VENV_DIR}/bin/activate"

pip install --upgrade pip
pip install -r requirements.txt

python manage.py migrate

cat <<'MSG'
Se aplicaron las migraciones. Para crear un superusuario ejecute:

    source ${VENV_DIR}/bin/activate
    DJANGO_SUPERUSER_PASSWORD=<password> python manage.py createsuperuser --username admin --email admin@example.com --noinput

MSG

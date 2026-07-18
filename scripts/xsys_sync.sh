#!/usr/bin/env bash
#
# Wrapper de cron para la sincronización incremental del espejo xSys.
# NO maneja VPN: en producción el server está en la misma red del MSSQL.
# En dev, levantá la VPN por afuera antes de correrlo.
#
# Variables (con defaults):
#   XSYS_PROJECT_DIR  raíz del proyecto Django (default: dir del script/..)
#   XSYS_PYTHON       intérprete/venv (default: python3)
#   XSYS_LOCKFILE     lock de single-flight (default: /tmp/xsys_sync.lock)
#   XSYS_LOG          archivo de log (default: $XSYS_PROJECT_DIR/xsys_sync.log)
#   XSYS_SYNC_ARGS    args extra para manage.py xsys_sync (ej: "--limit 500")
#
# Crontab ejemplo (cada 6 h):
#   0 */6 * * * /ruta/al/proyecto/scripts/xsys_sync.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
XSYS_PROJECT_DIR="${XSYS_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
XSYS_PYTHON="${XSYS_PYTHON:-python3}"
XSYS_LOCKFILE="${XSYS_LOCKFILE:-/tmp/xsys_sync.lock}"
XSYS_LOG="${XSYS_LOG:-$XSYS_PROJECT_DIR/xsys_sync.log}"
XSYS_SYNC_ARGS="${XSYS_SYNC_ARGS:-}"

log() { echo "$(date -Is) $*" >>"$XSYS_LOG"; }

# single-flight: si ya hay una corrida activa, salir sin error.
exec 9>"$XSYS_LOCKFILE"
if ! flock -n 9; then
    log "ya hay una sincronización en curso, salteando."
    exit 0
fi

log "inicio sync"
if "$XSYS_PYTHON" "$XSYS_PROJECT_DIR/manage.py" xsys_sync $XSYS_SYNC_ARGS >>"$XSYS_LOG" 2>&1; then
    log "fin sync OK"
else
    rc=$?
    log "fin sync ERROR (rc=$rc)"
    exit "$rc"
fi

# Sirve la app geba_acs en modo NATIVO (sin Docker) con waitress en el puerto 80.
# Pensado para correr como Tarea Programada de Windows (ver register_native_services.ps1).
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
Set-Location $repo   # cwd = raíz del repo para que se lea el .env
$serve = Join-Path $repo ".venv\Scripts\waitress-serve.exe"
& $serve --listen=0.0.0.0:80 --threads=8 acs.wsgi:application

# Poller en tiempo real de CD_ES (modo NATIVO, sin Docker).
# Pensado para correr como Tarea Programada de Windows (ver register_native_services.ps1).
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
Set-Location $repo
$py = Join-Path $repo ".venv\Scripts\python.exe"
& $py manage.py xsys_poll --interval 1

<#
  Envío del reporte de acreditados de la elección por correo.

  Lo corre la tarea "GEBA-ACS-Elecciones-Reporte" cada hora durante la jornada
  (ver register_elecciones_envio_task.ps1). Genera el PDF con el corte del
  momento y lo manda a los destinatarios permitidos (EMAIL_DESTINATARIOS_PERMITIDOS).

  -Prueba: genera el PDF pero no lo manda.
#>
param([switch]$Prueba)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
$logDir = Join-Path $repoRoot "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "elecciones_envio.log"

Set-Location $repoRoot
$args_ = @("compose", "exec", "-T", "web", "python", "manage.py", "elecciones_reporte")
if (-not $Prueba) { $args_ += "--enviar" }

$inicio = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$salida = & docker @args_ 2>&1 | Where-Object { $_ -notmatch "warn" } | Out-String
Add-Content -Path $log -Encoding utf8 -Value ("[$inicio] exit=$LASTEXITCODE`r`n" + $salida.Trim())

# Un reintento si falló (el servidor de correo del club a veces corta).
if ($LASTEXITCODE -ne 0 -and -not $Prueba) {
    Start-Sleep -Seconds 60
    $salida = & docker @args_ 2>&1 | Where-Object { $_ -notmatch "warn" } | Out-String
    Add-Content -Path $log -Encoding utf8 -Value ("[reintento] exit=$LASTEXITCODE`r`n" + $salida.Trim())
}
exit $LASTEXITCODE

param(
    [string]$TaskName = "GEBA-ACS-WebWatchdog",
    [int]$IntervalMinutes = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Requiere PowerShell como Administrador (igual que register_auto_deploy_task.ps1:
# el módulo ScheduledTasks lo exige para registrar tareas S4U).

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
$scriptPath = Join-Path $repoRoot "scripts\web_watchdog.ps1"

if (-not (Test-Path $scriptPath)) {
    throw "No se encontró $scriptPath"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" `
    -WorkingDirectory $repoRoot

# Duración finita y larga (10 años) en vez de [TimeSpan]::MaxValue: ese máximo se
# serializa como P99999999DT23H59M59S y el Programador de tareas de este Windows
# lo rechaza con "valor fuera de intervalo". Mismo problema tiene
# register_auto_deploy_task.ps1, y por eso esa tarea nunca quedó registrada.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

# Mismo criterio que el auto-deploy: corre con el usuario actual porque Docker
# Desktop vive en su sesión; SYSTEM no lo ve.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U `
    -RunLevel Highest

# Sin esto el script mentía: Register-ScheduledTask devuelve el "Acceso denegado"
# como error NO terminante de CIM, así que $ErrorActionPreference = 'Stop' no lo
# frena y se imprimía "Tarea registrada" con la tarea sin registrar. Se avisa
# antes de intentar, y se comprueba después contra el Programador de tareas.
$identidad = [Security.Principal.WindowsIdentity]::GetCurrent()
$esAdmin = (New-Object Security.Principal.WindowsPrincipal($identidad)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $esAdmin) {
    throw ("Hay que correr esto en una PowerShell COMO ADMINISTRADOR: registrar " +
           "una tarea S4U lo exige. Usuario actual: " + $identidad.Name)
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Cada $IntervalMinutes min chequea que el visor responda en el puerto 80; si no, lo levanta con docker compose y avisa por mail." `
    -Force | Out-Null

if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    throw "Register-ScheduledTask no dejó la tarea '$TaskName' creada. Revisar el error de arriba."
}

Write-Host "Tarea '$TaskName' registrada. Corre cada $IntervalMinutes minutos."
Write-Host "Ver estado:   Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "Correr ahora: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Pausar:       New-Item '$repoRoot\web_watchdog.pause' -ItemType File"
Write-Host "Desregistrar: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host "Log:          $repoRoot\web_watchdog.log"

param(
    [string]$TaskName = "GEBA-ACS-AutoDeploy",
    [int]$IntervalMinutes = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Requiere PowerShell como Administrador (el modulo ScheduledTasks lo exige
# para registrar tareas que corren se pida el usuario esté logueado o no).

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
$scriptPath = Join-Path $repoRoot "scripts\auto_deploy.ps1"

if (-not (Test-Path $scriptPath)) {
    throw "No se encontró $scriptPath"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" `
    -WorkingDirectory $repoRoot

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

# Corre con el usuario actual; para que ande sin sesión iniciada, cambiar a
# -User "SYSTEM" (requiere que ese usuario tenga permisos sobre Docker Desktop,
# lo cual normalmente NO es el caso salvo que se use el Docker Engine standalone).
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Chequea cada $IntervalMinutes min si hay commits nuevos en origin/main y, si los hay, hace git reset --hard + docker compose up -d --build." `
    -Force | Out-Null

Write-Host "Tarea '$TaskName' registrada. Corre cada $IntervalMinutes minutos."
Write-Host "Ver estado:   Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "Correr ahora: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Desregistrar: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host "Log:          $repoRoot\auto_deploy.log"

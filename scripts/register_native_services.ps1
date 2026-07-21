param(
    [string]$WebTask = "GEBA-ACS-Web",
    [string]$PollerTask = "GEBA-ACS-Poller"
)

# Registra la app (waitress :80) y el poller CD_ES como Tareas Programadas que
# arrancan con Windows y corren esté el usuario logueado o no (modo NATIVO, sin
# Docker). Requiere PowerShell como Administrador.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath

function Register-NativeService {
    param([string]$Name, [string]$Script, [string]$Desc)

    $scriptPath = Join-Path $repo $Script
    if (-not (Test-Path $scriptPath)) { throw "No existe $scriptPath" }

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`"" `
        -WorkingDirectory $repo

    # Arranca con Windows; StartWhenAvailable cubre reinicios perdidos.
    $trigger = New-ScheduledTaskTrigger -AtStartup

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

    # S4U: corre sin sesión interactiva. RunLevel Highest: puede bindear el 80.
    $principal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType S4U `
        -RunLevel Highest

    Register-ScheduledTask `
        -TaskName $Name -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal `
        -Description $Desc -Force | Out-Null

    Write-Host "Tarea '$Name' registrada."
}

Register-NativeService -Name $WebTask   -Script "scripts\native_web.ps1"    -Desc "geba_acs web (waitress :80, modo nativo)"
Register-NativeService -Name $PollerTask -Script "scripts\native_poller.ps1" -Desc "geba_acs poller CD_ES (modo nativo)"

Start-ScheduledTask -TaskName $WebTask
Start-ScheduledTask -TaskName $PollerTask
Write-Host "Tareas iniciadas."
Write-Host "Estado:      Get-ScheduledTask GEBA-ACS-* | Get-ScheduledTaskInfo"
Write-Host "Detener:     Stop-ScheduledTask -TaskName '$WebTask'; Stop-ScheduledTask -TaskName '$PollerTask'"
Write-Host "Desregistrar: Unregister-ScheduledTask -TaskName '$WebTask','$PollerTask' -Confirm:`$false"

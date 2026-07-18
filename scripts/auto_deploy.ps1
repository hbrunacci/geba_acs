param(
    [string]$Branch = "main",
    [string]$Remote = "origin",
    [string]$LogPath = "auto_deploy.log"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $LogPathResolved -Value $line
}

$initialLocation = Get-Location
try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
    Set-Location $repoRoot

    if ([System.IO.Path]::IsPathRooted($LogPath)) {
        $LogPathResolved = $LogPath
    } else {
        $LogPathResolved = Join-Path $repoRoot $LogPath
    }

    # Evita corridas superpuestas si una corrida anterior (build lento) sigue viva.
    $lockPath = Join-Path $repoRoot "auto_deploy.lock"
    if (Test-Path $lockPath) {
        $lockAgeMinutes = ((Get-Date) - (Get-Item $lockPath).LastWriteTime).TotalMinutes
        if ($lockAgeMinutes -lt 30) {
            Write-Log "Ya hay una corrida en curso (lock de hace $([math]::Round($lockAgeMinutes,1)) min). Salgo."
            exit 0
        }
        Write-Log "Lock viejo (>30 min), lo descarto y sigo."
    }
    New-Item -Path $lockPath -ItemType File -Force | Out-Null

    try {
        git fetch $Remote $Branch --quiet
        if ($LASTEXITCODE -ne 0) {
            Write-Log "git fetch falló (exit $LASTEXITCODE). Reintento en la próxima corrida."
            exit 1
        }

        $localSha = (git rev-parse HEAD).Trim()
        $remoteSha = (git rev-parse "$Remote/$Branch").Trim()

        if ($localSha -eq $remoteSha) {
            # Sin cambios: no logueamos para no ensuciar el archivo cada corrida.
            exit 0
        }

        Write-Log "Cambios detectados: $localSha -> $remoteSha. Desplegando..."

        # reset --hard en vez de pull: esta máquina es un destino de deploy, no
        # tiene que tener commits locales propios ni resolver merges.
        git reset --hard "$Remote/$Branch"
        if ($LASTEXITCODE -ne 0) {
            Write-Log "git reset --hard falló (exit $LASTEXITCODE). Aborto el deploy."
            exit 1
        }

        docker compose up -d --build
        if ($LASTEXITCODE -ne 0) {
            Write-Log "docker compose up --build falló (exit $LASTEXITCODE). Revisar logs de Docker."
            exit 1
        }

        Write-Log "Deploy OK. HEAD ahora en $remoteSha."
    }
    finally {
        Remove-Item -Path $lockPath -Force -ErrorAction SilentlyContinue
    }
}
finally {
    Set-Location $initialLocation
}

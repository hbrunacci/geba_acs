param(
    [string]$PythonBin = "python",
    [string]$VenvDir = ".venv"
)

set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$initialLocation = Get-Location
try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
    Set-Location $repoRoot

    if (-not [System.IO.Path]::IsPathRooted($VenvDir)) {
        $VenvDir = Join-Path $repoRoot $VenvDir
    }

    if (-not (Test-Path -Path $VenvDir -PathType Container)) {
        Write-Host "Creando entorno virtual en $VenvDir"
        & $PythonBin -m venv $VenvDir
    }

    $venvScripts = Join-Path $VenvDir "Scripts"
    $activatePath = Join-Path $venvScripts "Activate.ps1"
    if (-not (Test-Path -Path $activatePath -PathType Leaf)) {
        throw "No se encontró el script de activación en '$activatePath'."
    }

    . $activatePath

    $venvPython = Join-Path $venvScripts "python.exe"

    $requirementsPath = Join-Path $repoRoot "requirements.txt"
    $managePath = Join-Path $repoRoot "manage.py"

    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r $requirementsPath
    & $venvPython $managePath migrate

    Write-Host @'
Se aplicaron las migraciones. Para crear un superusuario ejecute:

    . {0}
    $env:DJANGO_SUPERUSER_PASSWORD=<password>
    python manage.py createsuperuser --username admin --email admin@example.com --noinput
'@ -f $activatePath
}
finally {
    Set-Location $initialLocation
}

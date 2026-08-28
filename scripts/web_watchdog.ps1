<#
.SYNOPSIS
    Vigila que el visor responda en el puerto 80 y lo levanta si no.

.DESCRIPTION
    El 18/08/2026 el contenedor `web` quedó parado cinco horas sin que nadie se
    enterara: alguien lo frenó a mano y `restart: unless-stopped` no lo vuelve a
    levantar nunca (es el comportamiento correcto de esa política, pero deja la
    puerta abierta a que quede caído para siempre). El backend seguía sano, así
    que `acs_consistencia` tampoco lo detectaba: mide frescura de datos, y los
    pollers estaban ingiriendo bien.

    Cada corrida:
      1. Pide la home y espera un 200.
      2. Si no responde, loguea, hace `docker compose up -d web` y vuelve a
         probar.
      3. Avisa por mail el cambio de estado (caído / recuperado / no se pudo
         levantar).

    Cuando todo está bien NO escribe nada, para que el log sea sólo incidentes.

.NOTES
    Config en el .env del repo (gitignored, es donde viven los secretos):
      WATCHDOG_URL           default http://127.0.0.1/
      WATCHDOG_SMTP_HOST     ej. smtp.gmail.com  (vacío = sin mail)
      WATCHDOG_SMTP_PORT     default 587
      WATCHDOG_SMTP_USER     ej. hbrunacci@gmail.com
      WATCHDOG_SMTP_PASS     app password de 16 caracteres, NO la de la cuenta
      WATCHDOG_MAIL_TO       destinatario (default = SMTP_USER)

    Para frenar el auto-levantado durante un mantenimiento, crear el archivo
    `web_watchdog.pause` en la raíz del repo. El watchdog sigue avisando pero no
    toca el contenedor. Borrarlo para reanudar.
#>
param(
    [string]$Service = "web",
    # Pisa WATCHDOG_URL del .env. Sirve para probar el camino de "está caído"
    # apuntando a un puerto muerto, sin tener que frenar el visor de verdad.
    [string]$Url = "",
    [int]$TimeoutSec = 20,
    [int]$EsperaTrasLevantarSec = 45,
    # Cada cuánto reinsistir por mail mientras siga caído (no en cada corrida).
    [int]$ReavisoMinutos = 60,
    [string]$LogPath = "web_watchdog.log"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
if ([System.IO.Path]::IsPathRooted($LogPath)) {
    $LogPathResolved = $LogPath
} else {
    $LogPathResolved = Join-Path $repoRoot $LogPath
}
$statePath = Join-Path $repoRoot "web_watchdog.state"
$pausePath = Join-Path $repoRoot "web_watchdog.pause"

function Write-Log {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $LogPathResolved -Value $line
}

function Invoke-Nativo {
    <# Corre un ejecutable y devuelve @{ salida; codigo }.

       En PowerShell 5.1, con $ErrorActionPreference = "Stop", redirigir el
       stderr de un .exe (`2>&1`) envuelve cada línea en un ErrorRecord y la
       convierte en excepción terminante — y `docker compose` manda TODO su
       progreso ("Container ... Running") por stderr, así que una corrida
       perfectamente normal abortaba el script. Se baja la preferencia sólo
       alrededor de la llamada nativa. #>
    param([scriptblock]$Comando)
    $previo = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $salida = (& $Comando 2>&1 | Out-String).Trim()
        return @{ salida = $salida; codigo = $LASTEXITCODE }
    } finally {
        $ErrorActionPreference = $previo
    }
}

# --------------------------------------------------------------------- config

function Read-DotEnv {
    <# Lee el .env del repo a un hashtable. Sin .env se devuelve vacío y el
       watchdog igual sirve: loguea y levanta, sólo no manda mail. #>
    $cfg = @{}
    $envPath = Join-Path $repoRoot ".env"
    if (-not (Test-Path $envPath)) { return $cfg }
    foreach ($linea in (Get-Content $envPath -Encoding UTF8)) {
        $t = $linea.Trim()
        if ($t -eq "" -or $t.StartsWith("#")) { continue }
        $i = $t.IndexOf("=")
        if ($i -lt 1) { continue }
        $clave = $t.Substring(0, $i).Trim()
        $valor = $t.Substring($i + 1).Trim().Trim('"').Trim("'")
        $cfg[$clave] = $valor
    }
    return $cfg
}

function Get-Cfg {
    param([hashtable]$Cfg, [string]$Clave, [string]$Default = "")
    if ($Cfg.ContainsKey($Clave) -and $Cfg[$Clave] -ne "") { return $Cfg[$Clave] }
    return $Default
}

# ---------------------------------------------------------------------- aviso

function Send-Aviso {
    param([hashtable]$Cfg, [string]$Asunto, [string]$Cuerpo)

    $host_ = Get-Cfg $Cfg "WATCHDOG_SMTP_HOST"
    $user  = Get-Cfg $Cfg "WATCHDOG_SMTP_USER"
    $pass  = Get-Cfg $Cfg "WATCHDOG_SMTP_PASS"
    if ($host_ -eq "" -or $user -eq "" -or $pass -eq "") {
        Write-Log "  (mail no configurado: falta WATCHDOG_SMTP_HOST/USER/PASS en .env)"
        return
    }
    $puerto = [int](Get-Cfg $Cfg "WATCHDOG_SMTP_PORT" "587")
    $para   = Get-Cfg $Cfg "WATCHDOG_MAIL_TO" $user

    try {
        $seguro = ConvertTo-SecureString $pass -AsPlainText -Force
        $cred = New-Object System.Management.Automation.PSCredential($user, $seguro)
        Send-MailMessage -SmtpServer $host_ -Port $puerto -UseSsl -Credential $cred `
            -From $user -To $para -Subject $Asunto -Body $Cuerpo -Encoding UTF8 -ErrorAction Stop
        Write-Log "  mail enviado a $para"
    } catch {
        # Que falle el aviso no puede tumbar al watchdog: lo importante es haber
        # levantado el servicio.
        Write-Log "  NO se pudo mandar el mail: $($_.Exception.Message)"
    }
}

# --------------------------------------------------------------------- estado

function Read-Estado {
    if (-not (Test-Path $statePath)) { return @{ estado = "UP"; ultimo_aviso = [datetime]::MinValue } }
    try {
        $d = Get-Content $statePath -Raw | ConvertFrom-Json
        $ua = [datetime]::MinValue
        if ($d.ultimo_aviso) { $ua = [datetime]::Parse($d.ultimo_aviso) }
        return @{ estado = [string]$d.estado; ultimo_aviso = $ua }
    } catch {
        return @{ estado = "UP"; ultimo_aviso = [datetime]::MinValue }
    }
}

function Write-Estado {
    param([string]$Estado, [datetime]$UltimoAviso)
    $obj = [ordered]@{ estado = $Estado; ultimo_aviso = $UltimoAviso.ToString("o") }
    ($obj | ConvertTo-Json -Compress) | Out-File -FilePath $statePath -Encoding utf8
}

# --------------------------------------------------------------------- chequeo

function Test-Web {
    param([string]$Url, [int]$Timeout)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $Timeout
        return @{ ok = ($r.StatusCode -eq 200); detalle = "HTTP $($r.StatusCode)" }
    } catch {
        return @{ ok = $false; detalle = $_.Exception.Message }
    }
}

function Get-EstadoContenedor {
    param([string]$Servicio)
    try {
        $nombre = "geba_acs-$Servicio-1"
        $r = Invoke-Nativo { docker inspect $nombre --format "{{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}" }
        if ($r.codigo -ne 0) { return "no se pudo inspeccionar ($($r.salida))" }
        return $r.salida
    } catch {
        return "docker no responde: $($_.Exception.Message)"
    }
}

# ----------------------------------------------------------------------- main

$initialLocation = Get-Location
try {
    Set-Location $repoRoot
    $cfg = Read-DotEnv
    if ($Url -ne "") {
        $url = $Url
    } else {
        $url = Get-Cfg $cfg "WATCHDOG_URL" "http://127.0.0.1/"
    }
    $estadoPrevio = Read-Estado
    $ahora = Get-Date

    $r = Test-Web -Url $url -Timeout $TimeoutSec
    if ($r.ok) {
        if ($estadoPrevio.estado -ne "UP") {
            Write-Log "RECUPERADO: $url responde ($($r.detalle))."
            Send-Aviso $cfg "[geba_acs] Visor recuperado" @"
El visor volvió a responder.

URL:   $url
Estado: $($r.detalle)
Equipo: $env:COMPUTERNAME
Cuándo: $($ahora.ToString('yyyy-MM-dd HH:mm:ss'))
"@
        }
        # Todo bien: no se loguea nada para no ensuciar el archivo cada corrida.
        Write-Estado "UP" ([datetime]::MinValue)
        exit 0
    }

    # ------------------------------------------------------------- está caído
    $contenedor = Get-EstadoContenedor -Servicio $Service
    Write-Log "CAIDO: $url no responde ($($r.detalle)). Contenedor: $contenedor"

    $debeAvisar = ($estadoPrevio.estado -eq "UP") -or
                  (($ahora - $estadoPrevio.ultimo_aviso).TotalMinutes -ge $ReavisoMinutos)

    if (Test-Path $pausePath) {
        Write-Log "  hay web_watchdog.pause: NO lo levanto (mantenimiento)."
        if ($debeAvisar) {
            Send-Aviso $cfg "[geba_acs] Visor CAIDO (en pausa, no se levanta)" @"
El visor no responde y el watchdog está en pausa, así que no lo levanté.

URL:        $url
Error:      $($r.detalle)
Contenedor: $contenedor
Equipo:     $env:COMPUTERNAME

Para reanudar el auto-levantado, borrar:
$pausePath
"@
            Write-Estado "DOWN" $ahora
        } else {
            Write-Estado "DOWN" $estadoPrevio.ultimo_aviso
        }
        exit 1
    }

    Write-Log "  levantando con docker compose up -d $Service ..."
    $up = Invoke-Nativo { docker compose up -d $Service }
    $salida = $up.salida
    $codigo = $up.codigo
    if ($codigo -ne 0) {
        Write-Log "  docker compose falló (exit $codigo): $salida"
    }

    Start-Sleep -Seconds $EsperaTrasLevantarSec
    $r2 = Test-Web -Url $url -Timeout $TimeoutSec

    if ($r2.ok) {
        Write-Log "  LEVANTADO OK: $url responde ($($r2.detalle))."
        Send-Aviso $cfg "[geba_acs] Visor caido y levantado automaticamente" @"
El visor no respondía y el watchdog lo levantó.

URL:            $url
Error original: $($r.detalle)
Contenedor:     $contenedor
Ahora:          $($r2.detalle)
Equipo:         $env:COMPUTERNAME
Cuándo:         $($ahora.ToString('yyyy-MM-dd HH:mm:ss'))

Salida de docker compose:
$salida
"@
        Write-Estado "UP" ([datetime]::MinValue)
        exit 0
    }

    Write-Log "  NO se pudo levantar: sigue sin responder ($($r2.detalle))."
    if ($debeAvisar) {
        Send-Aviso $cfg "[geba_acs] Visor CAIDO y no se pudo levantar" @"
El visor no responde y el intento de levantarlo NO funcionó. Requiere revisión
manual en el equipo.

URL:            $url
Error:          $($r2.detalle)
Contenedor:     $contenedor
Equipo:         $env:COMPUTERNAME
Cuándo:         $($ahora.ToString('yyyy-MM-dd HH:mm:ss'))

Salida de docker compose (exit $codigo):
$salida

Log: $LogPathResolved
"@
        Write-Estado "DOWN" $ahora
    } else {
        Write-Estado "DOWN" $estadoPrevio.ultimo_aviso
    }
    exit 1
}
finally {
    Set-Location $initialLocation
}

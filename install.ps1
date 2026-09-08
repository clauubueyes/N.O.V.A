<#
N.O.V.A. one-click installer (Windows PowerShell).
====================================================================
Installs everything for a ready-to-chat JARVIS-style assistant (no prerequisites):
  1. Installs Python 3.12 from python.org (per-user, with PATH) if missing.
  2. Creates a virtualenv (.venv) if missing.
  3. Installs the package (+ dev extras, and voice when requested).
  4. Ensures Ollama is installed (winget, or direct installer when there is none) and running.
  5. Runs `nova-setup auto`: detects machine -> installs suitable models
     -> writes a safe config.yaml.
  6. Optionally enables autostart (nova-agent on login).

Usage:
  .\install.ps1                 # base install
  .\install.ps1 -Voice          # also install the local voice stack
  .\install.ps1 -NoSetup        # install only, skip the auto-provisioning
  .\install.ps1 -NoModels       # provision config but skip model downloads
  .\install.ps1 -Autostart      # enable autostart after installing
#>

param(
    [switch]$Voice,
    [switch]$NoSetup,
    [switch]$NoModels,
    [switch]$Autostart,
    [string]$Config = "config/config.yaml"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Trace-Cmd {
    param([string]$Desc, [scriptblock]$Block)
    Write-Host "  [ejecutando] $Desc" -ForegroundColor DarkGray
    & $Block
    $code = $LASTEXITCODE
    Write-Host "  [salida] codigo=$code" -ForegroundColor DarkGray
    return $code
}

# ---------------------------------------------------------------- Python
# Fully self-contained: if Python is missing we download and install it from
# python.org directly (win-bootstrap). This works even with no winget installed.
function Find-Python {
    $c = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ($c) { return $c }
    $probe = @(
        "$env:LOCALAPPDATA\Programs\Python",
        "$env:ProgramFiles\Python"
    )
    foreach ($base in $probe) {
        if (Test-Path $base) {
            $exe = Get-ChildItem "$base\Python*\python.exe" -ErrorAction SilentlyContinue |
                Sort-Object { [int]($_.Directory.Name -replace 'Python','') } -Descending |
                Select-Object -First 1 -ExpandProperty FullName
            if ($exe) { return $exe }
        }
    }
    return $null
}

Write-Step "Checking Python 3.11+"
$py = Find-Python
if (-not $py) {
    Write-Host "  Python not found. Downloading and installing Python 3.12 automatically..." -ForegroundColor Yellow
    $installer = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
    if (-not (Test-Path $installer)) {
        Write-Host "    Downloading https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe ..."
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            (New-Object System.Net.WebClient).DownloadFile(
                "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe", $installer)
        } catch {
            Write-Host "    Download failed: $($_.Exception.Message). Install manually from https://www.python.org/downloads" -ForegroundColor Red
            exit 1
        }
    }
    Write-Host "    Installing (user scope). This may take a minute..." -ForegroundColor Yellow
    $env:PYTHONUTF8 = "1"
    $proc = Start-Process -FilePath $installer -ArgumentList `
        "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0", "Include_launcher=1" `
        -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        Write-Host "    Python installer exited with code $($proc.ExitCode). Install manually from https://www.python.org/downloads" -ForegroundColor Red
        exit 1
    }
    # The per-user installer registers the PATH for future shells; refresh this session.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $py = Find-Python
}
if (-not $py) {
    Write-Host "  Python installed but not on PATH for this session. Close and reopen PowerShell, then re-run." -ForegroundColor Yellow
    exit 1
}
& $py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Python >= 3.11 required." -ForegroundColor Yellow
    exit 1
}
Write-Host "  Using Python: $py"

# ---------------------------------------------------------------- venv
Write-Step "Creating virtualenv (.venv)"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $py -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit 1 }
}
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"

# ---------------------------------------------------------------- pip
Write-Step "Installing N.O.V.A."
$extra = if ($Voice) { ".[dev,voice]" } else { ".[dev]" }
Write-Host "  [comando] $VenvPy -m pip install -e '$extra'" -ForegroundColor DarkGray
& $VenvPy -m pip install --upgrade pip | Out-Null
Write-Host "  [salida] pip upgrade codigo=$LASTEXITCODE" -ForegroundColor DarkGray
& $VenvPy -m pip install -e $extra
Write-Host "  [salida] pip install codigo=$LASTEXITCODE" -ForegroundColor DarkGray
if ($LASTEXITCODE -ne 0) {
    Write-Host "  pip install failed." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- Ollama
Write-Step "Ensuring Ollama is installed"
Write-Host "  Estado python        : $py" -ForegroundColor DarkGray
Write-Host "  Ollama en PATH       : $((Get-Command ollama -ErrorAction SilentlyContinue) -ne $null)" -ForegroundColor DarkGray
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) {
    $probeOllama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    Write-Host "  Buscando por ruta    : $probeOllama -> $(Test-Path $probeOllama)" -ForegroundColor DarkGray
    # Already installed but not on this session's PATH? Reuse it (don't reinstall).
    $ollama = Get-ChildItem $probeOllama -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}
Write-Host "  Ollama detectado     : $ollama" -ForegroundColor DarkGray
if (-not $ollama) {
    $winget = (Get-Command winget -ErrorAction SilentlyContinue).Source
    Write-Host "  winget disponible    : $($null -ne $winget)" -ForegroundColor DarkGray
    if ($winget) {
        Write-Host "  [comando] winget install --id Ollama.Ollama ..." -ForegroundColor DarkGray
        winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
        Write-Host "  [salida] winget codigo=$LASTEXITCODE" -ForegroundColor DarkGray
        $ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    }
    if (-not $ollama) {
        # No winget (or it didn't register ollama): download the official installer.
        Write-Host "  winget no disponible / no registró ollama. Descargando instalador oficial..." -ForegroundColor Yellow
        $installer = Join-Path $env:TEMP "OllamaSetup.exe"
        if (-not (Test-Path $installer)) {
            Write-Host "  [comando] descarga https://ollama.com/download/OllamaSetup.exe" -ForegroundColor DarkGray
            try {
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                (New-Object System.Net.WebClient).DownloadFile("https://ollama.com/download/OllamaSetup.exe", $installer)
                Write-Host "  [salida] descarga completa ($((Get-Item $installer).Length) bytes)" -ForegroundColor DarkGray
            } catch {
                Write-Host "    Download failed: $($_.Exception.Message). Launch https://ollama.com/download to install Ollama, then re-run." -ForegroundColor Red
                exit 1
            }
        } else {
            Write-Host "  [salida] instalador ya descargado: $installer" -ForegroundColor DarkGray
        }
        Write-Host "  [comando] $installer /VERYSILENT (instalando, acepta el UAC si sale)..." -ForegroundColor DarkGray
        $op = Start-Process -FilePath $installer -ArgumentList "/VERYSILENT" -Wait -PassThru
        Write-Host "  [salida] instalador Ollama exit code=$($op.ExitCode)" -ForegroundColor DarkGray
        # The installer registers ollama for new shells; refresh this session's PATH too.
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
        $ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
        if (-not $ollama) {
            $ollama = Get-ChildItem "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
        }
        if (-not $ollama) {
            Write-Host "  Ollama not found on PATH. Start 'Ollama' once from the Start Menu, then re-run." -ForegroundColor Yellow
            exit 1
        }
    }
    # Make sure Ollama's folder is in the user PATH permanently (the installer
    # sometimes only registers it for new shells) and refresh this session too,
    # so the provisioning step can run `ollama` / `nova-setup` reliably.
    $ollamaDir = Split-Path -Parent $ollama
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    Write-Host "  PATH de usuario contiene Ollama: $($userPath -like "*$ollamaDir*")" -ForegroundColor DarkGray
    if ($userPath -notlike "*$ollamaDir*") {
        [System.Environment]::SetEnvironmentVariable("Path", "$userPath;$ollamaDir", "User")
        Write-Host "  [accion] Added Ollama to the user PATH: $ollamaDir" -ForegroundColor Green
    }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
}
# Ensure the service is running (`ollama serve` detached; best-effort).
Write-Host "  [comando] ollama list (comprobar servidor)" -ForegroundColor DarkGray
try {
    & $ollama list | Out-Null
    Write-Host "  [salida] ollama list codigo=$LASTEXITCODE" -ForegroundColor DarkGray
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [accion] lanzando ollama serve..." -ForegroundColor DarkGray
        Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 3
    }
} catch {
    Write-Host "  [accion] lanzando ollama serve (por excepcion)..." -ForegroundColor DarkGray
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

# ---------------------------------------------------------------- provision
if (-not $NoSetup) {
    Write-Step "Auto-provisioning (detect machine, install models, write config)"
    $noModelsArg = if ($NoModels) { "--no-models" } else { "" }
    $setupExe = Join-Path $Root ".venv\Scripts\nova-setup.exe"
    Write-Host "  [comando] $setupExe auto --config $Config $noModelsArg" -ForegroundColor DarkGray
    & $setupExe auto --config $Config $noModelsArg
    Write-Host "  [salida] nova-setup auto codigo=$LASTEXITCODE" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------- autostart
if ($Autostart) {
    Write-Step "Enabling autostart on login"
    $setupExe = Join-Path $Root ".venv\Scripts\nova-setup.exe"
    Write-Host "  [comando] $setupExe autostart --enable 1" -ForegroundColor DarkGray
    & $setupExe autostart --enable 1
    Write-Host "  [salida] nova-setup autostart codigo=$LASTEXITCODE" -ForegroundColor DarkGray
}

Write-Step "Done."
Write-Host "  Start chatting with:  .\.venv\Scripts\nova"
Write-Host "  API + web UI:         .\.venv\Scripts\nova-api   (http://127.0.0.1:8000/)"
if ($Voice) {
    Write-Host "  Voice mode:           (in chat) /voice"
}
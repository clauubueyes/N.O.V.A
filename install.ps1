<#
N.O.V.A. one-click installer (Windows PowerShell).
====================================================================
Installs everything for a ready-to-chat JARVIS-style assistant (no prerequisites):
  1. Installs Python 3.12 from python.org (per-user, with PATH) if missing.
  2. Creates a virtualenv (.venv) if missing.
  3. Installs the package (+ dev extras, and voice when requested).
  4. Ensures Ollama is installed (winget) and running.
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
& $VenvPy -m pip install --upgrade pip | Out-Null
& $VenvPy -m pip install -e $extra
if ($LASTEXITCODE -ne 0) {
    Write-Host "  pip install failed." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- Ollama
Write-Step "Ensuring Ollama is installed"
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) {
    Write-Host "  Installing Ollama via winget (accept the UAC prompt if shown)..." -ForegroundColor Yellow
    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
    $ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if (-not $ollama) {
        Write-Host "  Ollama install did not add to PATH. Start 'Ollama' once from the Start Menu, then re-run." -ForegroundColor Yellow
        Start-Process "https://ollama.com/download"
        exit 1
    }
}
# Ensure the service is running (the app daemon usually is; `ollama serve` otherwise).
try {
    & ollama list | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 3
    }
} catch {
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

# ---------------------------------------------------------------- provision
if (-not $NoSetup) {
    Write-Step "Auto-provisioning (detect machine, install models, write config)"
    $noModelsArg = if ($NoModels) { "--no-models" } else { "" }
    & (Join-Path $Root ".venv\Scripts\nova-setup.exe") auto --config $Config $noModelsArg
}

# ---------------------------------------------------------------- autostart
if ($Autostart) {
    Write-Step "Enabling autostart on login"
    & (Join-Path $Root ".venv\Scripts\nova-setup.exe") autostart --enable 1
}

Write-Step "Done."
Write-Host "  Start chatting with:  .\.venv\Scripts\nova"
Write-Host "  API + web UI:         .\.venv\Scripts\nova-api   (http://127.0.0.1:8000/)"
if ($Voice) {
    Write-Host "  Voice mode:           (in chat) /voice"
}
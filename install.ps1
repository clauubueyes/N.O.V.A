<#
N.O.V.A. one-click installer (Windows PowerShell).
====================================================================
Installs everything for a ready-to-chat JARVIS-style assistant:
  1. Creates a virtualenv (.venv) if missing.
  2. Installs the package (+ dev extras, and voice when requested).
  3. Ensures Ollama is installed (winget) and running.
  4. Runs `nova-setup auto`: detects machine -> installs suitable models
     -> writes a safe config.yaml.
  5. Optionally enables autostart (nova-agent on login).

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
Write-Step "Checking Python 3.11+"
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) {
    Write-Host "  Python not found. Install from https://www.python.org/downloads (tick 'Add to PATH')." -ForegroundColor Yellow
    exit 1
}
& python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Python >= 3.11 required." -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------- venv
Write-Step "Creating virtualenv (.venv)"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & python -m venv .venv
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
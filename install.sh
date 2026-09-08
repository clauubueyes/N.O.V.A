#!/usr/bin/env bash
# N.O.V.A. one-click installer (Linux/macOS/Unix).
# ====================================================================
# 1. Creates a virtualenv (.venv) if missing.
# 2. Installs the package (+ dev extras, and voice when requested).
# 3. Ensures Ollama is installed and serving.
# 4. Runs `nova-setup auto`: detects machine -> installs suitable models
#    -> writes a safe config.yaml.
# 5. Optionally enables autostart (nova-agent on login).
#
# Usage:
#   ./install.sh                  # base install
#   ./install.sh --voice          # also install the local voice stack
#   ./install.sh --no-setup       # install only, skip auto-provisioning
#   ./install.sh --autostart      # enable autostart after installing
# ====================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VOICE=0
NO_SETUP=0
AUTOSTART=0
if [ "$(uname)" = "Darwin" ]; then
  export NOVA_HOME="${NOVA_HOME:-$HOME/Library/Application Support/NOVA}"
else
  export NOVA_HOME="${NOVA_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/NOVA}"
fi
CONFIG="$NOVA_HOME/config.yaml"
VENV_DIR="$NOVA_HOME/venv"
VENV_CREATED=0

for arg in "$@"; do
  case "$arg" in
    --voice) VOICE=1 ;;
    --no-setup) NO_SETUP=1 ;;
    --autostart) AUTOSTART=1 ;;
    --config=*) CONFIG="${arg#--config=}" ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }

# ------------------------------------------------------------------ Python
step "Checking Python 3.11+"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python not found. Install Python >= 3.11 first." >&2
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "Python >= 3.11 required." >&2; exit 1
}

# ------------------------------------------------------------------ venv
step "Creating virtualenv ($VENV_DIR)"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  python3 -m venv "$VENV_DIR"
  VENV_CREATED=1
fi
VENVPY="$VENV_DIR/bin/python"

# ------------------------------------------------------------------ pip
step "Installing N.O.V.A."
if [ "$VOICE" = "1" ]; then
  "$VENVPY" -m pip install --upgrade pip
  "$VENVPY" -m pip install -e ".[dev,voice]"
else
  "$VENVPY" -m pip install --upgrade pip
  "$VENVPY" -m pip install -e ".[dev]"
fi

if [ "$VENV_CREATED" = "1" ]; then
  "$VENVPY" -m nova.setup.bootstrap --venv "$VENV_DIR"
fi

# ------------------------------------------------------------------ Ollama
step "Ensuring Ollama is installed"
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama not found. Open https://ollama.com/download to install it." >&2
  open_url() { :; }
  if [ "$(uname)" = "Darwin" ]; then open "https://ollama.com/download"; else xdg-open "https://ollama.com/download" 2>/dev/null || true; fi
  echo "Rerun this script after installing Ollama." >&2
  exit 1
fi
if ! curl -sf http://localhost:11434/ >/dev/null 2>&1; then
  echo "Starting Ollama..."
  (nohup ollama serve >/dev/null 2>&1 &)
  sleep 3
fi

# ------------------------------------------------------------------ provision
if [ "$NO_SETUP" = "0" ]; then
  step "Auto-provisioning (detect machine, install models, write config)"
  "$VENV_DIR/bin/nova-setup" auto --config "$CONFIG"
fi

# ------------------------------------------------------------------ autostart
if [ "$AUTOSTART" = "1" ]; then
  step "Enabling autostart on login"
  "$VENV_DIR/bin/nova-setup" autostart --enable 1
fi

step "Done."
echo "  Start chatting with:  $VENV_DIR/bin/nova"
echo "  API + web UI:         $VENV_DIR/bin/nova-api   (http://127.0.0.1:8000/)"
if [ "$VOICE" = "1" ]; then
  echo "  Voice mode:           (in chat) /voice"
fi

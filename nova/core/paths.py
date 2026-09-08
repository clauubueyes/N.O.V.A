from __future__ import annotations

import os
import json
import sys
from pathlib import Path


def installation_home() -> Path:
    if os.environ.get("NOVA_HOME"):
        return Path(os.environ["NOVA_HOME"]).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return (base / "NOVA").resolve()


def default_config_path() -> Path:
    if os.environ.get("NOVA_CONFIG"):
        return Path(os.environ["NOVA_CONFIG"]).expanduser().resolve()
    journal = installation_home() / "installation-state.json"
    try:
        recorded = json.loads(journal.read_text(encoding="utf-8")).get("config_path")
        if recorded and Path(recorded).is_absolute() and Path(recorded).is_file():
            return Path(recorded)
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    installed = installation_home() / "config.yaml"
    return installed if installed.exists() else Path("config/config.yaml")

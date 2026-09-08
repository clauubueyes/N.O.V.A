from __future__ import annotations

"""PHASE 13 — N.O.V.A. Setup & First-Run wizard.

Everything needed to turn a fresh clone into a configured, private, local-first
assistant: machine detection (RAM/GPU), Ollama bootstrap, model selection for the
installed hardware, safe default provisioning (permissions, host, plugins, web),
a guided "JARVIS" first-run assistant (text + optional voice) and optional
autostart on login.

The setup layer never lowers the security model: it writes defaults that still go
through the Permission System and audit. It is opt-in (a dedicated `nova setup`
CLI + first-run detection), so a manual config.yaml is always respected.
"""

from nova.setup.detect import (
    MachineProfile,
    detect_machine,
    detect_ollama,
    detect_python_packages,
)
from nova.setup.models import ModelRec, recommended_models, ram_to_flags
from nova.setup.provision import (
    ProvisionReport,
    autoconfigure,
    ensure_config,
    provision_defaults,
)
from nova.setup.assistant import run_assistant
from nova.setup.autostart import AutostartError, autostart_status, set_autostart
from nova.setup.cli import main

__all__ = [
    "MachineProfile",
    "ModelRec",
    "ProvisionReport",
    "AutostartError",
    "detect_machine",
    "detect_ollama",
    "detect_python_packages",
    "recommended_models",
    "ram_to_flags",
    "autoconfigure",
    "ensure_config",
    "provision_defaults",
    "run_assistant",
    "autostart_status",
    "set_autostart",
    "main",
]

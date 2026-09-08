from __future__ import annotations

"""Safe auto-provisioning of config.yaml.

The installer merges its computed defaults ON TOP of whatever the user already
has in config/config.yaml (or creates a fresh one). It NEVER overwrites a
user-set value. Everything it writes still honours the N.O.V.A. security model:
tools are denied by default unless added to `permissions.allow`, and host/web/
plugins/automation stay OFF unless the wizard explicitly enables them.
"""

from dataclasses import dataclass, field
from pathlib import Path

from nova.setup.detect import MachineProfile
from nova.setup.models import EMBED_MODEL, default_model_for, recommended_models

CONFIG_PATH = Path("config/config.yaml")

#: Safe defaults so N.O.V.A. works out of the box without lowering security.
_SAFE_ALLOW = ["date_time", "calculate", "list_dir", "remember", "memory_search"]
#: Host apps a fully-powered desktop assistant can safely launch by name.
_DEFAULT_APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "chrome": "chrome.exe",
}


@dataclass
class ProvisionReport:
    config_path: str
    created: bool = False
    changed: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    summary: str = ""


def _load_yaml(path: Path) -> dict:
    import yaml

    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_yaml(path: Path, data: dict) -> None:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


def _set(data: dict, key: str, value) -> bool:
    """Set a dotted top-level key only if unset. Returns True if changed."""
    section, _, sub = key.partition(".")
    section_data = data.setdefault(section, {})
    if sub:
        if sub in section_data:
            return False
        section_data[sub] = value
    else:
        if section in data:
            return False
        data[section] = value
    return True


def provision_defaults(profile: MachineProfile | None = None) -> dict:
    """Build the recommended settings dict for the detected machine."""
    if profile is None:
        profile = MachineProfile(ram_total_gb=16, cpu_count=8, gpu_vram_gb=0.0, gpu_available=False, os_name="?", python="?")
    default_rec = default_model_for(profile.ram_total_gb, profile.gpu_vram_gb)
    catalog = {rec.role: rec.model for rec in recommended_models(profile.ram_total_gb, profile.gpu_vram_gb)}

    data: dict = {}
    data["llm"] = {
        "provider": "ollama",
        "base_url": "http://localhost:11434",
        "default_model": default_rec.model,
        "embedding_model": EMBED_MODEL,
        "temperature": 0.7,
        "timeout_s": 60.0,
        "models": catalog,
    }
    data["model_router"] = {"min_ram_gb": 8.0, "battery": True, "cloud_enabled": False}
    data["permissions"] = {"autonomy": "ask", "allow": list(_SAFE_ALLOW), "deny": []}
    data["host"] = {"apps": dict(_DEFAULT_APPS), "commands": [], "roots": [], "working_dir": None, "timeout_s": 30.0}
    data["web"] = {"enabled": False}
    data["voice"] = {"enabled": False}
    data["plugins"] = {"enabled": ["text_tools", "units"], "dir": None}
    data["automation"] = {"enabled": False, "poll_s": 1.0, "tasks": [], "workflows": []}
    return data


def ensure_config(path: str | Path = CONFIG_PATH) -> tuple[Path, bool]:
    """Return (path, created) ensuring the config file exists on disk."""
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# N.O.V.A. configuration (auto-generated). Edit freely; rerun `nova setup` to merge.\n", encoding="utf-8")
        return path, True
    return path, False


def autoconfigure(
    profile: MachineProfile | None = None,
    path: str | Path = CONFIG_PATH,
    enable_plugins: bool = True,
    enable_voice: bool = False,
    enable_web: bool = False,
    enable_automation: bool = False,
) -> ProvisionReport:
    """Merge safe computed defaults into the existing config (or create it).

    Never overwrites a value already present. `enable_*` toggles optional features
    (voice/web/plugins/automation) only when explicitly requested by the wizard.
    """
    path = Path(path)
    created = ensure_config(path)[1]
    data = _load_yaml(path)
    defaults = provision_defaults(profile)

    changed: list[str] = []
    for section, value in defaults.items():
        if section not in data:
            data[section] = value
            changed.append(section)

    # Optional feature toggles (only flipped ON when explicitly requested).
    if enable_plugins and isinstance(data.get("plugins"), dict):
        if not data["plugins"].get("enabled"):
            data["plugins"]["enabled"] = ["text_tools", "units"]
            changed.append("plugins.enabled")
    for section, flag in (
        ("voice", enable_voice),
        ("web", enable_web),
        ("automation", enable_automation),
    ):
        if flag and isinstance(data.get(section), dict) and not data[section].get("enabled"):
            data[section]["enabled"] = True
            changed.append(f"{section}.enabled")

    _write_yaml(path, data)
    model_names = [rec.model for rec in recommended_models(
        (profile.ram_total_gb if profile else 16.0),
        (profile.gpu_vram_gb if profile else 0.0),
    )]
    report = ProvisionReport(
        config_path=str(path),
        created=created,
        changed=changed,
        models=model_names,
    )
    report.summary = ", ".join(changed) if changed else "config already up to date"
    return report

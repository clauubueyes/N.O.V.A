from __future__ import annotations

"""Installation-state manifest for the N.O.V.A. setup lifecycle.

`config/nova-state.json` records everything the installer generated on this
machine so `nova-setup update` and `nova-setup remove` can act on N.O.V.A.'s own
files/models and never on the repository (git-tracked source) or on models the
user pulled themselves. The manifest is a *generated* artifact: written by the
installer and ignored by git.

Everything here is idempotent: loading a missing manifest returns None (a first
time install), and every writer just overwrites the single JSON document.
"""

import json
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from nova import __version__
from nova.setup.catalog import spec_by_name
from nova.setup.selector import CONFIG_ROLE

#: Relative paths (to the install root) the installer is known to create.
DEFAULT_ARTIFACTS: tuple[str, ...] = (
    ".venv",
    "build",
    "dist",
    "logs",
    "memory",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ManagedModel:
    """One Ollama model pulled by N.O.V.A. (owned -> removable by `remove`)."""

    name: str
    role: str = ""
    reason: str = ""
    installed_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ManagedModel":
        return cls(
            name=str(data.get("name", "")),
            role=str(data.get("role", "")),
            reason=str(data.get("reason", "")),
            installed_at=str(data.get("installed_at", "")),
        )


@dataclass
class InstallState:
    """Everything `remove`/`update` need to know about this installation."""

    root: str
    config: str
    installed_at: str = ""
    updated_at: str = ""
    nova_version: str = ""
    venv: str = ""
    host_os: str = ""
    autostart: bool = False
    ollama_installed_by_nova: bool = False
    models: list[ManagedModel] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=lambda: list(DEFAULT_ARTIFACTS))

    @property
    def root_path(self) -> Path:
        return Path(self.root)

    def model_by_role(self, role: str) -> ManagedModel | None:
        for m in self.models:
            if m.role == role:
                return m
        return None

    def managed_model_names(self) -> set[str]:
        return {m.name for m in self.models}

    def has_model(self, name: str) -> bool:
        return name in self.managed_model_names()

    def add_model(self, model: ManagedModel) -> None:
        for i, m in enumerate(self.models):
            if m.name == model.name:
                self.models[i] = model
                return
        self.models.append(model)

    def remove_model(self, name: str) -> bool:
        for i, m in enumerate(self.models):
            if m.name == name:
                self.models.pop(i)
                return True
        return False

    def to_dict(self) -> dict:
        data = asdict(self)
        data["models"] = [m.to_dict() for m in self.models]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "InstallState":
        return cls(
            root=str(data.get("root", "")),
            config=str(data.get("config", "")),
            installed_at=str(data.get("installed_at", "")),
            updated_at=str(data.get("updated_at", "")),
            nova_version=str(data.get("nova_version", "")),
            venv=str(data.get("venv", "")),
            host_os=str(data.get("host_os", "")),
            autostart=bool(data.get("autostart", False)),
            ollama_installed_by_nova=bool(data.get("ollama_installed_by_nova", False)),
            models=[ManagedModel.from_dict(m) for m in data.get("models", []) if isinstance(m, dict)],
            artifacts=[str(a) for a in data.get("artifacts", [])],
        )


def state_path(config_path: str | Path | None = None) -> Path:
    """Return the manifest path: next to the config file in the config dir."""
    if config_path is None:
        return Path("config/nova-state.json")
    cfg = Path(config_path)
    return cfg.parent / "nova-state.json"


def install_root_from_config(config_path: str | Path | None = None) -> Path:
    """Where the installer lives: the git root (or the config's parent dir)."""
    if config_path is None:
        cfg = Path("config/config.yaml").resolve()
    else:
        cfg = Path(config_path).resolve()
    parent = cfg.parent
    if (parent / ".git").exists():
        return parent
    if (parent.parent / ".git").exists():
        return parent.parent
    return parent.parent


def load_state(config_path: str | Path | None = None) -> InstallState | None:
    """Return the installed state, or None when there is no manifest yet."""
    path = state_path(config_path)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None
        state = InstallState.from_dict(data)
        return state if state.root else None
    except (OSError, ValueError):
        return None


def save_state(state: InstallState) -> Path:
    path = state_path(state.config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state.to_dict(), fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)
    return path


def ensure_state(config_path: str | Path | None = None) -> InstallState:
    """Load the manifest or return a fresh one for `config_path`."""
    existing = load_state(config_path)
    if existing is not None:
        return existing
    cfg = Path(config_path) if config_path is not None else Path("config/config.yaml")
    state = InstallState(
        root=str(install_root_from_config(config_path)),
        config=str(cfg),
        installed_at=_now(),
    )
    return state


def clear_state(config_path: str | Path | None = None) -> None:
    path = state_path(config_path)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Git protection: `remove` must never delete repository content.
# ---------------------------------------------------------------------------


def _git(args: list[str], root: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return proc.returncode == 0, proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def is_git_tracked(path: str | Path, root: str | Path | None = None) -> bool:
    """True when `path` (absolute or relative to `root`) is tracked by git."""
    p = Path(path)
    if root is None:
        root = p  # compare against the path's own tree when unknown
    base = Path(root)
    try:
        rel = p.resolve().relative_to(base.resolve())
    except ValueError:
        vcs_root, _ = _git(["rev-parse", "--show-toplevel"], base)
        if not vcs_root:
            return False
        try:
            rel = p.resolve().relative_to(Path(vcs_root.rstrip()))
        except ValueError:
            return False
        base = Path(vcs_root.rstrip())
    ok, _ = _git(["ls-files", "--error-unmatch", "--", str(rel)], base)
    return ok


def safe_to_delete(path: str | Path, root: str | Path) -> bool:
    """Refuse to delete anything git tracks or lives inside `.git`.

    `remove` only ever passes paths it believes it created; this is the last
    safety net in front of `shutil.rmtree`.
    """
    p = Path(path)
    base = Path(root).resolve()
    try:
        resolved = p.resolve()
        resolved.relative_to(base)
    except ValueError:
        return False  # outside the install root: not ours
    if resolved.name == ".git" or ".git" in resolved.parts:
        return False
    if is_git_tracked(resolved, base):
        return False
    return True


def git_tracked_files(root: str | Path) -> list[str]:
    """Absolute paths of every repository file (for the repo-untouched check)."""
    ok, out = _git(["ls-files"], Path(root))
    if not ok or not out:
        return []
    return [str((Path(root) / line).resolve()) for line in out.splitlines() if line]


def managed_models_from_pulled(pulled_names: list[str]) -> list[ManagedModel]:
    """Turn `_pull_models` output into manifest records (role + reason)."""
    models: list[ManagedModel] = []
    for name in pulled_names:
        spec = spec_by_name(name)
        if spec is None:
            models.append(ManagedModel(name=name, role="", reason="installed by N.O.V.A."))
            continue
        role = CONFIG_ROLE.get(spec.role, spec.role)
        models.append(
            ManagedModel(name=name, role=role, reason=spec.description)
        )
    return models


def record_install(
    config_path: str | Path,
    pulled: list[str] | None = None,
    autostart: bool | None = None,
    ollama_owned: bool = False,
) -> InstallState:
    """Merge a finished install/update into the manifest (idempotent)."""
    cfg = Path(config_path)
    state = ensure_state(cfg)
    if pulled:
        for model in managed_models_from_pulled(pulled):
            state.add_model(model)
    if autostart is not None:
        state.autostart = bool(autostart)
    if ollama_owned:
        state.ollama_installed_by_nova = True
    if not state.host_os:
        state.host_os = f"{platform.system()} {platform.release()}".strip()
    if not state.venv:
        state.venv = str(Path(sys.prefix))
    state.nova_version = __version__
    state.updated_at = _now()
    save_state(state)
    return state
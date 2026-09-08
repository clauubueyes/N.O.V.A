from __future__ import annotations

"""Explicit ownership journal. Unknown resources are never adopted implicitly."""

import hashlib
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nova.core.paths import installation_home
from nova.setup.models import normalize_model_name

_LOCK = threading.RLock()
_ACTIVE = threading.local()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ManagedModel(BaseModel):
    name: str
    installed_at: str = Field(default_factory=now)
    variant: str = ""
    category: str = ""
    installed_by_nova: bool = False
    endpoint: str = "http://localhost:11434"
    digest: str = ""


class Resource(BaseModel):
    path: str
    kind: Literal["config", "venv", "data", "cache", "binary", "integration"]


class OllamaOwnership(BaseModel):
    installed_by_nova: bool = False
    version: str = ""
    executable: str = ""
    method: str = ""


class InstallationState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    created_at: str = Field(default_factory=now)
    repository_roots: list[str] = Field(default_factory=list)
    config_path: str = ""
    models: list[ManagedModel] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)
    ollama: OllamaOwnership = Field(default_factory=OllamaOwnership)
    autostart: dict[str, str] = Field(default_factory=dict)
    hardware: dict = Field(default_factory=dict)
    stack: dict[str, str] = Field(default_factory=dict)


def repository_roots(*starts: Path) -> list[Path]:
    roots: set[Path] = set()
    for start in (*starts, Path.cwd(), Path(__file__)):
        resolved = start.resolve()
        for parent in (resolved, *resolved.parents):
            if parent == Path.home().resolve() and resolved != parent:
                break
            if (parent / ".git").exists() or (
                (parent / "pyproject.toml").exists() and (parent / "nova").is_dir()
            ):
                roots.add(parent)
                break
    return sorted(roots)


def check_removal_path(path: Path, roots: list[Path]) -> Path:
    """Reject repository descendants, ancestors, nested repos and reparse points."""
    absolute = Path(os.path.abspath(path))
    resolved = path.resolve()
    for root in (*roots, *repository_roots(path)):
        root = root.resolve()
        if resolved == root or resolved.is_relative_to(root) or root.is_relative_to(resolved):
            raise ValueError(f"Repository protected: {path} (root: {root})")
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise ValueError(f"Unsafe removal target: {path}")
    for component in (absolute, *absolute.parents):
        if component.is_symlink() or (
            component.exists() and getattr(component.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError(f"Link/junction protected: {component}")
    if resolved.is_dir():
        for base, directories, files in os.walk(resolved, followlinks=False):
            if ".git" in directories or ".git" in files:
                raise ValueError(f"Nested repository protected: {base}")
            for name in directories + files:
                child = Path(base) / name
                if child.is_symlink() or getattr(child.lstat(), "st_file_attributes", 0) & 0x400:
                    raise ValueError(f"Link/junction protected: {child}")
    return resolved


class StateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or installation_home() / "installation-state.json"

    def load(self) -> InstallationState | None:
        if not self.path.exists():
            return None
        try:
            return InstallationState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ValueError(f"Cannot read installation state; refusing ownership guesses: {exc}") from exc

    @contextmanager
    def lock(self):
        """Serialize setup mutations across threads and processes; fail visibly if busy."""
        with _LOCK:
            key = str(self.path.resolve()).casefold()
            active = getattr(_ACTIVE, "paths", set())
            if key in active:
                yield
                return
            release = lambda: None
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes

                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
                kernel.CreateMutexW.restype = wintypes.HANDLE
                kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = kernel.CreateMutexW(None, False, "Local\\NOVA-" + hashlib.sha256(key.encode()).hexdigest())
                if not handle:
                    raise OSError("Cannot lock installation state")
                result = kernel.WaitForSingleObject(handle, 0)
                if result not in (0, 0x80):
                    kernel.CloseHandle(handle)
                    raise OSError("Another N.O.V.A. maintenance operation is running")
                def release():
                    kernel.ReleaseMutex(handle)
                    kernel.CloseHandle(handle)
            else:
                import fcntl

                self.path.parent.mkdir(parents=True, exist_ok=True)
                handle = self.path.with_suffix(".lock").open("a")
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    handle.close()
                    raise OSError("Another N.O.V.A. maintenance operation is running") from None
                release = handle.close
            _ACTIVE.paths = active | {key}
            try:
                yield
            finally:
                _ACTIVE.paths = active
                release()

    def save(self, state: InstallationState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def change(self, callback) -> InstallationState:
        with self.lock():
            state = self.load() or InstallationState()
            state.repository_roots = sorted(set(state.repository_roots) | {
                str(root) for root in repository_roots()
            })
            callback(state)
            self.save(state)
            return state

    def record_resource(self, path: Path, kind: str) -> None:
        def record(state):
            resolved = str(path.resolve())
            if not any(r.path == resolved for r in state.resources):
                state.resources.append(Resource(path=resolved, kind=kind))
        self.change(record)

    def record_model(self, name: str, category: str, endpoint: str, *, owned: bool,
                     digest: str = "") -> None:
        def record(state):
            canonical = normalize_model_name(name)
            previous = next((m for m in state.models if normalize_model_name(m.name) == canonical
                             and m.endpoint == endpoint), None)
            if previous:
                if digest and previous.installed_by_nova:
                    previous.digest = digest
                return
            state.models.append(ManagedModel(name=name, category=category, endpoint=endpoint,
                                             variant=name.partition(":")[2] or "latest",
                                             installed_by_nova=owned, digest=digest))
        self.change(record)


def exclusive(operation):
    @wraps(operation)
    def run(*args, **kwargs):
        store = kwargs.get("store") or StateStore()
        with store.lock():
            return operation(*args, **kwargs)
    return run

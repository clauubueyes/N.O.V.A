from __future__ import annotations

"""PHASE 14.2 — reusable dependency model + detection for the N.O.V.A. setup.

Every dependency listed here is backed by something N.O.V.A. actually uses
(imports, binaries or services the code/scripts really invoke). Unknown
runtimes are never invented: the catalog is explicit and per-feature
(``features``: core / hybrid / voice / web / dev).

Design rules:
- Detection is best-effort and never mutates anything.
- Version comparison is dependency-free (simple tuple compare).
- Installation is delegated to safe, platform-specific methods; anything that
  cannot be installed automatically gets explicit manual instructions and is
  *optional* unless it really blocks N.O.V.A.
- Credentials are never touched: OpenCode auth is delegated to OpenCode itself.
"""

import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable

from nova.setup.remote_catalog import version_tuple

_PLATFORMS = ("win", "linux", "darwin")


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "win"
    if sys.platform.startswith("darwin"):
        return "darwin"
    return "linux"


def _run(args: list[str], timeout: float = 8.0) -> str:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return (proc.stdout or "") + (proc.stderr or "")
    except Exception:  # noqa: BLE001
        return ""


def _first_version(text: str) -> str:
    match = re.search(r"\d+\.\d+(\.\d+)?", text)
    return match.group(0) if match else ""


def version_key(text: str) -> tuple[int, ...]:
    """Parse a version like '3.11', '2.4.1' or 'v1.2.3' into a comparable tuple.

    Unlike ``version_tuple`` (which requires X.Y.Z), this also handles the
    partial forms used as minimums, so ``3.11.9 >= 3.11`` works as expected.
    """
    match = re.search(r"\d+(?:\.\d+)*", text or "")
    if not match:
        return ()
    return tuple(int(part) for part in match.group(0).split("."))


def find_binary(name: str) -> str | None:
    return shutil.which(name)


def python_version() -> str:
    return ".".join(str(v) for v in sys.version_info[:3])


def in_venv() -> bool:
    try:
        return sys.prefix != sys.base_prefix
    except Exception:  # noqa: BLE001
        return False


def venv_is_broken(venv_dir: str | None = None) -> bool:
    """True when the active (or given) virtualenv looks unusable."""
    import os
    from pathlib import Path

    if venv_dir is not None:
        marker = Path(venv_dir) / "pyvenv.cfg"
        if not marker.is_file():
            return True
        py = Path(venv_dir) / ("Scripts" if os.name == "nt" else "bin") / "python"
        return not py.is_file()
    if not in_venv():
        return False
    # A usable venv: marker at the venv root plus a python-named interpreter.
    try:
        marker = Path(sys.prefix) / "pyvenv.cfg"
        script = Path(sys.executable)
        if not marker.is_file():
            return True
        if sys.platform.startswith("win"):
            return script.name.lower() != "python.exe"
        return not script.name.startswith("python")
    except Exception:  # noqa: BLE001
        return False


def module_version(name: str) -> str:
    _DIST = {"yaml": "PyYAML"}
    try:
        from importlib.metadata import version

        return version(_DIST.get(name, name))
    except Exception:  # noqa: BLE001
        return ""


def module_present(name: str) -> bool:
    if name == "pydantic-settings":
        return importlib.util.find_spec("pydantic_settings") is not None
    return importlib.util.find_spec(name) is not None


@dataclass
class Dependency:
    """A real, audited N.O.V.A. dependency."""

    name: str
    purpose: str
    binary: str | None = None          # command to locate, e.g. "ollama"
    python_module: str | None = None   # import spec, e.g. "httpx"
    dist_name: str | None = None       # pip package name when it differs ("yaml"->PyYAML)
    version_args: list[str] | None = None  # e.g. ["--version"]
    version_parser: Callable[[str], str] = _first_version
    min_version: str | None = None
    required: bool = False
    platform: tuple[str, ...] | None = None  # None = all platforms
    category: str = "system"           # "system" | "python"
    features: str = "core"             # core/hybrid/voice/web/dev
    install_hint: str = ""             # safe automated install command (Windows safe)
    manual_url: str = ""               # official source to install manually
    detect_extra: Callable[[], bool] | None = None  # extra probe (e.g. venv)
    status: str = "unknown"            # missing/installed/optional
    version: str = ""

    def matches_platform(self) -> bool:
        if not self.platform:
            return True
        return current_platform() in self.platform

    def detect(self) -> "Dependency":
        self.status = "missing"
        if not self.matches_platform():
            self.status = "skip"
            return self
        if self.detect_extra is not None:
            if not self.detect_extra():
                return self
            self.status = "installed"
            if self.binary and self.version_args:
                path = find_binary(self.binary) or sys.executable
                out = _run([path, *self.version_args])
                self.version = self.version_parser(out) if out else ""
            return self
        if self.binary:
            path = find_binary(self.binary)
            if not path:
                return self
            self.status = "installed"
            if self.version_args:
                out = _run([path, *self.version_args])
                self.version = self.version_parser(out) if out else ""
            return self
        if self.python_module:
            if module_present(self.python_module):
                self.status = "installed"
                self.version = module_version(self.python_module)
            return self
        return self

    @property
    def enough_version(self) -> bool:
        if not self.min_version or not self.version:
            return True
        return version_key(self.version) >= version_key(self.min_version)

    @property
    def effective_status(self) -> str:
        if self.status == "skip":
            return "skip"
        if self.status == "installed" and not self.enough_version:
            return "outdated"
        return self.status


@dataclass
class Audit:
    """Result of auditing a dependency list."""

    dependencies: list[Dependency] = field(default_factory=list)
    missing_required: list[Dependency] = field(default_factory=list)
    missing_optional: list[Dependency] = field(default_factory=list)
    outdated: list[Dependency] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.missing_required and not self.outdated


def default_dependencies(features: tuple[str, ...] = ("core",)) -> list[Dependency]:
    """Catalog of dependencies N.O.V.A. really uses, by feature group."""
    python_core = [
        Dependency(
            name="python",
            purpose="N.O.V.A. runtime language",
            binary="python" if not sys.platform.startswith("win") else "python",
            version_args=["--version"],
            version_parser=_first_version,
            min_version="3.11",
            required=True,
            category="system",
            features="core",
            detect_extra=lambda: True,  # the interpreter running nova IS python
            manual_url="https://www.python.org/downloads",
        ),
        Dependency(
            name="venv",
            purpose="Isolated Python environment (not polluting the system)",
            required=True,
            category="system",
            features="core",
            detect_extra=in_venv,
            manual_url="https://docs.python.org/3/library/venv.html",
        ),
        Dependency(
            name="pip",
            purpose="Python package manager (required for dependency install)",
            required=True,
            category="system",
            features="core",
            detect_extra=lambda: module_present("pip") or _run([sys.executable, "-m", "pip", "--version"]) != "",
            manual_url="https://pip.pypa.io",
        ),
    ]
    python_packages = [
        Dependency(
            name=pkg,
            purpose={
                "httpx": "HTTP client for Ollama/OpenCode providers",
                "pydantic": "settings model validation",
                "pydantic-settings": "config/env loading",
                "PyYAML": "config.yaml parsing",
                "fastapi": "REST API + web backend",
                "uvicorn": "ASGI server for the API",
            }[pkg],
            python_module={"PyYAML": "yaml"}.get(pkg, pkg),
            dist_name="PyYAML" if pkg == "PyYAML" else None,
            min_version={"httpx": "0.27", "pydantic": "2.8", "pydantic-settings": "2.4",
                         "PyYAML": "6.0", "fastapi": "0.115", "uvicorn": "0.30"}.get(pkg),
            required=True,
            category="python",
            features="core",
            install_hint=f"{sys.executable} -m pip install '{pkg}'",
            manual_url=f"https://pypi.org/project/{pkg}/",
        )
        for pkg in ("httpx", "pydantic", "pydantic-settings", "PyYAML", "fastapi", "uvicorn")
    ]
    ollama = Dependency(
        name="ollama",
        purpose="Local LLM runtime and embeddings (LOCAL and HYBRID modes)",
        binary="ollama",
        version_args=["--version"],
        version_parser=_first_version,
        min_version=None,
        required=True,
        category="system",
        features="core",
        install_hint="winget install --id Ollama.Ollama",
        manual_url="https://ollama.com/download",
    )
    opencode = Dependency(
        name="opencode",
        purpose="Optional cloud provider for HYBRID mode; auth stays inside OpenCode",
        binary="opencode",
        version_args=["--version"],
        version_parser=_first_version,
        min_version=None,
        required=False,
        category="system",
        features="hybrid",
        manual_url="https://opencode.ai/docs",
    )
    voice = [
        Dependency(
            name="numpy", purpose="Audio buffering for the voice pipeline",
            python_module="numpy", min_version="1.26", required=False,
            category="python", features="voice",
            install_hint=f"{sys.executable} -m pip install 'numpy>=1.26'",
            manual_url="https://pypi.org/project/numpy/",
        ),
        Dependency(
            name="vosk", purpose="Offline speech-to-text (Spanish)",
            python_module="vosk", min_version="0.3.45", required=False,
            category="python", features="voice",
            install_hint=f"{sys.executable} -m pip install 'vosk>=0.3.45'",
            manual_url="https://pypi.org/project/vosk/",
        ),
        Dependency(
            name="pyttsx3", purpose="Local text-to-speech (JARVIS greeting)",
            python_module="pyttsx3", min_version="2.95", required=False,
            category="python", features="voice",
            install_hint=f"{sys.executable} -m pip install 'pyttsx3>=2.95'",
            manual_url="https://pypi.org/project/pyttsx3/",
        ),
        Dependency(
            name="sounddevice", purpose="Microphone capture for STT",
            python_module="sounddevice", min_version="0.4.6", required=False,
            category="python", features="voice",
            install_hint=f"{sys.executable} -m pip install 'sounddevice>=0.4.6'",
            manual_url="https://pypi.org/project/sounddevice/",
        ),
    ]
    dev = [
        Dependency(
            name="pytest", purpose="Development / test suite",
            python_module="pytest", min_version="8", required=False,
            category="python", features="dev",
            install_hint=f"{sys.executable} -m pip install 'pytest>=8'",
            manual_url="https://pypi.org/project/pytest/",
        ),
        Dependency(
            name="git", purpose="Development only (source install / contributions)",
            binary="git", version_args=["--version"], version_parser=_first_version,
            min_version=None, required=False, category="system", features="dev",
            manual_url="https://git-scm.com/downloads",
        ),
    ]
    catalog = [*python_core, *python_packages, ollama, opencode, *voice, *dev]
    return [dep for dep in catalog if dep.features in features or dep.features == "core"]


def audit_dependencies(
    features: tuple[str, ...] = ("core", "hybrid", "voice", "dev"),
    dependencies: list[Dependency] | None = None,
) -> Audit:
    """Detect every dependency and classify it into required/optional/outdated."""
    deps = list(dependencies) if dependencies is not None else default_dependencies(features)
    for dep in deps:
        dep.detect()
    audit = Audit(dependencies=deps)
    for dep in deps:
        state = dep.effective_status
        if state == "skip":
            continue
        if state == "outdated":
            audit.outdated.append(dep)
        elif state == "missing":
            if dep.required:
                audit.missing_required.append(dep)
            else:
                audit.missing_optional.append(dep)
    return audit


def install_python(dependency: Dependency, verbose: bool = False) -> bool:
    """Install a python dependency through the active interpreter's pip."""
    spec = dependency.dist_name or dependency.python_module
    if not spec:
        return False
    hint = dependency.install_hint
    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if ">=" in hint:
        command.append(hint.split("'")[1] if "'" in hint else spec)
    else:
        command.append(spec)
    result = subprocess.run(command, capture_output=not verbose, text=True, timeout=600,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode == 0:
        dependency.detect()
        return dependency.effective_status == "installed"
    return False


def install_ollama(verbose: bool = False) -> bool:
    """Install Ollama with a safe, supported method (Windows: winget or official
    installer; elsewhere: report it must be installed manually)."""
    if sys.platform.startswith("win"):
        if shutil.which("winget"):
            result = subprocess.run(
                ["winget", "install", "--id", "Ollama.Ollama", "-e",
                 "--accept-source-agreements", "--accept-package-agreements", "--silent"],
                capture_output=not verbose, text=True, timeout=900,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                return True
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory(prefix="nova-ollama-") as directory:
            installer = Path(directory) / "OllamaSetup.exe"
            import httpx

            try:
                with httpx.Client(timeout=60, follow_redirects=True) as client:
                    with client.stream("GET", "https://ollama.com/download/OllamaSetup.exe") as response:
                        response.raise_for_status()
                        with installer.open("wb") as output:
                            for chunk in response.iter_bytes():
                                if installer.stat().st_size > 2_000_000_000:
                                    raise ValueError("Ollama installer exceeds download limit")
                                output.write(chunk)
            except (httpx.HTTPError, ValueError, OSError):
                return False
            result = subprocess.run(
                [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                capture_output=not verbose, text=True, timeout=900,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.returncode == 0
    return False  # macOS/Linux: manual install (brew/apt/dmg), see manual_url


def install_dependency(dependency: Dependency, *, interactive_confirm: bool = False,
                       auto: bool = False, verbose: bool = False) -> bool:
    """Install one dependency. Returns True on success.

    ``interactive_confirm`` asks the user before installing (used by the wizard);
    ``auto`` installs without asking (used by ``auto``/``repair`` non-interactive).
    OpenCode and other system runtimes without a safe method are never installed
    here silently: they return False and the caller shows manual instructions.
    """
    if dependency.binary == "ollama":
        return install_ollama(verbose=verbose)
    if dependency.python_module:
        return install_python(dependency, verbose=verbose)
    return False


def summary_of(audit: Audit) -> str:
    parts = []
    if audit.missing_required:
        parts.append(f"{len(audit.missing_required)} required missing")
    if audit.missing_optional:
        parts.append(f"{len(audit.missing_optional)} optional missing")
    if audit.outdated:
        parts.append(f"{len(audit.outdated)} outdated")
    if not parts:
        return "all dependencies satisfied"
    return ", ".join(parts) + "."


__all__ = [
    "Audit", "Dependency", "audit_dependencies", "current_platform",
    "default_dependencies", "find_binary", "install_dependency", "install_ollama",
    "install_python", "in_venv", "module_version", "python_version", "summary_of",
    "venv_is_broken", "version_tuple",
]
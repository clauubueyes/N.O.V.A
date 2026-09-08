from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from nova.setup.remote_catalog import get_bytes, version_tuple


@dataclass
class OllamaSnapshot:
    running: bool = False
    version: str = ""
    models: dict[str, str] = field(default_factory=dict)
    error: str = ""


def local_endpoint(endpoint: str) -> bool:
    url = urlparse(endpoint)
    return url.scheme in ("http", "https") and url.hostname in ("localhost", "127.0.0.1", "::1")


def snapshot(endpoint: str) -> OllamaSnapshot:
    from nova.setup.models import normalize_model_name

    try:
        with httpx.Client(base_url=endpoint, timeout=4) as client:
            tags = client.get("/api/tags")
            tags.raise_for_status()
            version = client.get("/api/version")
            version.raise_for_status()
            return OllamaSnapshot(True, version.json()["version"], {
                normalize_model_name(m["name"]): m.get("digest", "") for m in tags.json()["models"]
            })
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return OllamaSnapshot(error=str(exc))


def latest_version(client: httpx.Client | None = None) -> str:
    import json

    own = client is None
    client = client or httpx.Client(timeout=5, follow_redirects=True)
    try:
        release = json.loads(get_bytes(client, "https://api.github.com/repos/ollama/ollama/releases/latest"))
        if not isinstance(release, dict) or release.get("prerelease") or release.get("draft"):
            return ""
        arch = platform.machine().lower()
        if sys.platform == "win32":
            if sys.getwindowsversion().build < 19045 or arch not in ("amd64", "x86_64", "arm64", "aarch64"):
                return ""
            asset = "OllamaSetup.exe"
        elif sys.platform == "darwin":
            if tuple(map(int, platform.mac_ver()[0].split(".")[:1])) < (14,):
                return ""
            asset = "Ollama-darwin.zip"
        elif arch in ("amd64", "x86_64", "arm64", "aarch64"):
            asset = "ollama-linux-" + ("amd64" if arch in ("amd64", "x86_64") else "arm64")
        else:
            return ""
        if not any(a["name"].startswith(asset) for a in release.get("assets", [])):
            return ""
        version = release.get("tag_name", "").removeprefix("v")
        return version if version_tuple(version) else ""
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return ""
    finally:
        if own:
            client.close()


def run_system(args: list[str]) -> None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=600,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise OSError("System operation timed out; inspect its result before retrying") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise OSError(f"Operation failed ({result.returncode}). Administrator privileges required if access was denied. {detail}")


def update_ollama(version: str) -> None:
    if not version_tuple(version):
        raise ValueError("Invalid Ollama release version")
    if sys.platform == "win32" and shutil.which("winget"):
        run_system(["winget", "upgrade", "--id", "Ollama.Ollama", "--exact", "--version", version,
                    "--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity"])
    elif sys.platform == "win32":
        import tempfile

        with tempfile.TemporaryDirectory(prefix="nova-ollama-update-") as directory:
            installer = Path(directory) / "OllamaSetup.exe"
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                with client.stream("GET", f"https://github.com/ollama/ollama/releases/download/v{version}/OllamaSetup.exe") as response:
                    response.raise_for_status()
                    total = 0
                    with installer.open("wb") as output:
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > 2_000_000_000:
                                raise ValueError("Ollama installer exceeds download limit")
                            output.write(chunk)
            run_system([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
    else:
        raise OSError("Install the official Ollama update from https://ollama.com/download, then rerun update.")


def stop_owned_processes(executable: Path, *, environment: bool = False) -> None:
    """Stop processes by verified executable path; never kill by image name alone."""
    if sys.platform != "win32":
        raise OSError("Automatic Ollama uninstall is supported on Windows only")
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    pids = (wintypes.DWORD * 65536)()
    needed = wintypes.DWORD()
    if not ctypes.WinDLL("psapi").EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
        raise OSError("Cannot enumerate Ollama processes")
    for pid in pids[:needed.value // ctypes.sizeof(wintypes.DWORD)]:
        if pid == os.getpid():
            continue
        handle = kernel.OpenProcess(0x1000 | 0x0001 | 0x100000, False, pid)
        if not handle:
            continue
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                path = Path(buffer.value).resolve()
                directory = executable.resolve() if environment else executable.resolve().parent
                matches = path.is_relative_to(directory) and (environment or path.name.lower().startswith("ollama"))
                if matches:
                    result = subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if kernel.WaitForSingleObject(handle, 3000) == 258 and not kernel.TerminateProcess(handle, 0):
                        raise PermissionError("Administrator privileges required to stop Ollama")
        finally:
            kernel.CloseHandle(handle)


def uninstall_ollama(ownership, roots: list[Path]) -> None:
    from nova.setup.state import check_removal_path

    if not ownership.installed_by_nova:
        raise ValueError("Ollama is not owned by N.O.V.A.")
    executable = check_removal_path(Path(ownership.executable), roots)
    if not executable.exists():
        return
    if sys.platform != "win32":
        raise OSError("Remove Ollama with the system package manager; automatic uninstall is Windows-only")
    uninstallers = sorted(executable.parent.glob("unins*.exe"))
    if ownership.method not in ("winget", "official") or not uninstallers:
        raise OSError("Registered Ollama uninstaller not found; keeping Ollama")
    uninstaller = check_removal_path(uninstallers[0], roots)
    check_removal_path(executable.parent, roots)
    stop_owned_processes(executable)
    run_system([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])


def model_directory() -> Path:
    return Path(os.environ.get("OLLAMA_MODELS", Path.home() / ".ollama" / "models")).expanduser()

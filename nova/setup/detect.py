from __future__ import annotations

"""Real machine detection (best-effort, dependency-free core).

These readers are used by the installer to pick the right Ollama models for the
installed hardware. They intentionally go further than the conservative runtime
`ResourceManager` defaults (which stay untouched): on Windows we read physical
RAM and GPU VRAM properly with the standard library + subprocess so a mismatch
between, say, a 4 GB and a 32 GB machine never results in an unusable model.
"""

import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class OllamaStatus:
    installed: bool
    running: bool = False
    base_url: str = "http://localhost:11434"
    models: list[str] = field(default_factory=list)
    message: str = ""
    started_now: bool = False


@dataclass
class MachineProfile:
    ram_total_gb: float
    cpu_count: int
    gpu_vram_gb: float
    gpu_available: bool
    os_name: str
    python: str
    has_embedding_model: bool = False
    cpu_model: str = "unknown"
    cpu_cores: int = 0
    cpu_threads: int = 0
    ram_available_gb: float = 0.0
    gpu_vendor: str = ""
    gpu_model: str = ""
    gpu_accel: str = ""
    arch: str = ""
    disk_free_gb: float = 0.0

    def summary(self) -> list[str]:
        lines = [
            f"OS:        {self.os_name}",
            f"CPU:       {self.cpu_model or 'unknown'} ({self.cpu_count} core(s))",
            f"RAM:       {self.ram_total_gb:.1f} GB total"
            + (f" / ~{self.ram_available_gb:.1f} GB available" if self.ram_available_gb > 0 else ""),
            f"Arch:      {self.arch or 'unknown'}",
            f"Disk free: {self.disk_free_gb:.0f} GB" if self.disk_free_gb > 0 else "Disk free: unknown",
        ]
        if self.gpu_available:
            gpu = self.gpu_model or "GPU"
            accel = f", {self.gpu_accel}" if self.gpu_accel else ""
            lines.append(f"GPU:       {gpu} ({self.gpu_vram_gb:.0f} GB VRAM{accel})")
        else:
            lines.append("GPU:       none detected (CPU-only)")
        lines.append(f"Python:    {self.python}")
        return lines


@dataclass
class OpenCodeStatus:
    """PHASE 14 — OpenCode detection result."""

    installed: bool = False
    running: bool = False
    version: str = ""
    configured: bool = False
    base_url: str = "http://127.0.0.1:4096"
    providers: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    message: str = ""


def _ram_total_windows() -> float:
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return stat.ullTotalPhys / (1024**3)
    except Exception:
        pass
    return 0.0


def _ram_total_generic() -> float:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return pages * page_size / (1024**3)
    except (ValueError, AttributeError, OSError):
        return 0.0


def detect_ram() -> float:
    if sys.platform.startswith("win"):
        total = _ram_total_windows()
        if total > 0:
            return total
    return _ram_total_generic()


def _run_command(args: list[str], timeout: float = 8.0) -> str | None:
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
    except Exception:
        return None


def detect_gpu_vram() -> tuple[bool, float]:
    """Return (available, vram_gb). Tries nvidia-smi, then wmic, then no GPU."""
    # Prefer `nvidia-smi` when on PATH.
    smi = shutil.which("nvidia-smi")
    if smi:
        out = _run_command([smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"])
        if out:
            try:
                line = out.strip().splitlines()[0].strip()
                mb = float(line.split(" ")[0].replace(",", ""))
                if mb > 0:
                    return True, mb / 1024.0
            except (ValueError, IndexError):
                pass
    # Fallback on Windows to WMI (also catches AMD via a generic check, best-effort).
    if sys.platform.startswith("win"):
        out = _run_command(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | "
             "Select-Object -ExpandProperty AdapterRAM | Measure-Object -Sum | "
             "Select-Object -ExpandProperty Sum"]
        )
        if out:
            try:
                total_bytes = float(out.strip().splitlines()[-1].strip())
                gb = total_bytes / (1024**3)
                if gb >= 1.0:
                    return True, gb
            except (ValueError, IndexError):
                pass
    return False, 0.0


def _ram_available_windows() -> float:
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return stat.ullAvailPhys / (1024**3)
    except Exception:
        pass
    return 0.0


def _ram_available_generic() -> float:
    try:
        # Linux: MemAvailable is anonymous memory an app can allocate.
        with open("/proc/meminfo", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    kb = float(line.split()[1])
                    return kb / (1024**2)
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def detect_ram_available() -> float:
    if sys.platform.startswith("win"):
        avail = _ram_available_windows()
        if avail > 0:
            return avail
    return _ram_available_generic()


def _cpu_info() -> tuple[str, int, int]:
    """Return (model, cores, threads) best-effort, with graceful fallbacks."""
    model = "unknown"
    cores = os.cpu_count() or 0
    threads = cores
    try:
        model = platform.processor() or model
    except Exception:  # noqa: BLE001
        pass
    if sys.platform.startswith("win"):
        try:
            ident = os.environ.get("PROCESSOR_IDENTIFIER", "")
            if ident and model in ("unknown", ""):
                model = ident
            nproc = os.environ.get("NUMBER_OF_PROCESSORS")
            if nproc and nproc.isdigit():
                threads = int(nproc)
        except Exception:  # noqa: BLE001
            pass
    return model, cores, threads


def _gpu_info() -> tuple[str, str, float, str]:
    """Return (vendor, model, vram_gb, accel). Vendor/accel best-effort."""
    vendor = ""
    model = ""
    accel = ""
    vram = 0.0

    smi = shutil.which("nvidia-smi")
    if smi:
        out = _run_command([smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        if out:
            for raw in out.strip().splitlines():
                try:
                    parts = [p.strip() for p in raw.split(",")]
                    if len(parts) >= 2 and parts[0]:
                        model = parts[0] or model
                        vram = float(parts[1].split(" ")[0].replace(",", "")) / 1024.0
                except (ValueError, IndexError):
                    continue
        if model:
            vendor = "NVIDIA"
            accel = "CUDA"

    # Apple Silicon / Metal (best-effort, no GPU enumeration needed).
    if not model and sys.platform.startswith("darwin"):
        vendor = "Apple"
        model = platform.machine()  # 'arm64' -> Apple Silicon
        accel = "Metal"
        return vendor, model, 0.0, accel

    # Windows fallback: WMI also catches AMD / Intel integrated GPUs.
    if not model and sys.platform.startswith("win"):
        out = _run_command(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | "
             "Select-Object Name,AdapterRAM | ConvertTo-Json -Compress"]
        )
        if out:
            try:
                import json

                data = json.loads(out.strip())
                if isinstance(data, dict):
                    data = [data]
                total_bytes = 0.0
                for gpu in data:
                    name = gpu.get("Name") or ""
                    if name and not model:
                        model = name
                    try:
                        total_bytes += float(gpu.get("AdapterRAM") or 0.0)
                    except (TypeError, ValueError):
                        pass
                if total_bytes >= (1024**3):
                    vram = total_bytes / (1024**3)
                if model:
                    up = model.upper()
                    if "NVIDIA" in up:
                        vendor = "NVIDIA"
                        accel = "CUDA"
                    elif "AMD" in up or "RADEON" in up:
                        vendor = "AMD"
                    elif "INTEL" in up or "ARC" in up:
                        vendor = "Intel"
            except (ValueError, TypeError):
                pass
    return vendor, model, vram, accel


def detect_arch() -> str:
    try:
        import platform

        return platform.machine()
    except Exception:  # noqa: BLE001
        return ""


def detect_disk_free_gb(path: str | None = None) -> float:
    try:
        usage = shutil.disk_usage(path or os.getcwd())
        return usage.free / (1024**3)
    except (OSError, ValueError):
        return 0.0


def detect_os_name() -> str:
    if sys.platform.startswith("win"):
        return f"Windows {sys.getwindowsversion().major}"
    if sys.platform.startswith("darwin"):
        return "macOS"
    return "Linux"


def detect_machine() -> MachineProfile:
    ram = detect_ram()
    gpu_avail, gpu_vram = detect_gpu_vram()
    vendor, model, vram_from_info, accel = _gpu_info()
    cpu_model, cores, threads = _cpu_info()
    # Prefer the richer GPU probe; fall back to the older VRAM reader.
    if vram_from_info > 0 and not gpu_avail:
        gpu_avail, gpu_vram = True, vram_from_info
    elif gpu_avail and vram_from_info > 0:
        gpu_vram = vram_from_info
    return MachineProfile(
        ram_total_gb=ram,
        ram_available_gb=detect_ram_available(),
        cpu_count=cores,
        cpu_cores=cores,
        cpu_threads=threads,
        cpu_model=cpu_model,
        gpu_vram_gb=gpu_vram,
        gpu_available=gpu_avail,
        gpu_vendor=vendor,
        gpu_model=model,
        gpu_accel=accel,
        arch=detect_arch(),
        disk_free_gb=detect_disk_free_gb(),
        os_name=detect_os_name(),
        python=sys.version.split()[0],
    )


def detect_python_packages() -> dict[str, bool]:
    """Best-effort presence of the optional extras (voice, dev)."""
    out = {}
    for mod in ("vosk", "pyttsx3", "sounddevice", "fastapi", "uvicorn", "pytest"):
        try:
            __import__(mod)
            out[mod] = True
        except ImportError:
            out[mod] = False
    return out


def _find_ollama_bin() -> str | None:
    """Locate the `ollama` executable, on PATH or at common install locations."""
    on_path = shutil.which("ollama")
    if on_path:
        return on_path
    candidates = []
    if sys.platform.startswith("win"):
        import winreg

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                                        0, winreg.KEY_READ | view) as uninstall:
                        for index in range(winreg.QueryInfoKey(uninstall)[0]):
                            try:
                                with winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, index)) as app:
                                    display, _ = winreg.QueryValueEx(app, "DisplayName")
                                    if display != "Ollama":
                                        continue
                                    location, _ = winreg.QueryValueEx(app, "InstallLocation")
                                    candidates.append(os.path.join(location, "ollama.exe"))
                            except OSError:
                                continue
                except OSError:
                    continue
        candidates.append(
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Programs", "Ollama", "ollama.exe",
            )
        )
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def detect_ollama(url: str = "http://localhost:11434") -> OllamaStatus:
    ollama_bin = _find_ollama_bin()
    status = OllamaStatus(
        installed=ollama_bin is not None,
        base_url=url,
        message="ollama binary not found on PATH",
    )
    if ollama_bin:
        out = _run_command([ollama_bin, "list"])
        if out is not None and "NAME" in out:
            status.running = True
            status.message = "Ollama is installed and serving"
            for line in out.splitlines()[1:]:
                name = line.split()[0].strip() if line.split() else ""
                if name:
                    status.models.append(name)
        else:
            # Binary present but the `list` command failed (server not serving).
            # Fall back to an HTTP probe: it may still be running remotely isn't
            # the point here — report accurately what the binary told us.
            status.message = (
                "Ollama binary found but not serving; run `ollama serve` "
                "(or start the Ollama app)"
            )
    # Regardless of the binary, an answering endpoint is the real signal that
    # the server is up. If it answers, treat it as installed+running.
    if _url_reachable(url):
        status.installed = True
        status.running = True
        if status.models or (not status.message or "not serving" in status.message):
            status.message = "Ollama is installed and serving"
        # If the binary was found we already filled models; otherwise the models
        # stay as discovered (empty list is fine).
    return status


def _url_reachable(url: str, timeout: float = 2.0) -> bool:
    """True when an HTTP server answers at `url` (dependency-free)."""
    try:
        from urllib.request import Request, urlopen

        with urlopen(Request(url, method="GET"), timeout=timeout) as resp:
            return resp.status < 500
    except Exception:
        return False


def start_ollama() -> bool:
    """Launch `ollama serve` detached (best-effort). True if the process started."""
    ollama_bin = _find_ollama_bin()
    if not ollama_bin:
        return False
    try:
        subprocess.Popen(
            [ollama_bin, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        return True
    except Exception:
        return False


def ensure_ollama_running(
    url: str = "http://localhost:11434",
    wait_s: float = 10.0,
) -> OllamaStatus:
    """Best-effort: make sure an Ollama server answers at `url`.

    Fast path: if the endpoint already responds, nothing happens. Otherwise it
    launches `ollama serve` in the background and polls until the port binds.
    Returns an `OllamaStatus` whose `started_now` is True when this call had to
    start the server itself.
    """
    if _url_reachable(url):
        return detect_ollama(url)
    started = start_ollama()
    if not started:
        return detect_ollama(url)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if _url_reachable(url):
            status = detect_ollama(url)
            status.started_now = True
            return status
        time.sleep(0.5)
    status = detect_ollama(url)
    status.started_now = True
    return status


def _find_opencode_bin() -> str | None:
    """Locate the `opencode` executable, on PATH or at common install locations."""
    on_path = shutil.which("opencode")
    if on_path:
        return on_path
    candidates = []
    if sys.platform.startswith("win"):
        candidates.append(
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Programs", "opencode", "opencode.exe",
            )
        )
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _opencode_modules(url: str, timeout: float = 2.0) -> tuple[list[str], list[str], bool]:
    """(providers, models, configured) best-effort via the OpenCode HTTP API."""
    providers: list[str] = []
    models: list[str] = []
    configured = False
    try:
        import json
        from urllib.request import Request, urlopen

        with urlopen(Request(url + "/config/providers", method="GET"), timeout=timeout) as resp:
            if resp.status < 500:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
                if isinstance(payload, dict):
                    config = payload.get("config") or payload
                    if isinstance(config, dict):
                        for key, value in config.items():
                            if isinstance(value, dict):
                                providers.append(key)
                                configured = configured or bool(
                                    value.get("apiKey")
                                    or value.get("enabled")
                                    or value.get("models")
                                )
                                ms = value.get("models") or []
                                for m in ms:
                                    if isinstance(m, dict) and m.get("id"):
                                        models.append(f"{key}/{m['id']}")
    except Exception:
        pass
    return providers, models, configured


def detect_opencode(url: str = "http://127.0.0.1:4096") -> OpenCodeStatus:
    """Best-effort PHASE 14 detection of an OpenCode installation.

    Reports whether the `opencode` binary is on PATH (installed), whether its
    server answers at `url` (running), and which providers/models it exposes.
    Never modifies anything: reading configuration and health is safe.
    """
    bin_path = _find_opencode_bin()
    status = OpenCodeStatus(
        installed=bin_path is not None,
        base_url=url,
    )
    if bin_path:
        out = _run_command([bin_path, "--version"], timeout=6.0)
        if out:
            status.version = out.strip().splitlines()[0].strip()
    if _url_reachable(url, timeout=2.0):
        status.running = True
        status.providers, status.models, status.configured = _opencode_modules(url)
    providers = ", ".join(status.providers) if status.providers else "none"
    if status.running:
        status.message = (
            f"OpenCode server is running at {url} "
            f"(providers: {providers}, configured: {status.configured})"
        )
    elif status.installed:
        status.message = (
            "OpenCode binary found but the server is not running; "
            "run `opencode` to start it"
        )
    else:
        status.message = "OpenCode not found on PATH"
    return status

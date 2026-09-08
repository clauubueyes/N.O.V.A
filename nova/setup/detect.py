from __future__ import annotations

"""Real machine detection (best-effort, dependency-free core).

These readers are used by the installer to pick the right Ollama models for the
installed hardware. They intentionally go further than the conservative runtime
`ResourceManager` defaults (which stay untouched): on Windows we read physical
RAM and GPU VRAM properly with the standard library + subprocess so a mismatch
between, say, a 4 GB and a 32 GB machine never results in an unusable model.
"""

import os
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

    def summary(self) -> list[str]:
        lines = [
            f"OS:        {self.os_name}",
            f"CPU:       {self.cpu_count} core(s)",
            f"RAM:       {self.ram_total_gb:.1f} GB",
        ]
        if self.gpu_available:
            lines.append(f"GPU VRAM:  {self.gpu_vram_gb:.0f} GB (CUDA-capable)")
        else:
            lines.append("GPU VRAM:  none detected (CPU-only)")
        lines.append(f"Python:    {self.python}")
        return lines


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


def detect_os_name() -> str:
    if sys.platform.startswith("win"):
        return f"Windows {sys.getwindowsversion().major}"
    if sys.platform.startswith("darwin"):
        return "macOS"
    return "Linux"


def detect_machine() -> MachineProfile:
    ram = detect_ram()
    gpu_avail, gpu_vram = detect_gpu_vram()
    return MachineProfile(
        ram_total_gb=ram,
        cpu_count=os.cpu_count() or 0,
        gpu_vram_gb=gpu_vram,
        gpu_available=gpu_avail,
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

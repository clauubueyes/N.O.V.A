from __future__ import annotations

"""System health checks and model catalog validation.

Provides lightweight functions used at chat startup to:
- Gather hardware info for the welcome screen.
- Verify Ollama/OpenCode service status.
- Validate that every model in the router catalog is actually installed,
  removing missing roles and returning clear warnings.
"""

import platform
import sys
from dataclasses import dataclass, field

from nova.core.logging import get_logger

logger = get_logger("core.health")


# ---------------------------------------------------------------------------
# Hardware summary (for the startup screen)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SystemInfo:
    os_name: str
    cpu_model: str
    cpu_cores: int
    ram_gb: float
    gpu_model: str
    gpu_vram_gb: float
    cuda_available: bool
    python_version: str


def gather_system_info() -> SystemInfo:
    """Collect hardware info using existing detect helpers."""
    from nova.setup.detect import detect_gpu_vram, detect_ram

    cpu_model = "unknown"
    cpu_cores = 0
    try:
        import os
        if sys.platform.startswith("win"):
            import subprocess
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            cpu_model = (out.stdout or "").strip().splitlines()[0].strip() or "unknown"
            out2 = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty NumberOfCores"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            cpu_cores = int((out2.stdout or "0").strip().splitlines()[0].strip() or 0)
        else:
            cpu_model = platform.processor() or "unknown"
            cpu_cores = os.cpu_count() or 0
    except Exception:
        cpu_model = platform.processor() or "unknown"
        cpu_cores = os.cpu_count() or 0

    ram_gb = detect_ram()
    gpu_available, gpu_vram = detect_gpu_vram()
    gpu_model = ""
    cuda_available = False
    if gpu_available:
        try:
            import subprocess
            smi_out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            gpu_model = (smi_out.stdout or "").strip().splitlines()[0].strip()
            cuda_available = bool(gpu_model)
        except Exception:
            pass

    os_name = f"{platform.system()} {platform.release()}"
    if sys.platform.startswith("win"):
        try:
            v = sys.getwindowsversion()
            os_name = f"Windows {v.major}.{v.minor}"
        except Exception:
            pass

    return SystemInfo(
        os_name=os_name,
        cpu_model=cpu_model,
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        gpu_model=gpu_model or ("N/A" if not gpu_available else "GPU"),
        gpu_vram_gb=gpu_vram,
        cuda_available=cuda_available,
        python_version=platform.python_version(),
    )


# ---------------------------------------------------------------------------
# Service status
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ServiceStatus:
    name: str
    online: bool
    detail: str = ""


def check_ollama(base_url: str = "http://localhost:11434") -> ServiceStatus:
    """Quick Ollama health check."""
    try:
        import httpx
        r = httpx.get(base_url, timeout=3.0)
        if r.status_code < 400:
            return ServiceStatus(name="Ollama", online=True, detail="running")
    except Exception:
        pass
    return ServiceStatus(name="Ollama", online=False, detail="not reachable")


def check_opencode(base_url: str = "http://127.0.0.1:4096") -> ServiceStatus:
    """Quick OpenCode health check (non-fatal)."""
    try:
        import httpx
        r = httpx.get(f"{base_url}/global/health", timeout=3.0)
        if r.status_code < 400:
            return ServiceStatus(name="OpenCode", online=True, detail="running")
    except Exception:
        pass
    return ServiceStatus(name="OpenCode", online=False, detail="not configured")


# ---------------------------------------------------------------------------
# Model catalog validation
# ---------------------------------------------------------------------------

@dataclass
class ModelValidationResult:
    """Result of validating the router catalog against installed models."""
    valid_catalog: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    missing: dict[str, str] = field(default_factory=dict)  # role -> model name


def validate_catalog(
    catalog: dict[str, str],
    installed_models: list[str],
    default_model: str = "",
) -> ModelValidationResult:
    """Check that every model in the router catalog is actually installed.

    Returns a cleaned catalog (missing roles removed) and a list of human
    warnings. The ``embedding`` role is always kept regardless of validation
    (it is used by MemoryService, not the chat router).
    """
    installed_set = {m.strip().lower() for m in installed_models}
    result = ModelValidationResult()

    for role, model_name in catalog.items():
        if role == "embedding":
            result.valid_catalog[role] = model_name
            continue

        normalized = model_name.strip().lower()
        if normalized in installed_set:
            result.valid_catalog[role] = model_name
        else:
            result.missing[role] = model_name
            result.warnings.append(
                f'Model "{model_name}" not installed — role "{role}" disabled.'
            )
            logger.warning("catalog validation: role '%s' model '%s' not found", role, model_name)

    if default_model:
        nd = default_model.strip().lower()
        if nd not in installed_set:
            # Try to find a close match (e.g. "llama3.1:8b" in installed)
            candidates = [m for m in installed_models if nd.split(":")[0] in m.lower()]
            if candidates:
                result.warnings.append(
                    f'Default model "{default_model}" not found exactly; '
                    f"closest: {candidates[0]}."
                )
            else:
                result.warnings.append(
                    f'Default model "{default_model}" not installed. '
                    "Chat may fail if no other model is available."
                )

    if not result.valid_catalog.get("local") and not result.valid_catalog.get("small"):
        default_candidates = [m for m in installed_models if m.strip().lower() == nd]
        if default_candidates:
            result.valid_catalog["local"] = default_candidates[0]
            result.warnings.append(
                f'Auto-selected "{default_candidates[0]}" as primary model.'
            )

    return result

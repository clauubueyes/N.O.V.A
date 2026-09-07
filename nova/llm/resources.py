from __future__ import annotations

"""PHASE 7 — simple system resource abstraction.

Kept dependency-free on purpose: reads OS counters with the standard library only
so the model router can pick a model according to the available RAM/CPU, an
optional GPU/VRAM reading, and the battery state. Every reading is best-effort
and never raises; anything unreadable falls back to conservative defaults.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class SystemResources:
    """Snapshot of system resource readings (all best-effort)."""

    ram_total_gb: float = 0.0
    ram_available_gb: float = 0.0
    cpu_percent: float = 0.0
    cpu_count: int = 0
    gpu_vram_gb: float = 0.0
    gpu_available: bool = False
    battery_percent: float | None = None
    on_ac_power: bool = True
    source: str = "unknown"

    def ram_available_pct(self) -> float:
        if self.ram_total_gb <= 0:
            return 1.0
        return self.ram_available_gb / self.ram_total_gb


def _read_meminfo_linux() -> dict[str, float]:
    out = {"total": 0.0, "available": 0.0}
    try:
        with open("/proc/meminfo", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                kb = rest.strip().split()[0] if rest.strip() else "0"
                try:
                    value_kb = float(kb)
                except ValueError:
                    continue
                if key == "MemTotal":
                    out["total"] = value_kb / (1024 * 1024)
                elif key == "MemAvailable":
                    out["available"] = value_kb / (1024 * 1024)
    except OSError:
        pass
    return out


class ResourceManager:
    """Best-effort readers for RAM, CPU, GPU/VRAM and battery.

    Each reader is an injectable callable so tests can provide fakes. Defaults
    degrade gracefully when a subsystem cannot be read (e.g. no GPU, no battery).
    """

    def __init__(
        self,
        *,
        ram_reader: Callable[[], dict[str, float]] | None = None,
        cpu_reader: Callable[[], tuple[float, int]] | None = None,
        gpu_reader: Callable[[], tuple[bool, float]] | None = None,
        battery_reader: Callable[[], tuple[float | None, bool]] | None = None,
        source: str = "probe",
    ) -> None:
        self._ram = ram_reader or self._default_ram
        self._cpu = cpu_reader or self._default_cpu
        self._gpu = gpu_reader or self._default_gpu
        self._battery = battery_reader or self._default_battery
        self._source = source

    # -- defaults -----------------------------------------------------------
    def _default_ram(self) -> dict[str, float]:
        if sys.platform.startswith("linux"):
            return _read_meminfo_linux()
        # Cross-platform fallback: os.sysconf works on many POSIX; on Windows the
        # global heap size is not the physical RAM, so keep it conservative.
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            total = pages * page_size / (1024**3)
        except (ValueError, AttributeError, OSError):
            total = 0.0
        return {"total": total, "available": total}

    def _default_cpu(self) -> tuple[float, int]:
        count = os.cpu_count() or 0
        # Reading real CPU load without psutil is not portable; assume a neutral
        # moderate load so the router does not over/under-select.
        return 50.0, count

    @staticmethod
    def _default_gpu() -> tuple[bool, float]:
        # No GPU/DX reader without extra deps: report not available.
        return False, 0.0

    @staticmethod
    def _default_battery() -> tuple[float | None, bool]:
        # No cross-platform battery reader in the stdlib; assume AC power.
        return None, True

    # -- public API ---------------------------------------------------------
    def snapshot(self) -> SystemResources:
        try:
            ram = self._ram()
        except Exception:
            ram = {}
        try:
            cpu_percent, cpu_count = self._cpu()
        except Exception:
            cpu_percent, cpu_count = 50.0, 0
        try:
            gpu_available, gpu_vram_gb = self._gpu()
        except Exception:
            gpu_available, gpu_vram_gb = False, 0.0
        try:
            battery_pct, on_ac = self._battery()
        except Exception:
            battery_pct, on_ac = None, True

        total = float(ram.get("total", 0.0))
        available = float(ram.get("available", ram.get("total", 0.0)))
        return SystemResources(
            ram_total_gb=total,
            ram_available_gb=available,
            cpu_percent=float(cpu_percent),
            cpu_count=int(cpu_count),
            gpu_vram_gb=float(gpu_vram_gb),
            gpu_available=bool(gpu_available),
            battery_percent=(float(battery_pct) if battery_pct is not None else None),
            on_ac_power=bool(on_ac),
            source=self._source,
        )


def default_resource_manager() -> ResourceManager:
    return ResourceManager()

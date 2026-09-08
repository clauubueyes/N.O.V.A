from __future__ import annotations

"""Model recommendation for the detected hardware.

This module is the thin stable API the rest of the codebase (CLI, API, assistant)
uses. The actual decision lives in `nova.setup.selector` + `nova.setup.catalog`,
which combine RAM, VRAM, GPU, CPU and disk into a capability profile instead of a
rigid RAM list (ADR-013: local-first). The functions here keep their old names so
nothing else changes.
"""

import os
from dataclasses import dataclass

from nova.setup.detect import MachineProfile

EMBED_MODEL = "nomic-embed-text"

#: Minimum total RAM (GB) to comfortably run an 8B chat model (kept as a sanity
#: floor for the compatibility helpers below).
_RAM_8B_GB = 12.0
#: Minimum total RAM (GB) to also schedule a 7B coding model.
_RAM_CODING_GB = 16.0


@dataclass(frozen=True)
class ModelRec:
    role: str
    model: str
    reason: str


def ram_to_flags(ram_total_gb: float, gpu_vram_gb: float) -> tuple[bool, bool]:
    """Return (can_8b, can_coding) based on RAM + optional GPU VRAM.

    A CUDA GPU with enough VRAM can raise the ceiling even with modest RAM
    because a chunk of the model lives in VRAM. This is a heuristic; the
    conservative path never assumes more than the total RAM.
    """
    can_8b = ram_total_gb >= _RAM_8B_GB or gpu_vram_gb >= 8.0
    can_coding = (ram_total_gb >= _RAM_CODING_GB or gpu_vram_gb >= 8.0) and can_8b
    return can_8b, can_coding


def _profile_from(ram_total_gb: float, gpu_vram_gb: float) -> MachineProfile:
    return MachineProfile(
        ram_total_gb=ram_total_gb,
        cpu_count=os.cpu_count() or 8,
        gpu_vram_gb=gpu_vram_gb,
        gpu_available=gpu_vram_gb > 0.0,
        os_name="?",
        python="?",
    )


def recommended_models(ram_total_gb: float, gpu_vram_gb: float) -> list[ModelRec]:
    """Choose the Ollama models the installer should pull for this hardware."""
    from nova.setup.selector import select_stack

    stack = select_stack(_profile_from(ram_total_gb, gpu_vram_gb), check_disk=False)
    return [
        ModelRec(choice.role, choice.spec.name, choice.spec.description)
        for choice in stack
        if choice.install
    ]


def default_model_for(ram_total_gb: float, gpu_vram_gb: float) -> ModelRec:
    recs = recommended_models(ram_total_gb, gpu_vram_gb)
    for rec in recs:
        if rec.role == "local":
            return rec
    return recs[0] if recs else ModelRec("local", "llama3.1:8b", "fallback")


def normalize_model_name(name: str) -> str:
    """Return the canonical '<name>' ignoring the ':latest'/'<tag>' suffix."""
    base, _, tag = name.partition(":")
    if tag in ("latest", ""):
        return base
    return name


def wanted_models(ram_total_gb: float, gpu_vram_gb: float) -> set[str]:
    """Set of canonical model names the installer wants present."""
    return {normalize_model_name(rec.model) for rec in recommended_models(ram_total_gb, gpu_vram_gb)}


def missing_models(installed: list[str], ram_total_gb: float, gpu_vram_gb: float) -> set[str]:
    have = {normalize_model_name(m) for m in installed}
    return wanted_models(ram_total_gb, gpu_vram_gb) - have
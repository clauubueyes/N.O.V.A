from __future__ import annotations

"""Model recommendation for the detected hardware.

Kept hardware-aware but deliberately conservative (ADR-013: local-first, no paid
cloud). The installer uses these to pick a default chat model, a small model for
light tasks, an optional coding model and the embedding model — matching the
`llm.models` catalog the ModelRouter consumes.
"""

from dataclasses import dataclass

#: Minimum total RAM (GB) to comfortably run an 8B chat model.
_RAM_8B_GB = 12.0
#: Minimum total RAM (GB) to also schedule a 7B coding model.
_RAM_CODING_GB = 16.0

EMBED_MODEL = "nomic-embed-text"


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


def recommended_models(ram_total_gb: float, gpu_vram_gb: float) -> list[ModelRec]:
    """Choose the Ollama models to install for the detected hardware."""
    can_8b, can_coding = ram_to_flags(ram_total_gb, gpu_vram_gb)
    recs: list[ModelRec] = [
        ModelRec("embedding", EMBED_MODEL, "memory embeddings"),
    ]
    if can_8b:
        recs.append(ModelRec("local", "llama3.1:8b", "general chat (8B)"))
        recs.append(ModelRec("small", "llama3.2:1b", "light/fast tasks (1B)"))
        if can_coding:
            recs.append(ModelRec("coding", "qwen2.5-coder:7b", "coding assistant (7B)"))
    else:
        recs.append(ModelRec("local", "llama3.2:3b", "general chat (3B, fits RAM)"))
        recs.append(ModelRec("small", "llama3.2:1b", "light/fast tasks (1B)"))
    return recs


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

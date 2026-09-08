from __future__ import annotations

"""Local model catalog for the adaptive installer.

Metadata for the Ollama models N.O.V.A. knows how to install, grouped by the
semantic role they fill (embedding / fast / general / coding / reasoning /
vision). Sizes are the conservative real-world Q4_K_M numbers for each model in
the public Ollama library; the selector combines them with the detected RAM,
VRAM, CPU and disk instead of a hardcoded RAM list.

Extend the catalog by adding a `ModelSpec` to the relevant role list — nothing
else needs to change. When the machine is offline (or when Ollama reports
metadata we prefer to trust over this table), the selector still works: it only
ever *falls back* to this catalog, never requires external metadata.
"""

from dataclasses import dataclass, field

#: All semantic roles the selector understands, in priority display order.
ROLE_ORDER: tuple[str, ...] = ("embedding", "general", "fast", "coding", "reasoning", "vision")

#: Per-model runtime memory overhead beyond weights + KV cache (GB).
RUNTIME_OVERHEAD_GB = 1.5


@dataclass(frozen=True)
class ModelSpec:
    """Static metadata for one installable Ollama model."""

    name: str
    role: str                          # semantic category (ROLE_ORDER)
    params_b: float                    # approximate parameters, in billions
    quant: str                         # quantization the sizes assume
    context: int                       # default context used for the estimate
    weights_gb: float                  # approx download/weights size (GB)
    memory_gb: float | None = None     # running estimate override (GB)
    min_ram_gb: float = 0.0            # hard floor on TOTAL RAM to even try
    priority: int = 1                  # lower = preferred within the role
    description: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    min_ollama: str = ""
    architectures: tuple[str, ...] = field(default_factory=tuple)
    verified: bool = True

    def mem_estimate(self, context: int | None = None) -> float:
        if self.memory_gb:
            return self.memory_gb
        ctx = context or self.context
        kv_gb = self.params_b * ctx / 200_000.0
        return self.weights_gb + kv_gb + RUNTIME_OVERHEAD_GB


# Catalog entries. `priority` orders candidates inside a role (1 = best when it
# fits). Weights are Ollama Q4_K_M sizes rounded up ~10% (conservative).
_EMBEDDING = [
    ModelSpec(
        name="nomic-embed-text",
        role="embedding",
        params_b=0.137,
        quant="F32",
        context=8192,
        weights_gb=0.28,
        memory_gb=0.9,
        min_ram_gb=2.0,
        description="memory embeddings (must-have)",
        tags=("embedding", "small"),
    ),
]

_FAST = [
    ModelSpec(
        name="llama3.2:1b",
        role="fast",
        params_b=1.24,
        quant="Q4_K_M",
        context=8192,
        weights_gb=1.33,
        memory_gb=2.6,
        min_ram_gb=4.0,
        description="light/fast tasks (1B)",
        tags=("fast", "small"),
    ),
    ModelSpec(
        name="llama3.2:3b",
        role="fast",
        params_b=3.21,
        quant="Q4_K_M",
        context=8192,
        weights_gb=2.04,
        memory_gb=3.6,
        min_ram_gb=4.0,
        priority=2,
        description="fast/large-small tasks (3B)",
        tags=("fast", "small", "general"),
    ),
]

_GENERAL = [
    ModelSpec(
        name="llama3.1:8b",
        role="general",
        params_b=8.03,
        quant="Q4_K_M",
        context=8192,
        weights_gb=4.91,
        memory_gb=6.9,
        min_ram_gb=12.0,
        description="general chat (8B)",
        tags=("chat", "general"),
    ),
    ModelSpec(
        name="qwen2.5:7b",
        role="general",
        params_b=7.61,
        quant="Q4_K_M",
        context=32768,
        weights_gb=4.72,
        memory_gb=7.2,
        min_ram_gb=8.0,
        priority=2,
        description="general chat/multilingual (7B)",
        tags=("chat", "general"),
    ),
    ModelSpec(
        name="llama3.2:3b",
        role="general",
        params_b=3.21,
        quant="Q4_K_M",
        context=8192,
        weights_gb=2.04,
        memory_gb=3.6,
        min_ram_gb=4.0,
        priority=3,
        description="general chat (3B, fits modest RAM)",
        tags=("chat", "general", "small"),
    ),
    ModelSpec(
        name="llama3.2:1b",
        role="general",
        params_b=1.24,
        quant="Q4_K_M",
        context=8192,
        weights_gb=1.33,
        memory_gb=2.6,
        min_ram_gb=4.0,
        priority=4,
        description="general chat (1B, last-resort fallback)",
        tags=("chat", "general", "small"),
    ),
]

_CODING = [
    ModelSpec(
        name="qwen2.5-coder:7b",
        role="coding",
        params_b=7.61,
        quant="Q4_K_M",
        context=32768,
        weights_gb=4.72,
        memory_gb=7.4,
        min_ram_gb=8.0,
        description="coding assistant (7B)",
        tags=("coding",),
    ),
    ModelSpec(
        name="qwen2.5-coder:14b",
        role="coding",
        params_b=14.77,
        quant="Q4_K_M",
        context=32768,
        weights_gb=9.06,
        memory_gb=13.3,
        min_ram_gb=16.0,
        priority=2,
        description="coding assistant large (14B)",
        tags=("coding",),
    ),
    ModelSpec(
        name="qwen2.5-coder:1.5b",
        role="coding",
        params_b=1.54,
        quant="Q4_K_M",
        context=32768,
        weights_gb=1.04,
        memory_gb=2.8,
        min_ram_gb=4.0,
        priority=3,
        description="small coding assistant (1.5B)",
        tags=("coding", "small"),
    ),
]

_REASONING = [
    ModelSpec(
        name="qwen3:8b",
        role="reasoning",
        params_b=8.23,
        quant="Q4_K_M",
        context=32768,
        weights_gb=4.92,
        memory_gb=7.3,
        min_ram_gb=12.0,
        description="reasoning agent (8B)",
        tags=("reasoning",),
    ),
    ModelSpec(
        name="qwen3:4b",
        role="reasoning",
        params_b=3.99,
        quant="Q4_K_M",
        context=32768,
        weights_gb=2.40,
        memory_gb=4.6,
        min_ram_gb=6.0,
        priority=2,
        description="reasoning agent (4B)",
        tags=("reasoning",),
    ),
]

_VISION = [
    ModelSpec(
        name="llama3.2-vision:11b",
        role="vision",
        params_b=11.0,
        quant="Q4_K_M",
        context=8192,
        weights_gb=7.0,
        memory_gb=9.8,
        min_ram_gb=16.0,
        description="vision/multimodal (11B)",
        tags=("vision",),
    ),
    ModelSpec(
        name="qwen2.5vl:7b",
        role="vision",
        params_b=7.6,
        quant="Q4_K_M",
        context=32768,
        weights_gb=5.5,
        memory_gb=8.2,
        min_ram_gb=12.0,
        priority=2,
        description="vision/multimodal (7B)",
        tags=("vision",),
    ),
]

#: role -> ordered candidate list.
SPECS_BY_ROLE: dict[str, list[ModelSpec]] = {
    "embedding": _EMBEDDING,
    "fast": _FAST,
    "general": _GENERAL,
    "coding": _CODING,
    "reasoning": _REASONING,
    "vision": _VISION,
}


def spec_by_name(name: str) -> ModelSpec | None:
    for specs in SPECS_BY_ROLE.values():
        for spec in specs:
            if spec.name == name:
                return spec
    return None


def candidates(role: str) -> list[ModelSpec]:
    """Candidates for a role, best first (priority then size)."""
    specs = sorted(SPECS_BY_ROLE.get(role, ()), key=lambda s: (s.priority, s.weights_gb))
    return specs

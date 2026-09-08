from __future__ import annotations

"""Adaptive model selection (replaces the fixed RAM-based list).

Combines the detected hardware (RAM total + available, VRAM, GPU vendor,
acceleration, CPU cores, architecture, free disk) into a capability profile and
then evaluates every catalog model conservatively:

    RECOMMENDED       fits comfortably with safety margin -> the installer pulls it
    POSSIBLE          fits but tight / would be slow -> shown as [OPTIONAL], not installed
    NOT_RECOMMENDED   does not fit -> never considered

The policy never installs a model class just because the machine is big; at most
one model per role, and only the roles the machine can actually use. Embedding +
general (+ fast) are always guaranteed so a modest PC stays usable.
"""

from dataclasses import dataclass, field
from enum import Enum

from nova.setup.catalog import SPECS_BY_ROLE, ModelSpec, candidates
from nova.setup.detect import MachineProfile

CONFIG_ROLE: dict[str, str] = {
    "embedding": "embedding",
    "general": "local",
    "fast": "small",
    "coding": "coding",
    "reasoning": "reasoning",
    "vision": "vision",
}

#: (tier, min total RAM GB). VRAM may bump a machine one or two tiers up.
_TIER_BOUNDS: tuple[tuple[str, float], ...] = (
    ("ULTRA_LIGHT", 0.0),
    ("LIGHT", 8.0),
    ("BALANCED", 12.0),
    ("PERFORMANCE", 16.0),
    ("HIGH_END", 24.0),
    ("ENTHUSIAST", 48.0),
)
_TIER_ORDER: tuple[str, ...] = tuple(name for name, _ in _TIER_BOUNDS)

#: Extra dip required beyond 8B params before a CPU-only machine gets too slow.
_CPU_SLOW_PARAMS_B = 7.0
_CPU_SLOW_CORES = 6
#: Safe margins (GB) used by the memory fit check.
_REC_MARGIN_GB = 3.0
_POSSIBLE_MARGIN_GB = 1.0
#: Reserve we subtract from total RAM when available RAM is unknown (GB).
_DRAM_UNKNOWN_RESERVE_GB = 2.5
#: Disk headroom kept free after every download (GB).
_DISK_MARGIN_GB = 12.0

#: Semantic roles pulled in (install=True) even when only POSSIBLE --- a usable
#: install always needs them.
_CORE_ROLES = ("embedding", "general", "fast")


class Availability(Enum):
    RECOMMENDED = "RECOMMENDED"
    POSSIBLE = "POSSIBLE"
    NOT_RECOMMENDED = "NOT_RECOMMENDED"


@dataclass
class CapabilityProfile:
    tier: str
    ram_total_gb: float
    ram_available_gb: float
    gpu_vram_gb: float
    gpu_available: bool
    gpu_accel: str
    gpu_model: str = ""
    cpu_count: int = 0
    cpu_model: str = ""
    arch: str = ""
    disk_free_gb: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass
class ModelChoice:
    role: str                     # config catalog key (embedding/small/local/coding/...)
    kind: str                     # semantic category
    spec: ModelSpec
    state: Availability
    install: bool
    memory_gb: float
    disk_ok: bool = True
    reasons: list[str] = field(default_factory=list)


def _bump_tier(tier: str, steps: int) -> str:
    idx = _TIER_ORDER.index(tier)
    idx = min(len(_TIER_ORDER) - 1, max(0, idx + steps))
    return _TIER_ORDER[idx]


def capability_profile(profile: MachineProfile) -> CapabilityProfile:
    """Classify a detected machine into a capability tier."""
    tier = "ULTRA_LIGHT"
    for name, min_ram in _TIER_BOUNDS:
        if profile.ram_total_gb >= min_ram:
            tier = name

    vram = profile.gpu_vram_gb if profile.gpu_available else 0.0
    reasons: list[str] = [f"{profile.ram_total_gb:.0f} GB RAM"]
    if vram >= 32.0:
        tier = _bump_tier(tier, 2)
        reasons.append(">=32 GB VRAM")
    elif vram >= 16.0:
        tier = _bump_tier(tier, 1)
        reasons.append(">=16 GB VRAM")
    if vram >= 8.0:
        reasons.append(f"{vram:.0f} GB VRAM GPU")
    elif profile.gpu_available:
        reasons.append(f"{vram:.0f} GB VRAM GPU")

    if profile.gpu_accel:
        reasons.append(f"accel={profile.gpu_accel}")
    if profile.disk_free_gb > 0:
        reasons.append(f"{profile.disk_free_gb:.0f} GB disk free")
    if profile.cpu_count > 0:
        reasons.append(f"{profile.cpu_count} CPU cores")

    return CapabilityProfile(
        tier=tier,
        ram_total_gb=profile.ram_total_gb,
        ram_available_gb=profile.ram_available_gb,
        gpu_vram_gb=vram,
        gpu_available=profile.gpu_available,
        gpu_accel=profile.gpu_accel,
        gpu_model=profile.gpu_model,
        cpu_count=profile.cpu_count,
        cpu_model=profile.cpu_model,
        arch=profile.arch,
        disk_free_gb=profile.disk_free_gb,
        reasons=reasons,
    )


def _dram_available(cap: CapabilityProfile) -> float:
    if cap.ram_available_gb > 0:
        return cap.ram_available_gb
    return max(0.0, cap.ram_total_gb - _DRAM_UNKNOWN_RESERVE_GB)


def _tier_roles(tier: str) -> list[str]:
    roles = ["embedding", "general", "fast"]
    if tier in ("BALANCED", "PERFORMANCE", "HIGH_END", "ENTHUSIAST"):
        roles.append("coding")
    if tier in ("HIGH_END", "ENTHUSIAST"):
        roles.append("reasoning")
    if tier == "ENTHUSIAST":
        roles.append("vision")
    return roles


def _allowed_for_tier(spec: ModelSpec, kind: str, tier: str) -> bool:
    if kind == "coding" and tier == "BALANCED" and spec.params_b > 2.0:
        return False
    # Large coding agents are only worth it on genuinely strong machines.
    if kind == "coding" and spec.params_b > 10.0 and tier not in ("HIGH_END", "ENTHUSIAST"):
        return False
    return True


def evaluate(spec: ModelSpec, cap: CapabilityProfile, check_disk: bool = False) -> tuple[Availability, list[str]]:
    """Return (state, reasons) for one catalog model on this machine."""
    reasons: list[str] = []
    mem = spec.mem_estimate()

    # Hard floors first.
    if cap.ram_total_gb > 0 and cap.ram_total_gb < spec.min_ram_gb:
        return Availability.NOT_RECOMMENDED, [f"needs {spec.min_ram_gb:.0f} GB RAM total"]
    # A >=7B model needs real system RAM, even with a GPU: VRAM does not feed the
    # OS, the browser or Ollama's own runtime. Keep small machines off big models.
    if spec.params_b >= 7.0 and cap.ram_total_gb > 0 and cap.ram_total_gb < 12.0:
        return Availability.NOT_RECOMMENDED, ["too little total RAM for a 7B+ model"]

    vram = cap.gpu_vram_gb if cap.gpu_available else 0.0
    dram = _dram_available(cap)
    cpu_only = vram <= 0.0

    # VRAM offload shrinks the DRAM footprint; always keep a small DRAM buffer.
    dram_need = mem
    if vram > 0.0:
        offloadable = min(spec.weights_gb, vram * 0.9)
        dram_need = max(1.5, mem - offloadable)

    headroom = dram - dram_need
    if headroom >= _REC_MARGIN_GB:
        state = Availability.RECOMMENDED
    elif headroom >= _POSSIBLE_MARGIN_GB:
        state = Availability.POSSIBLE
        reasons.append(f"tight RAM (~{dram - dram_need:.1f} GB headroom)")
    else:
        return Availability.NOT_RECOMMENDED, [
            f"needs ~{mem:.1f} GB RAM, only ~{dram:.1f} GB usable"
        ]

    # A big model on a small CPU-only box is usable but miserable: cap to POSSIBLE.
    if cpu_only and cap.cpu_count > 0 and cap.cpu_count < _CPU_SLOW_CORES and spec.params_b > _CPU_SLOW_PARAMS_B:
        state = Availability.POSSIBLE
        reasons.append(f"would be slow on {cap.cpu_count}-core CPU (no GPU)")

    if check_disk and cap.disk_free_gb > 0 and (cap.disk_free_gb - spec.weights_gb) < _DISK_MARGIN_GB:
        state = Availability.POSSIBLE
        reasons.append(
            f"needs ~{spec.weights_gb:.1f} GB disk, only {cap.disk_free_gb:.0f} GB free"
        )

    return state, reasons


def _disk_ok(spec: ModelSpec, cap: CapabilityProfile) -> bool | None:
    """True/False when disk is known, None when it cannot be checked."""
    if cap.disk_free_gb <= 0:
        return None
    return (cap.disk_free_gb - spec.weights_gb) >= _DISK_MARGIN_GB


def select_stack(profile: MachineProfile, check_disk: bool = False,
                 catalog: dict[str, list[ModelSpec]] | None = None) -> list[ModelChoice]:
    """Pick the best model per role for this machine.

    Returns one `ModelChoice` per usable semantic role. Core roles
    (embedding/general/fast) always end up `install=True` (falling back to the
    smallest candidate when nothing fits comfortably); optional roles
    (coding/reasoning/vision) are installed only when RECOMMENDED.
    """
    cap = capability_profile(profile)
    dram = _dram_available(cap)
    choices: list[ModelChoice] = []

    for kind in _tier_roles(cap.tier):
        available = candidates(kind) if catalog is None else sorted(
            catalog.get(kind, []), key=lambda s: (s.priority, s.weights_gb)
        )
        specs = [s for s in available if s.verified and _allowed_for_tier(s, kind, cap.tier)]
        if not specs:
            continue

        ranked: list[tuple[ModelSpec, Availability, list[str]]] = []
        for spec in specs:
            state, reasons = evaluate(spec, cap, check_disk=check_disk)
            ranked.append((spec, state, reasons))

        recommended = [(s, st, r) for s, st, r in ranked if st is Availability.RECOMMENDED]
        possible = [(s, st, r) for s, st, r in ranked if st is Availability.POSSIBLE]

        core = kind in _CORE_ROLES
        chosen: ModelSpec | None = None
        state: Availability = Availability.NOT_RECOMMENDED
        install = False
        reasons: list[str] = []

        if core:
            if recommended:
                chosen, state, reasons = recommended[0]
            elif possible:
                chosen, state, reasons = possible[0]
                state = Availability.RECOMMENDED
                reasons = [f"smallest usable {kind} model on this machine"]
            elif ranked:
                chosen, state, reasons = min(ranked, key=lambda t: t[0].weights_gb)  # absolute fallback
                state = Availability.RECOMMENDED
                reasons = [f"last-resort {kind} model"]
            install = chosen is not None
        else:
            if recommended:
                chosen, state, reasons = recommended[0]
                install = True
            elif possible:
                chosen, state, reasons = possible[0]  # [OPTIONAL] only
                install = False

        if chosen is None:
            continue

        # Disk guard: prefer a smaller candidate that leaves the margin free.
        disk_ok = _disk_ok(chosen, cap)
        if install and disk_ok is False and check_disk:
            smaller = [
                s for s, st, _ in ranked
                if st is not Availability.NOT_RECOMMENDED and s.weights_gb < chosen.weights_gb
            ]
            candidate = next(
                (s for s in smaller if _disk_ok(s, cap) is not False),
                None,
            )
            if candidate is not None:
                chosen = candidate
                state = Availability.RECOMMENDED
                reasons = [f"smaller model used to respect free disk ({cap.disk_free_gb:.0f} GB)"]
                disk_ok = True
            else:
                install = False
                state = Availability.POSSIBLE
                reasons = [
                    f"insufficient free disk for {chosen.weights_gb:.1f} GB model "
                    f"({cap.disk_free_gb:.0f} GB free)"
                ]
                disk_ok = True

        choices.append(
            ModelChoice(
                role=CONFIG_ROLE[kind],
                kind=kind,
                spec=chosen,
                state=state,
                install=install,
                memory_gb=chosen.mem_estimate(),
                disk_ok=disk_ok is not False,
                reasons=list(reasons),
            )
        )

    return choices


def fallback_candidate(kind: str, name: str) -> ModelSpec | None:
    """Next (preferably smaller) catalog model for a role, for pull retries."""
    current = next((s for s in SPECS_BY_ROLE.get(kind, ()) if s.name == name), None)
    rest = [s for s in candidates(kind) if s.name != name]
    if not rest:
        return None
    if current is None:
        return rest[0]
    smaller = [s for s in rest if s.weights_gb < current.weights_gb]
    return (smaller or rest)[0]


def pprint_plan(profile: MachineProfile) -> None:
    """Print the human-readable install plan (used before any download)."""
    for line in plan_lines(profile):
        print(line)


def plan_lines(profile: MachineProfile) -> list[str]:
    cap = capability_profile(profile)
    choices = select_stack(profile, check_disk=True)
    lines: list[str] = []

    lines.append("\nHardware detected")
    cpu = cap.cpu_model or "unknown"
    arch = cap.arch or "unknown"
    lines.append(f"  CPU:       {cpu} ({cap.cpu_count} core(s), {arch})")
    avail = cap.ram_available_gb or (cap.ram_total_gb - _DRAM_UNKNOWN_RESERVE_GB)
    lines.append(f"  RAM:       {cap.ram_total_gb:.1f} GB total / ~{avail:.1f} GB available")
    if cap.gpu_available:
        gpu_model = cap.gpu_model or "GPU"
        gpu_line = f"  GPU:       {gpu_model} ({cap.gpu_vram_gb:.0f} GB VRAM"
        gpu_line += f", {cap.gpu_accel})" if cap.gpu_accel else ")"
        lines.append(gpu_line)
    else:
        lines.append("  GPU:       none detected (CPU-only)")
    lines.append(f"  Disk free: {cap.disk_free_gb:.0f} GB" if cap.disk_free_gb > 0 else "  Disk free: unknown")
    lines.append(f"  OS:        {profile.os_name}")

    lines.append(f"\nCapability profile: {cap.tier}")
    lines.append("Recommended N.O.V.A. stack:")
    total_storage = 0.0
    max_memory = 0.0
    for choice in choices:
        total_storage += choice.spec.weights_gb
        max_memory = max(max_memory, choice.memory_gb)
        kind_label = choice.kind.capitalize()
        if choice.install:
            lines.append(f"  [OK] {kind_label:<11} {choice.spec.name:<22} ~{choice.spec.weights_gb:.1f} GB")
        else:
            lines.append(f"  [OPT] {kind_label:<11} {choice.spec.name:<22} ~{choice.spec.weights_gb:.1f} GB")
        if choice.reasons:
            lines.append(f"         - {', '.join(choice.reasons)}")
    lines.append(f"\n  Estimated storage: {total_storage:.1f} GB")
    lines.append(f"  Estimated peak memory: {max_memory:.1f} GB")
    return lines

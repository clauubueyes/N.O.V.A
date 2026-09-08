from __future__ import annotations

"""Tests for the adaptive model selector (nova.setup.catalog + selector).

Keep these offline: selections are pure functions over a `MachineProfile`; no
models are downloaded and no hardware is queried. They pin the "stability over
size" policy so a modest PC never ends up with an unusable stack.
"""

import pytest

from nova.setup.detect import MachineProfile
from nova.setup.selector import (
    Availability,
    capability_profile,
    fallback_candidate,
    select_stack,
)


def _profile(ram=16.0, cpu=8, vram=0.0, gpu=False, disk=0.0) -> MachineProfile:
    return MachineProfile(
        ram_total_gb=ram,
        ram_available_gb=0.0,
        cpu_count=cpu,
        gpu_vram_gb=vram,
        gpu_available=gpu,
        os_name="test",
        python="3",
        cpu_model=f"fake-{cpu}core",
        disk_free_gb=disk,
    )


def _by_kind(stack):
    return {c.kind: c for c in stack}


def _installed(stack):
    return {c.kind: c.spec.name for c in stack if c.install}


def test_8gb_no_gpu_stack_is_light():
    """A modest PC gets embedding + a small general + a tiny fast model, no coding."""
    stack = select_stack(_profile(ram=8.0, cpu=4), check_disk=False)
    assert capability_profile(_profile(ram=8.0, cpu=4)).tier == "LIGHT"
    by = _by_kind(stack)
    assert by["embedding"].spec.name == "nomic-embed-text"
    assert by["embedding"].install is True
    assert by["general"].install is True
    assert by["general"].spec.name == "llama3.2:3b"
    assert by["fast"].install is True
    # A <8B machine must never be handed a 7B+ model, GPU or not.
    assert "coding" not in by
    for choice in stack:
        if choice.install:
            assert choice.spec.params_b < 7.0


def test_16gb_no_gpu_stack_is_performance():
    stack = select_stack(_profile(ram=16.0, cpu=8), check_disk=False)
    assert capability_profile(_profile(ram=16.0, cpu=8)).tier == "PERFORMANCE"
    by = _by_kind(stack)
    assert by["general"].install is True
    assert by["general"].spec.name == "llama3.1:8b"
    assert by["coding"].install is True
    assert by["coding"].spec.name == "qwen2.5-coder:7b"
    assert by["fast"].spec.name == "llama3.2:1b"
    assert "reasoning" not in by
    assert "vision" not in by


def test_32gb_gpu8gb_stack_adds_reasoning_but_not_vision():
    p = _profile(ram=32.0, cpu=8, vram=8.0, gpu=True)
    stack = select_stack(p, check_disk=False)
    assert capability_profile(p).tier == "HIGH_END"
    by = _by_kind(stack)
    assert by["general"].spec.name == "llama3.1:8b"
    assert "reasoning" in by and by["reasoning"].install is True
    assert by["reasoning"].spec.name == "qwen3:8b"
    assert "vision" not in by


def test_64gb_gpu24gb_stack_is_enthusiast_including_vision():
    p = _profile(ram=64.0, cpu=16, vram=24.0, gpu=True)
    stack = select_stack(p, check_disk=False)
    assert capability_profile(p).tier == "ENTHUSIAST"
    by = _by_kind(stack)
    assert by["reasoning"].install is True
    assert by["vision"].install is True
    assert by["vision"].spec.name == "llama3.2-vision:11b"
    # Enthusiast still means one model per role, not a pile.
    assert len([c for c in stack if c.install]) <= 6


def test_no_gpu_needed_for_basic_stack():
    """Without any GPU the general + fast models come purely from RAM/CPU."""
    for ram in (16.0, 24.0, 32.0):
        stack = select_stack(_profile(ram=ram, cpu=8), check_disk=False)
        assert _installed(stack)["general"]
        assert _installed(stack)["embedding"]
    # 24GB CPU-only reaches HIGH_END -> reasoning appears without a GPU.
    p = _profile(ram=24.0, cpu=8)
    assert capability_profile(p).tier == "HIGH_END"
    assert "reasoning" in _by_kind(select_stack(p, check_disk=False))


def test_unreachable_hardware_never_raises_and_keeps_fallback():
    """Missing detection data must degrade to a safe ULTRA_LIGHT stack."""
    p = MachineProfile(
        ram_total_gb=0.0, cpu_count=0, gpu_vram_gb=0.0, gpu_available=False,
        os_name="?", python="?", cpu_model="unknown",
    )
    stack = select_stack(p, check_disk=False)
    assert stack, "selector must still return a plan"
    assert any(c.kind == "embedding" and c.install for c in stack)
    assert any(c.kind == "general" for c in stack)


def test_little_disk_blocks_downloads():
    """With ~6 GB free nothing with weights > margin should be install==True."""
    p = _profile(ram=32.0, cpu=8, vram=8.0, gpu=True, disk=6.0)
    stack = select_stack(p, check_disk=True)
    assert all(not c.install for c in stack)
    assert all(c.state is Availability.POSSIBLE for c in stack)


def test_disk_constraint_downgrades_to_smaller_general():
    """14 GB free still fits the 1B/1.5B models, so the stack falls back to them."""
    p = _profile(ram=32.0, cpu=8, vram=8.0, gpu=True, disk=14.0)
    stack = select_stack(p, check_disk=True)
    general = _by_kind(stack)["general"]
    assert general.install is True
    assert general.spec.name == "llama3.2:1b"
    embed = _by_kind(stack)["embedding"]
    assert embed.install is True
    # Coding can still install the small 1.5B coder (fits the disk); the 7B one
    # must never be picked with only 14 GB free.
    coding = _by_kind(stack)["coding"]
    assert coding.install is True
    assert coding.spec.name == "qwen2.5-coder:1.5b"
    # Reasoning has no candidate tiny enough for the disk -> not installed.
    reasoning = _by_kind(stack)["reasoning"]
    assert reasoning.install is False
    assert all(c.install is False for c in stack if c.kind in ("reasoning",))


def test_lots_of_disk_allows_full_stack():
    p = _profile(ram=64.0, cpu=16, vram=24.0, gpu=True, disk=120.0)
    stack = select_stack(p, check_disk=True)
    assert all(c.install for c in stack)
    assert _installed(stack)["vision"] == "llama3.2-vision:11b"


def test_fallback_candidate_prefers_smaller():
    fb = fallback_candidate("general", "llama3.1:8b")
    assert fb is not None and fb.weights_gb < 4.91
    fb = fallback_candidate("coding", "qwen2.5-coder:7b")
    assert fb is not None and fb.weights_gb < 4.72
    # No smaller embedding exists -> None (nothing to retry).
    assert fallback_candidate("embedding", "nomic-embed-text") is None


def test_recommended_maps_to_config_roles(tmp_path, monkeypatch):
    """`llm.models` catalog keys the ModelRouter consumes must come out intact."""
    from nova.setup.models import recommended_models

    recs = recommended_models(32.0, 8.0)
    roles = {rec.role for rec in recs}
    assert {"embedding", "small", "local", "coding", "reasoning"} <= roles
    models = {rec.model for rec in recs}
    assert "llama3.1:8b" in models
    assert "qwen2.5-coder:7b" in models
    assert "nomic-embed-text" in models
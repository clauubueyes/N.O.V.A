from __future__ import annotations

"""Tests for nova.setup: machine detection, model recommendation, safe
auto-provisioning and autostart. All hardware/Ollama interactions are faked so
the suite runs offline and on any machine."""

from pathlib import Path

import pytest

from nova.setup.autostart import AutostartError
from nova.setup.detect import (
    MachineProfile,
    OllamaStatus,
    detect_machine,
)
from nova.setup.models import (
    normalize_model_name,
    ram_to_flags,
    recommended_models,
    missing_models,
)
from nova.setup.provision import (
    autoconfigure,
    ensure_config,
    provision_defaults,
)


def test_ram_to_flags():
    assert ram_to_flags(4, 0) == (False, False)
    assert ram_to_flags(8, 0) == (False, False)
    assert ram_to_flags(16, 0) == (True, True)
    # GPU with enough VRAM raises the ceiling even with modest RAM.
    assert ram_to_flags(8, 12) == (True, True)


def test_recommended_models_low_ram():
    recs = recommended_models(8, 0)
    by_role = {r.role: r.model for r in recs}
    assert by_role["embedding"] == "nomic-embed-text"
    assert by_role["local"] == "llama3.2:3b"
    assert by_role["small"] == "llama3.2:1b"
    assert "coding" not in by_role


def test_recommended_models_high_ram():
    recs = recommended_models(32, 8)
    by_role = {r.role: r.model for r in recs}
    assert by_role["local"] == "llama3.1:8b"
    assert by_role["small"] == "llama3.2:1b"
    assert by_role["coding"] == "qwen2.5-coder:7b"


def test_normalize_model_name():
    assert normalize_model_name("nomic-embed-text") == "nomic-embed-text"
    assert normalize_model_name("nomic-embed-text:latest") == "nomic-embed-text"
    assert normalize_model_name("llama3.1:8b") == "llama3.1:8b"


def test_missing_models_ignores_latest_tag():
    installed = ["qwen2.5-coder:7b", "nomic-embed-text:latest", "llama3.1:8b"]
    missing = missing_models(installed, 32, 8)
    assert "nomic-embed-text" not in missing
    assert "llama3.2:1b" in missing


def test_detect_machine_never_raises():
    m = detect_machine()
    assert isinstance(m, MachineProfile)
    assert m.ram_total_gb >= 0
    assert m.cpu_count >= 0


def test_provision_defaults_no_overwrite(tmp_path: Path):
    target = tmp_path / "config.yaml"
    # Pre-seed a user value that must be preserved.
    target.write_text("llm:\n  default_model: my-custom-model:7b\n", encoding="utf-8")
    report = autoconfigure(None, path=target, enable_plugins=True)
    data = target.read_text(encoding="utf-8")
    assert "my-custom-model:7b" in data
    # Sections the user didn't define are added.
    assert "permissions" in data
    assert report.models


def test_provision_creates_file(tmp_path: Path):
    target = tmp_path / "nested" / "config.yaml"
    report = autoconfigure(None, path=target, enable_plugins=True)
    assert report.created
    assert target.exists()


def test_provision_defaults_are_safe():
    data = provision_defaults(MachineProfile(ram_total_gb=32, cpu_count=8, gpu_vram_gb=8, gpu_available=True, os_name="x", python="3"))
    assert data["permissions"]["autonomy"] == "ask"
    assert "date_time" in data["permissions"]["allow"]
    # High-risk capabilities stay off / deny by default.
    assert data["web"]["enabled"] is False
    assert data["voice"]["enabled"] is False
    assert data["host"]["commands"] == []
    assert data["host"]["roots"] == []
    assert data["automation"]["enabled"] is False
    # Plugins are enabled but only the safe built-ins.
    assert data["plugins"]["enabled"] == ["text_tools", "units"]


def test_enable_flag_toggles(tmp_path: Path):
    target = tmp_path / "config.yaml"
    report = autoconfigure(None, path=target, enable_plugins=True, enable_voice=True, enable_web=True)
    data = target.read_text(encoding="utf-8")
    assert "enabled: true" in data  # voice + web flipped on


def test_ensure_config(tmp_path: Path):
    p, created = ensure_config(tmp_path / "c.yaml")
    assert created
    assert p.exists()


def test_autostart_unknown_platform(monkeypatch):
    monkeypatch.setattr("nova.setup.autostart.sys.platform", "plan9")
    from nova.setup.autostart import set_autostart

    with pytest.raises(AutostartError):
        set_autostart(True)

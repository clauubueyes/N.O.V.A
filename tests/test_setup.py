from __future__ import annotations

"""Tests for nova.setup: machine detection, model recommendation, safe
auto-provisioning and autostart. All hardware/Ollama interactions are faked so
the suite runs offline and on any machine."""

from pathlib import Path
import sys

import pytest
import yaml

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


def test_say_never_raises_without_pyttsx3(monkeypatch):
    """The JARVIS greeting must degrade silently when TTS is unavailable."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pyttsx3":
            raise ImportError("no tts")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from nova.setup.assistant import _say

    _say("Bienvenido, señor.")  # must not raise


def test_say_never_raises_when_tts_fails(monkeypatch):
    """Even a broken TTS engine must not block the wizard."""
    import types

    import nova.setup.assistant as A

    fake = types.ModuleType("pyttsx3")

    def init(*a, **k):
        raise RuntimeError("engine broke")

    fake.init = init
    monkeypatch.setitem(sys.modules, "pyttsx3", fake)
    A._say("hola")  # must not raise


def test_assistant_greeting_flag(monkeypatch, tmp_path: Path):
    """run_assistant with greeting declined sets greeted=False; with accept True."""
    import nova.setup.assistant as A

    calls: list[str] = []

    def ask(prompt, default=True):
        return True

    def say(text):
        calls.append(text)

    monkeypatch.setattr(A, "_ask", ask)
    monkeypatch.setattr(A, "_say", say)
    target = tmp_path / "config.yaml"
    result = A.run_assistant(interactive=True, config_path=target)
    assert result["greeted"] is True
    assert any("Bienvenido" in c for c in calls)


def test_cli_auto_no_models(tmp_path: Path, monkeypatch):
    """`nova-setup auto --config <tmp> --no-models` writes config without pulling."""
    import nova.setup.cli as C

    cfg = str(tmp_path / "config.yaml")
    calls: dict = {}

    def fake_pull(profile):
        calls["pulled"] = True
        return ["model-x"]

    monkeypatch.setattr(C, "detect_machine", lambda: MachineProfile(ram_total_gb=32, cpu_count=8, gpu_vram_gb=8, gpu_available=True, os_name="t", python="3"))
    monkeypatch.setattr(C, "detect_ollama", lambda: OllamaStatus(installed=True, running=True, models=[]))
    monkeypatch.setattr("nova.setup.assistant._pull_models", fake_pull)
    rc = C.main(["auto", "--config", cfg, "--no-models"])
    assert rc == 0
    assert "pulled" not in calls
    assert cfg and Path(cfg).exists()


def test_ensure_ollama_already_running(monkeypatch):
    """Fast path: when the endpoint answers, nothing is started."""
    import nova.setup.detect as D

    started = []

    def fake_start() -> bool:
        started.append(True)
        return True

    monkeypatch.setattr(D, "_url_reachable", lambda url, timeout=2.0: True)
    monkeypatch.setattr(D, "start_ollama", fake_start)
    st = D.ensure_ollama_running()
    assert st.running is True
    assert st.started_now is False
    assert started == []


def test_ensure_ollama_starts_when_down(monkeypatch):
    """When unreachable, ensure launches `ollama serve` and reports started_now."""
    import nova.setup.detect as D
    import types

    class FauxTime:
        def monotonic(self):
            return 0.0

        def sleep(self, _s):
            return None

    reach_calls = []

    def fake_reach(url, timeout=2.0):
        reach_calls.append(True)
        return len(reach_calls) >= 2

    def fake_start() -> bool:
        return True

    monkeypatch.setattr(D, "_url_reachable", fake_reach)
    monkeypatch.setattr(D, "start_ollama", fake_start)
    monkeypatch.setattr(D, "detect_ollama", lambda *a, **k: OllamaStatus(installed=True, running=True, models=["x"], message="serving"))
    monkeypatch.setattr(D, "time", FauxTime())
    st = D.ensure_ollama_running()
    assert st.running is True
    assert st.started_now is True
    assert len(reach_calls) >= 2


def test_ensure_ollama_not_installed(monkeypatch):
    """No binary: ensure returns the detection status without attempting to start."""
    import nova.setup.detect as D

    monkeypatch.setattr(D, "_url_reachable", lambda url, timeout=2.0: False)
    monkeypatch.setattr(D, "start_ollama", lambda: False)
    monkeypatch.setattr(D, "detect_ollama", lambda *a, **k: OllamaStatus(installed=False, message="no binary"))
    st = D.ensure_ollama_running()
    assert st.installed is False
    assert st.started_now is False


def test_cli_auto_voice_writes_config(tmp_path: Path, monkeypatch):
    """`nova setup auto --voice` flips voice.enabled on and fetches the Vosk model."""
    import nova.setup.cli as C

    target = tmp_path / "config.yaml"
    calls: list[str] = []

    monkeypatch.setattr(C, "detect_machine", lambda: MachineProfile(
        ram_total_gb=16, cpu_count=4, gpu_vram_gb=0.0, gpu_available=False, os_name="win", python="3"
    ))
    monkeypatch.setattr(C, "detect_ollama", lambda *a, **k: OllamaStatus(installed=True, running=True, models=["llama3.1:8b"], message="serving"))
    monkeypatch.setattr(C, "_ensure_vosk_model", lambda cfg: calls.append(cfg))
    monkeypatch.setattr("nova.setup.assistant._pull_models", lambda profile: calls.append("pulled"))

    rc = C.main(["auto", "--voice", "--config", str(target)])
    assert rc == 0
    assert "pulled" in calls
    assert str(target) in calls

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["voice"]["enabled"] is True

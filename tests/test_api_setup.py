from __future__ import annotations

"""Tests for the web installer endpoints (/v1/setup/*). Offline and hermetic:
machine detection, Ollama state and the TTS/autostart sides are all faked."""

from pathlib import Path

from fastapi.testclient import TestClient

from nova.api.app import create_app
from nova.core.config import load_settings
from nova.setup.detect import MachineProfile, OllamaStatus

FAKE_PROFILE = MachineProfile(
    ram_total_gb=32.0,
    cpu_count=8,
    gpu_vram_gb=8.0,
    gpu_available=True,
    os_name="Windows 10",
    python="3.11",
)


def _client(tmp_path, monkeypatch, models: list[str] | None = None) -> TestClient:
    from nova.api import setup as setup_api

    settings = load_settings()
    settings.memory.db_file = str(tmp_path / "memory.db")
    settings.audit.file = str(tmp_path / "audit.jsonl")
    settings.api.host = "127.0.0.1"

    monkeypatch.setattr(setup_api, "detect_machine", lambda: FAKE_PROFILE)
    monkeypatch.setattr(
        setup_api,
        "detect_ollama",
        lambda *a, **k: OllamaStatus(installed=True, running=True, models=models or ["llama3.1:8b"]),
    )
    return TestClient(create_app(settings))


def test_setup_status_shape(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        data = client.get("/v1/setup/status").json()
        assert data["machine"]["ram_gb"] == 32.0
        assert data["ollama"]["running"] is True
        assert data["default_model"]
        assert isinstance(data["missing_models"], list)
        assert isinstance(data["config"]["exists"], bool)


def test_setup_provision_writes_config(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        target = str(tmp_path / "new" / "config.yaml")
        response = client.post("/v1/setup/provision", json={"config_path": target, "plugins": True})
        assert response.status_code == 200
        body = response.json()
        assert body["config_path"] == target
        assert body["created"] is True
        assert Path(target).exists()


def test_setup_provision_never_overwrites_existing(tmp_path, monkeypatch) -> None:
    target = tmp_path / "existing.yaml"
    target.write_text("llm:\n  default_model: qwen2.5-coder:7b\n", encoding="utf-8")
    with _client(tmp_path, monkeypatch) as client:
        response = client.post("/v1/setup/provision", json={"config_path": str(target)})
        assert response.status_code == 200
        content = target.read_text(encoding="utf-8")
        assert "qwen2.5-coder:7b" in content  # user value preserved


def test_setup_autostart(tmp_path, monkeypatch) -> None:
    from nova.setup import autostart as autostart_mod

    monkeypatch.setattr(autostart_mod, "set_autostart", lambda enabled: None)
    monkeypatch.setattr(autostart_mod, "autostart_status", lambda: True)
    with _client(tmp_path, monkeypatch) as client:
        response = client.post("/v1/setup/autostart", json={"enable": True})
        assert response.status_code == 200
        assert response.json()["enabled"] is True


def test_setup_greeting(tmp_path, monkeypatch) -> None:
    from nova.setup import assistant as assistant_mod

    spoken = []
    monkeypatch.setattr(assistant_mod, "_say", lambda text: spoken.append(text))
    with _client(tmp_path, monkeypatch) as client:
        response = client.post("/v1/setup/greeting")
        assert response.status_code == 200
        assert response.json()["spoken"] is True
        assert spoken and "Bienvenido" in spoken[0]


def test_setup_guard_requires_token_when_remote(tmp_path, monkeypatch) -> None:
    from nova.api import setup as setup_api

    settings = load_settings()
    settings.memory.db_file = str(tmp_path / "memory.db")
    settings.audit.file = str(tmp_path / "audit.jsonl")
    settings.api.host = "0.0.0.0"  # exposed to the LAN, no token
    monkeypatch.setattr(setup_api, "detect_machine", lambda: FAKE_PROFILE)
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/setup/status")
        assert response.status_code == 403


def test_setup_pull_streams_progress(tmp_path, monkeypatch) -> None:
    from nova.api import setup as setup_api

    class FakePuller:
        def __init__(self, settings) -> None:
            pass

        def pull_model(self, name, progress=None):
            progress(123456, 1000000, "downloading")
            progress(0, 0, "verifying")
            return "success"

        def close(self) -> None:
            pass

    monkeypatch.setattr(setup_api, "OllamaProvider", FakePuller)
    with _client(tmp_path, monkeypatch) as client:
        response = client.get("/v1/setup/pull", params={"model": "llama3.2:1b"})
        assert response.status_code == 200
        body = response.text
        assert "downloading" in body
        assert '"ok": true' in body


def test_setup_pull_already_complete(tmp_path, monkeypatch) -> None:
    with _client(
        tmp_path,
        monkeypatch,
        models=["llama3.1:8b", "llama3.2:1b", "qwen2.5-coder:7b", "nomic-embed-text"],
    ) as client:
        response = client.get("/v1/setup/pull")
        assert response.status_code == 200
        assert "already complete" in response.text
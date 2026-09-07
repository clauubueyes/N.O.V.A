from __future__ import annotations

import os

from nova.core.config import load_settings


def _clean_env(monkeypatch) -> None:
    for var in list(os.environ):
        if var.startswith("NOVA_"):
            monkeypatch.delenv(var, raising=False)


def test_defaults_when_no_config_file(tmp_path, monkeypatch) -> None:
    _clean_env(monkeypatch)
    settings = load_settings(path=str(tmp_path / "missing.yaml"))
    assert settings.llm.provider == "ollama"
    assert settings.llm.base_url == "http://localhost:11434"
    assert settings.llm.default_model == "llama3.1:8b"
    assert settings.llm.embedding_model == "nomic-embed-text"
    assert settings.llm.temperature == 0.7
    assert settings.llm.timeout_s == 60.0
    assert settings.logging.level == "INFO"
    assert settings.session.max_history_messages == 20
    assert "N.O.V.A." in settings.session.system_prompt
    assert settings.memory.db_file == "memory/nova.db"
    assert settings.memory.session_id == "default"
    assert settings.memory.max_context == 3
    assert settings.memory.similarity_threshold == 0.3
    assert settings.api.host == "127.0.0.1"
    assert settings.api.port == 8000
    assert settings.model_router.min_ram_gb == 8.0
    assert settings.model_router.battery is True
    assert settings.model_router.cloud_enabled is False
    assert settings.llm.models == {}


def test_config_file_overrides_defaults(tmp_path, monkeypatch) -> None:
    _clean_env(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text(
        "llm:\n  default_model: qwen2.5-coder:7b\n  temperature: 0.2\n",
        encoding="utf-8",
    )
    settings = load_settings(path=str(config))
    assert settings.llm.default_model == "qwen2.5-coder:7b"
    assert settings.llm.temperature == 0.2
    assert settings.llm.provider == "ollama"


def test_models_catalog_and_router_from_config(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "llm:\n  models:\n    small: llama3.2:1b\n    coding: qwen2.5-coder:7b\n"
        "model_router:\n  min_ram_gb: 6.0\n  battery: false\n",
        encoding="utf-8",
    )
    settings = load_settings(path=str(config))
    assert settings.llm.models == {"small": "llama3.2:1b", "coding": "qwen2.5-coder:7b"}
    assert settings.model_router.min_ram_gb == 6.0
    assert settings.model_router.battery is False


def test_env_variable_overrides_config(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("llm:\n  default_model: qwen2.5-coder:7b\n", encoding="utf-8")
    monkeypatch.setenv("NOVA_LLM_DEFAULT_MODEL", "llama3.1:8b")
    monkeypatch.setenv("NOVA_LOGGING_LEVEL", "DEBUG")
    monkeypatch.setenv("NOVA_MEMORY_DB_FILE", "other.db")
    monkeypatch.setenv("NOVA_MEMORY_MAX_CONTEXT", "5")
    settings = load_settings(path=str(config))
    assert settings.llm.default_model == "llama3.1:8b"
    assert settings.logging.level == "DEBUG"
    assert settings.memory.db_file == "other.db"
    assert settings.memory.max_context == 5
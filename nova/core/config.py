from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

ENV_PREFIX = "NOVA_"


class AutonomyLevel(str, Enum):
    off = "off"
    ask = "ask"
    full = "full"


class LLMSettings(BaseSettings):
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    default_model: str = "llama3.1:8b"
    embedding_model: str = "nomic-embed-text"
    temperature: float = 0.7
    timeout_s: float = 60.0


class LoggingSettings(BaseSettings):
    level: str = "INFO"
    file: str = "logs/nova.log"


class SessionSettings(BaseSettings):
    max_history_messages: int = 20
    system_prompt: str = (
        "You are N.O.V.A. (Neural Operations & Virtual Assistant), a personal "
        "AI assistant running locally. Be concise, precise and helpful."
    )


class PermissionSettings(BaseSettings):
    autonomy: AutonomyLevel = AutonomyLevel.ask
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class AuditSettings(BaseSettings):
    file: str = "logs/audit.nova.jsonl"


class MemorySettings(BaseSettings):
    db_file: str = "memory/nova.db"
    session_id: str = "default"
    max_context: int = 3
    similarity_threshold: float = 0.3


class NovaSettings(BaseSettings):
    model_config = {
        "extra": "ignore",
    }

    llm: LLMSettings = LLMSettings()
    logging: LoggingSettings = LoggingSettings()
    session: SessionSettings = SessionSettings()
    permissions: PermissionSettings = PermissionSettings()
    audit: AuditSettings = AuditSettings()
    memory: MemorySettings = MemorySettings()


_ENV_OVERRIDES: dict[str, dict[str, str]] = {
    "llm": {
        "provider": "provider",
        "base_url": "base_url",
        "default_model": "default_model",
        "embedding_model": "embedding_model",
        "temperature": "temperature",
        "timeout_s": "timeout_s",
    },
    "logging": {
        "level": "level",
        "file": "file",
    },
    "session": {
        "max_history_messages": "max_history_messages",
        "system_prompt": "system_prompt",
    },
    "permissions": {
        "autonomy": "autonomy",
    },
    "audit": {
        "file": "file",
    },
    "memory": {
        "db_file": "db_file",
        "session_id": "session_id",
        "max_context": "max_context",
        "similarity_threshold": "similarity_threshold",
    },
}


def _apply_env(data: dict[str, Any], prefix: str = ENV_PREFIX) -> None:
    for section, fields in _ENV_OVERRIDES.items():
        for env_key, model_field in fields.items():
            value = os.environ.get(f"{prefix}{section.upper()}_{env_key.upper()}")
            if value is not None:
                data.setdefault(section, {})[model_field] = value


def load_settings(path: str = "config/config.yaml", prefix: str = ENV_PREFIX) -> NovaSettings:
    data: dict[str, Any] = {}
    config_path = Path(path)
    if config_path.exists():
        with open(config_path, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
            if isinstance(loaded, dict):
                data = loaded
    _apply_env(data, prefix)
    return NovaSettings(**data)


CONFIG_DOC = "config/config.yaml"
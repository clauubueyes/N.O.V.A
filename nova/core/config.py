from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
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
    # PHASE 7 — model catalog used by the ModelRouter. Each key is a role the
    # router fills with a concrete model name when the task matches it.
    models: dict[str, str] = Field(default_factory=dict)


class ModelRouterSettings(BaseSettings):
    """PHASE 7 — routing policy.

    `min_ram_gb` is the threshold below which heavy models are avoided when a
    lightweight alternative exists; `battery` controls whether to downshift when
    on battery. `cloud_enabled` (ADR-013) is always False unless explicitly
    configured by the user; N.O.V.A. never depends on a cloud model.
    """

    min_ram_gb: float = 8.0
    battery: bool = True
    cloud_enabled: bool = False


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


class APISettings(BaseSettings):
    """PHASE 8 — remote access policy for the REST API.

    - `token`: Bearer token required on every `/v1/*` route. Empty = no auth
      (only safe while the API is bound to 127.0.0.1 and never exposed).
    - `host_enabled`: expose the Desktop Agent host tools (open_app/open_url/run,
      files) through the API so a remote client can drive the host machine.
      REFUSES TO START without a token (hard safety guard).
    - `cors_origins`: origins allowed for a separately-hosted frontend
      (e.g. Vercel free hosting). "*" mirrors the default UX of a local UI.
    """

    host: str = "127.0.0.1"
    port: int = 8000
    token: str = ""
    host_enabled: bool = False
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


class HostSettings(BaseSettings):
    """PHASE 6 — host/desktop tools configuration.

    `run` only executes base commands listed in `commands` (allowlist; empty = nothing runs)
    and never elevation commands. `apps` maps an application name to a configured
    executable/path; the LLM only ever provides the name, never an arbitrary path.
    `roots` bounds every path that host tools may touch (working directories and
    file tools); empty = no filesystem access at all.
    """

    apps: dict[str, str] = Field(default_factory=dict)
    commands: list[str] = Field(default_factory=list)
    roots: list[str] = Field(default_factory=list)
    working_dir: str | None = None
    timeout_s: float = 30.0

    @field_validator("apps", "commands", "roots", mode="before")
    @classmethod
    def _empty_or_csv(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


class WebSettings(BaseSettings):
    """PHASE 9 — controlled web tools configuration.

    The LLM only reaches the Internet through these tools; it never gets a raw
    network primitive. `enabled: false` (default) registers nothing. When enabled,
    every request still has to pass the Permission System (denied by default).
    Respects robots.txt and a per-host rate limit by default.
    """

    enabled: bool = False
    user_agent: str = "NOVA/1.0 (N.O.V.A. local personal assistant)"
    timeout_s: float = 12.0
    max_redirects: int = 5
    max_bytes: int = 1_000_000
    max_chars: int = 4000
    respect_robots: bool = True
    min_delay_s: float = 1.0
    search_url: str = "https://html.duckduckgo.com/html/?q={query}"
    search_max: int = 5


class NovaSettings(BaseSettings):
    model_config = {
        "extra": "ignore",
    }

    llm: LLMSettings = LLMSettings()
    model_router: ModelRouterSettings = ModelRouterSettings()
    logging: LoggingSettings = LoggingSettings()
    session: SessionSettings = SessionSettings()
    permissions: PermissionSettings = PermissionSettings()
    audit: AuditSettings = AuditSettings()
    memory: MemorySettings = MemorySettings()
    api: APISettings = APISettings()
    host: HostSettings = HostSettings()
    web: WebSettings = WebSettings()


_ENV_OVERRIDES: dict[str, dict[str, str]] = {
    "llm": {
        "provider": "provider",
        "base_url": "base_url",
        "default_model": "default_model",
        "embedding_model": "embedding_model",
        "temperature": "temperature",
        "timeout_s": "timeout_s",
    },
    "model_router": {
        "min_ram_gb": "min_ram_gb",
        "battery": "battery",
        "cloud_enabled": "cloud_enabled",
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
    "api": {
        "host": "host",
        "port": "port",
        "token": "token",
        "host_enabled": "host_enabled",
        "cors_origins": "cors_origins",
    },
    "host": {
        "apps": "apps",
        "commands": "commands",
        "roots": "roots",
        "working_dir": "working_dir",
        "timeout_s": "timeout_s",
    },
    "web": {
        "enabled": "enabled",
        "user_agent": "user_agent",
        "timeout_s": "timeout_s",
        "max_redirects": "max_redirects",
        "max_bytes": "max_bytes",
        "max_chars": "max_chars",
        "respect_robots": "respect_robots",
        "min_delay_s": "min_delay_s",
        "search_url": "search_url",
        "search_max": "search_max",
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
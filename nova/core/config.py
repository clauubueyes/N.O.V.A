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


class AIMode(str, Enum):
    """PHASE 14 — AI operating mode.

    - local: Ollama only, no cloud dependency.
    - hybrid: Ollama first, cloud fallback when needed.
    """

    local = "local"
    hybrid = "hybrid"


class PrivacyPolicy(str, Enum):
    """PHASE 14 — data routing privacy policy.

    - local_only: ALL tasks stay on Ollama. Cloud is never used.
    - cloud_allowed: Local-first, cloud fallback permitted.
    """

    local_only = "local_only"
    cloud_allowed = "cloud_allowed"


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


class OpenCodeProviderSettings(BaseSettings):
    """PHASE 14 — OpenCode cloud provider configuration.

    When `ai.mode` is hybrid, OpenCode provides cloud fallback for tasks
    that exceed local model capacity. OpenCode runs its own HTTP server
    (default port 4096); N.O.V.A. talks to it over HTTP just like Ollama.
    """

    enabled: bool = False
    base_url: str = "http://127.0.0.1:4096"
    timeout_s: float = 120.0
    default_model: str = ""
    # Cloud model catalog keyed by task role (coding, reasoning, etc.)
    models: dict[str, str] = Field(default_factory=dict)


class ModelRouterSettings(BaseSettings):
    """PHASE 7 + 14 — routing policy.

    `min_ram_gb` is the threshold below which heavy models are avoided when a
    lightweight alternative exists; `battery` controls whether to downshift when
    on battery. `cloud_enabled` (ADR-013) is always False unless explicitly
    configured by the user; N.O.V.A. never depends on a cloud model.

    PHASE 14: `strategy` controls hybrid routing behaviour.
    """

    min_ram_gb: float = 8.0
    battery: bool = True
    cloud_enabled: bool = False
    strategy: str = "local-first"


class AISettings(BaseSettings):
    """PHASE 14 — top-level AI mode and privacy policy.

    - `mode`: local (Ollama only) or hybrid (Ollama + cloud fallback).
    - `privacy`: local_only (never cloud) or cloud_allowed (local-first, cloud fallback).
    """

    mode: AIMode = AIMode.local
    privacy: PrivacyPolicy = PrivacyPolicy.local_only


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


class VoiceSTTSettings(BaseSettings):
    """PHASE 10 — speech-to-text backend. OSS/local: `vosk` (offline)."""

    backend: str = "vosk"
    model_dir: str | None = None
    language: str = "es"


class VoiceTTSSettings(BaseSettings):
    """PHASE 10 — text-to-speech backend. OSS/local: `pyttsx3` (OS voices)."""

    backend: str = "pyttsx3"
    voice: str | None = None
    rate: int = 180


class PluginSettings(BaseSettings):
    """PHASE 11 — plugins configuration.

    `enabled` lists the built-in plugins to activate by name (each provides
    tools that still go through the Permission System + audit). `dir` (optional)
    points to a folder whose `*_plugin.py` modules expose a `PLUGIN` object to
    load custom plugins. Empty `enabled` + no `dir` = no plugins registered.
    """

    enabled: list[str] = Field(default_factory=list)
    dir: str | None = None

    @field_validator("enabled", mode="before")
    @classmethod
    def _split_enabled(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


class AutomationScheduleSettings(BaseModel):
    """PHASE 12 — when an automation task runs.

    Either `interval_s` (every N seconds) or `at` (a local time "HH:MM" once a
    day). `interval_s` wins when both are set. An empty schedule never fires.
    """

    interval_s: float = 0.0
    at: str = ""


class AutomationTaskSettings(BaseModel):
    """PHASE 12 — a scheduled task: a moment in time plus a single action.

    The action is either a `tool` (+`args`), an `agent` (+`text` prompt) or a
    `workflow` by name. Executed by the Scheduler through the same ToolRunner /
    Permission System / audit as every other capability.
    """

    name: str
    description: str = ""
    enabled: bool = True
    schedule: AutomationScheduleSettings = AutomationScheduleSettings()
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    agent: str | None = None
    text: str = ""
    workflow: str | None = None


class AutomationStepSettings(BaseModel):
    """PHASE 12 — one step of a workflow: a tool call, an agent turn or a run.

    `on_error` is "stop" (default) or "continue" when the step fails/denied.
    """

    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    agent: str | None = None
    text: str = ""
    workflow: str | None = None
    on_error: str = "stop"


class AutomationWorkflowSettings(BaseModel):
    """PHASE 12 — a named, ordered multi-step workflow.

    Every tool call inside goes through the Permission System (denied by default
    in automation) and every step is audited; a failed step stops the workflow by
    default (`on_error: stop` per step) or continues if `continue`.
    """

    name: str
    description: str = ""
    enabled: bool = True
    steps: list[AutomationStepSettings] = Field(default_factory=list)


class AutomationSettings(BaseSettings):
    """PHASE 12 — scheduler + workflows.

    `enabled: false` (default) turns everything off. When enabled, `tasks` run on
    their schedule and `workflows` can be triggered manually (CLI/API) or from a
    scheduled task. Automation tools follow `permissions` (an `ask` is denied in
    automation: there is no human in the loop).
    """

    enabled: bool = False
    poll_s: float = 1.0
    tasks: list[AutomationTaskSettings] = Field(default_factory=list)
    workflows: list[AutomationWorkflowSettings] = Field(default_factory=list)


class VoiceSettings(BaseSettings):
    """PHASE 10 — local voice pipeline (STT -> chat -> TTS).

    Off by default. Never depends on a paid/cloud API: audio is captured and
    recognized on-device (Vosk) and answers are spoken with local OS voices
    (pyttsx3). `wake_word` (optional) makes the loop only react when a message
    starts with that word (matched case-insensitively from the transcript).
    """

    enabled: bool = False
    stt: VoiceSTTSettings = VoiceSTTSettings()
    tts: VoiceTTSSettings = VoiceTTSSettings()
    wake_word: str | None = None
    device: str | None = None


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
    voice: VoiceSettings = VoiceSettings()
    plugins: PluginSettings = PluginSettings()
    automation: AutomationSettings = AutomationSettings()
    # PHASE 14 — hybrid mode settings
    ai: AISettings = AISettings()
    open_code: OpenCodeProviderSettings = OpenCodeProviderSettings()


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
        "strategy": "strategy",
    },
    "ai": {
        "mode": "mode",
        "privacy": "privacy",
    },
    "open_code": {
        "enabled": "enabled",
        "base_url": "base_url",
        "timeout_s": "timeout_s",
        "default_model": "default_model",
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
    "voice": {
        "enabled": "enabled",
        "wake_word": "wake_word",
        "device": "device",
        "stt_backend": "stt.backend",
        "tts_backend": "tts.backend",
    },
    "plugins": {
        "enabled": "enabled",
        "dir": "dir",
    },
    "automation": {
        "enabled": "enabled",
        "poll_s": "poll_s",
    },
}


def _apply_env(data: dict[str, Any], prefix: str = ENV_PREFIX) -> None:
    for section, fields in _ENV_OVERRIDES.items():
        for env_key, model_field in fields.items():
            value = os.environ.get(f"{prefix}{section.upper()}_{env_key.upper()}")
            if value is None:
                continue
            target = data.setdefault(section, {})
            parts = model_field.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value


def load_settings(path: str | None = None, prefix: str = ENV_PREFIX) -> NovaSettings:
    from nova.core.paths import default_config_path

    data: dict[str, Any] = {}
    config_path = Path(path) if path is not None else default_config_path()
    if config_path.exists():
        with open(config_path, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
            if isinstance(loaded, dict):
                data = loaded
    _apply_env(data, prefix)
    return NovaSettings(**data)


CONFIG_DOC = "config/config.yaml"

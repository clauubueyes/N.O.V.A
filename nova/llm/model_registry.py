from __future__ import annotations

"""PHASE 5.2 — Model Registry with declarative capabilities.

The router needs to know *what a model can do* (vision, embeddings, context
window) to pick one safely. N.O.V.A. reads that from two sources:

  1. Ollama ``/api/show``  -> declared capabilities + context_length (preferred)
  2. Well-known name heuristics -> offline fallback when Ollama is unreachable

Readings are cached per model with a TTL so the router never triggers an API
call per message. Everything is best-effort: unknown models degrade to
"text only, no context bound known".
"""

import time
from dataclasses import dataclass, field
from typing import Iterable

_VISION_FAMILIES = {
    "llava",
    "bakllava",
    "moondream",
    "nano-llava",
    "minicpm-v",
    "qwen2-vl",
    "qwen2vl",
    "qwen2.5-vl",
    "qwen2.5vl",
    "llama3.2-vision",
    "granite3.2-vision",
    "minicpm-v",
}
# Match these tokens anywhere in the model name to flag vision capability.
_VISION_TOKENS = ("llava", "moondream", "bakllava", "-vl", "-vision", "minicpm-v")
_EMBEDDING_TOKENS = (
    "embed",
    "bge-",
    "bge_",
    "snowflake-arctic-embed",
    "mxbai-embed",
    "granite-embedding",
    "nomic-embed",
    "all-minilm",
)
_KNOWN_CONTEXT_BY_FAMILY = {
    "llama3.1": 131072,
    "llama3.2": 131072,
    "llama3.3": 131072,
    "qwen2": 32768,
    "qwen2.5": 32768,
    "gemma2": 8192,
    "mistral": 32768,
    "phi3": 128000,
    "deepseek-r1": 65536,
}


@dataclass(frozen=True)
class ModelCapabilities:
    """What a single model can do, as known to N.O.V.A."""

    vision: bool = False
    embedding: bool = False
    context_length: int | None = None
    family: str = ""
    parameter_size: str = ""
    quantization: str = ""
    source: str = "heuristic"  # "heuristic" | "ollama:/api/show"

    @classmethod
    def from_show(cls, show_info: dict, *, installed_name: str = "") -> "ModelCapabilities":
        caps = set(show_info.get("capabilities", []) or [])
        return cls(
            vision="vision" in caps,
            embedding="embedding" in caps or bool(show_info.get("dimensions")),
            context_length=show_info.get("context_length"),
            family=str(show_info.get("family") or ""),
            parameter_size=str(show_info.get("parameter_size") or ""),
            quantization=str(show_info.get("quantization") or ""),
            source="ollama:/api/show",
        )

    @classmethod
    def from_name(cls, name: str) -> "ModelCapabilities":
        lowered = name.lower()
        base = lowered.split(":")[0]
        vision = any(token in base for token in _VISION_TOKENS)
        embedding = any(token in lowered for token in _EMBEDDING_TOKENS)
        context_length = _KNOWN_CONTEXT_BY_FAMILY.get(base)
        return cls(vision=vision, embedding=embedding, context_length=context_length, family=base)


class ModelRegistry:
    """Cached knowledge about available models and their capabilities.

    ``refresh(provider)`` reads installed models and their ``/api/show`` metadata
    (throttled + cached). ``note()`` lets callers drop in extra facts (e.g. the
    embedding model configured in ``llm.embedding_model``). Lookups never raise.
    """

    _TTL_S = 3600.0

    def __init__(self) -> None:
        self._info: dict[str, ModelCapabilities] = {}
        self._stamp: dict[str, float] = {}
        self._installed: list[str] = []
        self._installed_stamp = 0.0

    # -- data -------------------------------------------------------------
    def note(self, name: str, caps: ModelCapabilities | None = None) -> None:
        """Record (or refresh) the capabilities known for a model."""
        if caps is None:
            caps = ModelCapabilities.from_name(name)
        self._info[name] = caps
        self._stamp[name] = time.time()

    def refresh(
        self,
        provider,
        installed: Iterable[str] | None = None,
        *,
        force: bool = False,
    ) -> list[str]:
        """Re-read the installed model list and enrich capabilities.

        ``installed`` may be given explicitly; otherwise it comes from
        ``provider.list_models()`` when possible. Returns the list of model names
        known after the refresh.
        """
        now = time.time()
        if installed is None and provider is not None:
            try:
                installed = [info.name for info in provider.list_models()]
            except Exception:  # noqa: BLE001 - registry must never raise
                installed = []
        names = list(dict.fromkeys(installed))
        if names:
            self._installed = names
            self._installed_stamp = now

        show_info = getattr(provider, "show_info", None)
        for name in names:
            if name in self._info and name in self._stamp and not force:
                if now - self._stamp[name] < self._TTL_S:
                    continue
            if show_info is not None:
                try:
                    details = show_info(name)
                except Exception:  # noqa: BLE001
                    details = None
                if details:
                    self.note(name, ModelCapabilities.from_show(details))
                    continue
            self.note(name, ModelCapabilities.from_name(name))
        return list(self._installed)

    # -- lookups ------------------------------------------------------------
    def installed(self) -> list[str]:
        return list(self._installed)

    def capabilities(self, name: str) -> ModelCapabilities:
        if name in self._info:
            return self._info[name]
        caps = ModelCapabilities.from_name(name)
        self._info[name] = caps
        self._stamp[name] = time.time()
        return caps

    def has(self, name: str, capability: str) -> bool:
        caps = self.capabilities(name)
        if capability == "vision":
            return caps.vision
        if capability in ("embedding", "embed"):
            return caps.embedding
        return False

    def context_length(self, name: str) -> int | None:
        if name in self._info and self._info[name].context_length is not None:
            return self._info[name].context_length
        return ModelCapabilities.from_name(name).context_length

    def vision_models(self) -> list[str]:
        return [name for name in self.installed() if self.has(name, "vision")]

    def embedding_models(self) -> list[str]:
        return [name for name in self.installed() if self.has(name, "embedding")]

    def first_matching(self, capability: str) -> str | None:
        """Return the first installed model that has ``capability``."""
        for name in self.installed():
            if self.has(name, capability):
                return name
        return None

    def pick_default_embedding(self, configured: str | None) -> str:
        if configured:
            return configured
        return self.first_matching("embedding") or ""


def build_model_registry(provider) -> ModelRegistry:
    """Create a registry enriched from the configured provider (best-effort)."""
    registry = ModelRegistry()
    try:
        registry.refresh(provider)
    except Exception:  # noqa: BLE001
        pass
    return registry
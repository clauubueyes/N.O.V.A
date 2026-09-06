from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence


class NOVAProviderError(Exception):
    """Raised when an LLM provider request fails or returns an invalid shape."""


@dataclass
class ChatMessage:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class ChatCompletionRequest:
    messages: list[ChatMessage]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass
class ChatCompletionResponse:
    message: ChatMessage
    model: str
    usage: dict[str, Any] | None = None


@dataclass
class ModelInfo:
    name: str
    size: int = 0
    modified_at: str = ""


class LLMProvider(ABC):
    """A chat/embedding provider. All internal code depends only on this abstraction."""

    #: Whether this provider implements embedding (`embed_text`).
    supports_embedding: bool = False

    @abstractmethod
    def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Send a chat completion request."""

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """List models available in the provider."""

    @abstractmethod
    def health(self) -> bool:
        """Return True when the provider is reachable."""

    def embed_text(self, texts: str | Sequence[str]) -> list[list[float]]:
        """Embed one or many texts and return one vector per input.

        Raises `NOVAProviderError` when the provider does not support embeddings.
        """
        raise NOVAProviderError(f"{type(self).__name__} does not support embeddings")

    def close(self) -> None:
        """Release provider resources. May be overridden."""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProviderErrorCode(str, Enum):
    unavailable = "unavailable"
    authentication = "authentication"
    rate_limited = "rate_limited"
    timeout = "timeout"
    invalid_request = "invalid_request"
    invalid_response = "invalid_response"
    cancelled = "cancelled"


class NOVAProviderError(Exception):
    """Provider-independent inference error safe to expose to Core callers."""

    def __init__(
        self,
        message: str,
        *,
        code: ProviderErrorCode = ProviderErrorCode.unavailable,
        provider: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.retryable = retryable


@dataclass(frozen=True)
class ProviderCapabilities:
    chat: bool = True
    streaming: bool = False
    model_listing: bool = False
    embeddings: bool = False
    tool_calling: bool = False
    structured_output: bool = False
    vision: bool = False
    cancellation: bool = True


@dataclass
class StreamCancellation:
    """Shared flag a streaming loop checks between chunks.

    Callers create it, hand it to ``stream()``, and call ``cancel()`` from any
    thread (e.g. a `POST /v1/sessions/{id}/stop` endpoint) to stop generation.
    """

    _stopped: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self._stopped.set()

    @property
    def cancelled(self) -> bool:
        return self._stopped.is_set()

    @classmethod
    def fresh(cls) -> StreamCancellation:
        return cls()


@dataclass
class ChatMessage:
    role: str
    content: str
    images: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        if self.images:
            return {"role": self.role, "content": [
                {"type": "text", "text": self.content},
                *[{"type": "image_url", "image_url": {"url": url}} for url in self.images],
            ]}
        return {"role": self.role, "content": self.content}


@dataclass
class ChatCompletionRequest:
    messages: list[ChatMessage]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    response_format: dict[str, Any] | None = None


@dataclass
class ChatCompletionResponse:
    message: ChatMessage
    model: str
    usage: dict[str, Any] | None = None
    provider: str = ""


@dataclass
class ModelInfo:
    name: str
    size: int = 0
    modified_at: str = ""
    context_window: int | None = None
    capabilities: frozenset[str] = frozenset()


class LLMProvider(ABC):
    """A chat/embedding provider. All internal code depends only on this abstraction."""

    #: Whether this provider implements embedding (`embed_text`).
    supports_embedding: bool = False
    provider_name: str = "unknown"
    location: str = "cloud"

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

    def stream(
        self,
        request: ChatCompletionRequest,
        cancellation: StreamCancellation | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream content deltas. Yields ``{"content": str}`` frames and finally
        ``{"usage": {...}}`` when the provider reports it. Falls back to a single
        chunk for providers without a streaming endpoint."""
        response = self.chat(request)
        if cancellation is not None and cancellation.cancelled:
            return
        yield {"content": response.message.content}
        if response.usage:
            yield {"usage": response.usage}

    def close(self) -> None:
        """Release provider resources. May be overridden."""

    def supports_images(self, model: str) -> bool:
        return False

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True,
            model_listing=True,
            embeddings=self.supports_embedding,
        )

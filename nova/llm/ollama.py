from __future__ import annotations

from typing import Sequence

import httpx

from nova.core.config import LLMSettings
from nova.core.logging import get_logger
from nova.llm.base import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    LLMProvider,
    ModelInfo,
    NOVAProviderError,
)

logger = get_logger("llm.ollama")
_CHAT_URL = "/v1/chat/completions"
_TAGS_URL = "/api/tags"
_EMBED_URL = "/api/embed"


class OllamaProvider(LLMProvider):
    supports_embedding = True

    def __init__(self, settings: LLMSettings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout_s,
        )

    def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        payload = {
            "model": request.model or self._settings.default_model,
            "messages": [message.to_dict() for message in request.messages],
            "temperature": (
                request.temperature
                if request.temperature is not None
                else self._settings.temperature
            ),
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens

        try:
            response = self._client.post(_CHAT_URL, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise NOVAProviderError(
                f"Ollama returned HTTP {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise NOVAProviderError(
                f"Could not reach Ollama at {self._settings.base_url}: {exc}"
            ) from exc

        data = response.json()
        try:
            choice = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise NOVAProviderError(f"Unexpected Ollama response shape: {data}") from exc

        return ChatCompletionResponse(
            message=ChatMessage(
                role=choice.get("role", "assistant"),
                content=choice.get("content", ""),
            ),
            model=data.get("model", payload["model"]),
            usage=data.get("usage"),
        )

    def list_models(self) -> list[ModelInfo]:
        try:
            response = self._client.get(_TAGS_URL)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise NOVAProviderError(
                f"Ollama returned HTTP {exc.response.status_code} listing models"
            ) from exc
        except httpx.RequestError as exc:
            raise NOVAProviderError(
                f"Could not reach Ollama at {self._settings.base_url}: {exc}"
            ) from exc

        data = response.json()
        return [
            ModelInfo(
                name=item.get("name", "?"),
                size=item.get("size", 0),
                modified_at=item.get("modified_at", ""),
            )
            for item in data.get("models", [])
        ]

    def health(self) -> bool:
        try:
            response = self._client.get("/")
            response.raise_for_status()
            return True
        except (httpx.HTTPStatusError, httpx.RequestError):
            return False

    def embed_text(self, texts: str | Sequence[str]) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        payload = {
            "model": self._settings.embedding_model,
            "input": list(texts),
        }
        try:
            response = self._client.post(_EMBED_URL, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise NOVAProviderError(
                f"Ollama returned HTTP {exc.response.status_code} embedding: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise NOVAProviderError(
                f"Could not reach Ollama at {self._settings.base_url}: {exc}"
            ) from exc

        data = response.json()
        try:
            embeddings = data["embeddings"]
        except (KeyError, TypeError) as exc:
            raise NOVAProviderError(f"Unexpected Ollama embedding response shape: {data}") from exc
        return [list(embedding) for embedding in embeddings]

    def close(self) -> None:
        self._client.close()
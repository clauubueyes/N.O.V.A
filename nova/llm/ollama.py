from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from typing import Any

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
    StreamCancellation,
)

logger = get_logger("llm.ollama")
_CHAT_URL = "/v1/chat/completions"
_TAGS_URL = "/api/tags"
_EMBED_URL = "/api/embed"
_PULL_URL = "/api/pull"

#: Progress callback signature: (bytes_read, total_bytes_or_None, status_str)
PullProgress = Callable[[int, int | None, str], None]


class OllamaProvider(LLMProvider):
    supports_embedding = True
    provider_name = "ollama"
    location = "local"

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
            provider="ollama",
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

    def pull_model(
        self,
        name: str,
        progress: PullProgress | None = None,
        timeout: float | None = None,
    ) -> str:
        """Download a model with `POST /api/pull`, reporting progress.

        The Ollama pull endpoint streams newline-delimited JSON objects:
        `{"status": "pulling manifest", ...}`,
        `{"status": "downloading", "digest": ..., "total": N, "completed": M}`, and
        a final `{"status": "success"}`. Each completed/status frame is passed to the
        optional `progress(completed, total, status)` callback. Returns the final
        status string.
        """
        try:
            with self._client.stream(
                "POST", _PULL_URL, json={"name": name, "stream": True}, timeout=timeout
            ) as response:
                response.raise_for_status()
                final_status = "unknown"
                seen: dict[str, tuple[int, int | None]] = {}
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    status = frame.get("status", "")
                    if status == "success":
                        final_status = status
                        if progress:
                            progress(0, 0, status)
                        continue
                    digest = frame.get("digest", "")
                    completed = int(frame.get("completed", 0) or 0)
                    total = frame.get("total")
                    total = int(total) if total else None
                    if digest:
                        # Report the summed progress of all active layer downloads.
                        seen[digest] = (completed, total)
                        if progress:
                            done = sum(c for c, _ in seen.values())
                            tot = sum(t for _, t in seen.values() if t is not None) or None
                            progress(done, tot, status)
                    elif progress:
                        progress(0, 0, status)
                return final_status
        except httpx.HTTPStatusError as exc:
            raise NOVAProviderError(
                f"Ollama returned HTTP {exc.response.status_code} pulling '{name}': {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise NOVAProviderError(
                f"Could not reach Ollama at {self._settings.base_url} pulling '{name}': {exc}"
            ) from exc

    def has_model(self, name: str) -> bool:
        """Return True when a model (or digest) is already present locally."""
        return any(m.name == name for m in self.list_models())

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

    def supports_images(self, model: str) -> bool:
        try:
            response = self._client.post('/api/show', json={'model': model})
            response.raise_for_status()
            return 'vision' in response.json().get('capabilities', [])
        except (httpx.HTTPError, ValueError):
            return False

    def stream(
        self,
        request: ChatCompletionRequest,
        cancellation: StreamCancellation | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream content deltas from Ollama's OpenAI-compatible SSE endpoint.

        Yields ``{"content": str}`` frames (empty ``{"content": ""}`` deltas are
        skipped) and a final ``{"usage": {...}}`` frame when provided. Stops
        early once ``cancellation.cancelled`` is set.
        """
        payload = {
            "model": request.model or self._settings.default_model,
            "messages": [message.to_dict() for message in request.messages],
            "temperature": (
                request.temperature
                if request.temperature is not None
                else self._settings.temperature
            ),
            "stream": True,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        try:
            with self._client.stream("POST", _CHAT_URL, json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if cancellation is not None and cancellation.cancelled:
                        break
                    if not line:
                        continue
                    line = line.removeprefix("data: ")
                    if line.strip() == "[DONE]":
                        break
                    try:
                        frame = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    choice = (frame.get("choices") or [{}])[0]
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        yield {"content": delta}
                    if frame.get("usage"):
                        yield {"usage": frame["usage"]}
        except httpx.HTTPStatusError as exc:
            raise NOVAProviderError(
                f"Ollama returned HTTP {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise NOVAProviderError(
                f"Could not reach Ollama at {self._settings.base_url}: {exc}"
            ) from exc

    def show_info(self, model: str) -> dict:
        """Return the `/api/show` metadata for a model (best-effort).

        Gives the ModelRegistry the declared capabilities (``vision``/``tools``),
        the context window and the parameter family so it can make routing
        decisions without hardcoded name lists.
        """
        try:
            response = self._client.post('/api/show', json={'model': model})
            response.raise_for_status()
        except httpx.HTTPError:
            return {}
        data = response.json()
        details = data.get("details", {})
        model_info = data.get("model_info", {})
        context_length = None
        for key in (
            "llama.context_length",
            "qwen2.context_length",
            "gemma2.context_length",
            "phi3.context_length",
            "mistral.context_length",
        ):
            if key in model_info:
                context_length = model_info[key]
                break
        params = data.get("parameters", "")
        return {
            "capabilities": list(data.get("capabilities", []) or []),
            "context_length": context_length,
            "family": details.get("family") or "",
            "parameter_size": details.get("parameter_size") or "",
            "quantization": details.get("quantization_level") or "",
            "parameters": params,
        }

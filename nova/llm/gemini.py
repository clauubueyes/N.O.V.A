from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from nova.llm.base import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    LLMProvider,
    ModelInfo,
    NOVAProviderError,
    ProviderCapabilities,
    ProviderErrorCode,
    StreamCancellation,
)


class GeminiProvider(LLMProvider):
    provider_name = "gemini"
    location = "cloud"

    def __init__(self, settings: Any, client: httpx.Client | None = None) -> None:
        self._settings = settings
        api_key = settings.resolve_api_key()
        headers = {"x-goog-api-key": api_key} if api_key else {}
        self._client = client or httpx.Client(base_url=settings.base_url.rstrip("/") + "/", headers=headers, timeout=settings.timeout_s)
        if api_key:
            self._client.headers["x-goog-api-key"] = api_key

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, model_listing=True, tool_calling=True, structured_output=True, vision=True)

    def _body(self, request: ChatCompletionRequest) -> dict[str, Any]:
        contents: list[dict[str, Any]] = []
        system: list[str] = []
        for message in request.messages:
            if message.role == "system":
                system.append(message.content)
                continue
            parts: list[dict[str, Any]] = [{"text": message.content}]
            for image in message.images:
                if image.startswith("data:") and "," in image:
                    meta, data = image.split(",", 1)
                    parts.append({"inline_data": {"mime_type": meta[5:].split(";")[0], "data": data}})
            contents.append({"role": "model" if message.role == "assistant" else "user", "parts": parts})
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["system_instruction"] = {"parts": [{"text": "\n".join(system)}]}
        generation: dict[str, Any] = {"temperature": request.temperature if request.temperature is not None else self._settings.temperature}
        if request.max_tokens is not None:
            generation["maxOutputTokens"] = request.max_tokens
        if request.response_format:
            generation["responseMimeType"] = "application/json"
        body["generationConfig"] = generation
        if request.tools:
            body["tools"] = [{"functionDeclarations": request.tools}]
        return body

    def _model(self, request: ChatCompletionRequest) -> str:
        return (request.model or self._settings.default_model).removeprefix("models/")

    def _raise(self, exc: httpx.HTTPError) -> None:
        if isinstance(exc, httpx.TimeoutException):
            code, retryable = ProviderErrorCode.timeout, True
        elif isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
            code, retryable = ProviderErrorCode.authentication, False
        elif isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
            code, retryable = ProviderErrorCode.rate_limited, True
        else:
            code, retryable = ProviderErrorCode.unavailable, True
        raise NOVAProviderError("Gemini request failed", code=code, provider="gemini", retryable=retryable) from exc

    @staticmethod
    def _text(data: dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        return "".join(part.get("text", "") for part in candidates[0].get("content", {}).get("parts", []) if isinstance(part, dict))

    def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        model = self._model(request)
        try:
            response = self._client.post(f"models/{model}:generateContent", json=self._body(request))
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            self._raise(exc)
        text = self._text(data)
        if not text:
            raise NOVAProviderError("Gemini returned no text", code=ProviderErrorCode.invalid_response, provider="gemini")
        metadata = data.get("usageMetadata")
        return ChatCompletionResponse(ChatMessage("assistant", text), model, metadata, "gemini")

    def stream(self, request: ChatCompletionRequest, cancellation: StreamCancellation | None = None) -> Iterator[dict[str, Any]]:
        model = self._model(request)
        try:
            with self._client.stream("POST", f"models/{model}:streamGenerateContent", params={"alt": "sse"}, json=self._body(request)) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if cancellation is not None and cancellation.cancelled:
                        break
                    if not line or not line.startswith("data: "):
                        continue
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    text = self._text(data)
                    if text:
                        yield {"content": text}
                    if data.get("usageMetadata"):
                        yield {"usage": data["usageMetadata"]}
        except httpx.HTTPError as exc:
            self._raise(exc)

    def list_models(self) -> list[ModelInfo]:
        try:
            response = self._client.get("models")
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            self._raise(exc)
        return [
            ModelInfo(
                name=str(item.get("name", "")).removeprefix("models/"),
                context_window=item.get("inputTokenLimit"),
                capabilities=frozenset(item.get("supportedGenerationMethods", [])),
            )
            for item in data.get("models", []) if item.get("name")
        ]

    def health(self) -> bool:
        try:
            return self._client.get("models").status_code < 500
        except httpx.HTTPError:
            return False

    def supports_images(self, model: str) -> bool:
        return True

    def close(self) -> None:
        self._client.close()

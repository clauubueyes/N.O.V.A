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


class OpenAICompatibleProvider(LLMProvider):
    """Reusable adapter for the documented OpenAI-compatible HTTP surface."""

    provider_name = "openai_compatible"

    def __init__(self, settings: Any, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self.provider_name = getattr(settings, "name", "") or self.provider_name
        self.location = getattr(settings, "location", "cloud")
        api_key = settings.resolve_api_key()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(
            base_url=settings.base_url.rstrip("/") + "/",
            headers=headers,
            timeout=settings.timeout_s,
        )
        if api_key:
            self._client.headers["Authorization"] = f"Bearer {api_key}"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True,
            model_listing=True,
            tool_calling=True,
            structured_output=True,
            vision=True,
        )

    def _payload(self, request: ChatCompletionRequest, *, stream: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model or self._settings.default_model,
            "messages": [message.to_dict() for message in request.messages],
            "temperature": request.temperature if request.temperature is not None else self._settings.temperature,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = request.tools
        if request.response_format:
            payload["response_format"] = request.response_format
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _error(self, exc: Exception) -> NOVAProviderError:
        if isinstance(exc, httpx.TimeoutException):
            return NOVAProviderError(
                f"{self.provider_name} request timed out",
                code=ProviderErrorCode.timeout,
                provider=self.provider_name,
                retryable=True,
            )
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            code = ProviderErrorCode.invalid_request
            retryable = status >= 500
            if status in (401, 403):
                code = ProviderErrorCode.authentication
            elif status == 429:
                code, retryable = ProviderErrorCode.rate_limited, True
            elif status >= 500:
                code = ProviderErrorCode.unavailable
            return NOVAProviderError(
                f"{self.provider_name} returned HTTP {status}",
                code=code,
                provider=self.provider_name,
                retryable=retryable,
            )
        return NOVAProviderError(
            f"Could not reach {self.provider_name}: {exc}",
            code=ProviderErrorCode.unavailable,
            provider=self.provider_name,
            retryable=True,
        )

    def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        payload = self._payload(request)
        try:
            response = self._client.post("chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            message = data["choices"][0]["message"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            if isinstance(exc, httpx.HTTPError):
                raise self._error(exc) from exc
            raise NOVAProviderError(
                f"Unexpected {self.provider_name} response shape",
                code=ProviderErrorCode.invalid_response,
                provider=self.provider_name,
            ) from exc
        return ChatCompletionResponse(
            message=ChatMessage(role=message.get("role", "assistant"), content=message.get("content", "")),
            model=data.get("model", payload["model"]),
            usage=data.get("usage"),
            provider=self.provider_name,
        )

    def stream(
        self,
        request: ChatCompletionRequest,
        cancellation: StreamCancellation | None = None,
    ) -> Iterator[dict[str, Any]]:
        try:
            with self._client.stream("POST", "chat/completions", json=self._payload(request, stream=True)) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if cancellation is not None and cancellation.cancelled:
                        break
                    if not line:
                        continue
                    raw = line.removeprefix("data: ")
                    if raw.strip() == "[DONE]":
                        break
                    try:
                        frame = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    content = (((frame.get("choices") or [{}])[0].get("delta") or {}).get("content"))
                    if content:
                        yield {"content": content}
                    if frame.get("usage"):
                        yield {"usage": frame["usage"]}
        except httpx.HTTPError as exc:
            raise self._error(exc) from exc

    def list_models(self) -> list[ModelInfo]:
        try:
            response = self._client.get("models")
            response.raise_for_status()
            return [ModelInfo(name=str(item.get("id", ""))) for item in response.json().get("data", []) if item.get("id")]
        except httpx.HTTPError as exc:
            raise self._error(exc) from exc

    def health(self) -> bool:
        try:
            response = self._client.get("models")
            return response.status_code < 500 and response.status_code not in (401, 403)
        except httpx.HTTPError:
            return False

    def supports_images(self, model: str) -> bool:
        return True

    def close(self) -> None:
        self._client.close()


class OpenAIProvider(OpenAICompatibleProvider):
    provider_name = "openai"

from __future__ import annotations

import json

import httpx
import pytest

from nova.core.config import (
    GeminiProviderSettings,
    OpenAICompatibleProviderSettings,
    OpenAIProviderSettings,
)
from nova.llm.base import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    LLMProvider,
    ModelInfo,
    NOVAProviderError,
    ProviderErrorCode,
    StreamCancellation,
)
from nova.llm.gemini import GeminiProvider
from nova.llm.model_registry import ModelLocation, ModelRegistry
from nova.llm.openai_compatible import OpenAICompatibleProvider, OpenAIProvider
from nova.llm.orchestrator import CloudConfirmationRequired, InferenceOrchestrator
from nova.llm.privacy import PrivacyRedactor
from nova.llm.router import RoutingDecision


def request() -> ChatCompletionRequest:
    return ChatCompletionRequest([ChatMessage("user", "hola")], model="test-model")


def test_openai_chat_models_and_streaming() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer secret"
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "test-model"}]})
        body = json.loads(req.content)
        if body.get("stream"):
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ho"}}]}\n\ndata: {"choices":[{"delta":{"content":"la"}}],"usage":{"total_tokens":2}}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"model": "test-model", "choices": [{"message": {"role": "assistant", "content": "hola"}}]})

    settings = OpenAIProviderSettings(enabled=True, api_key="secret", default_model="test-model")
    provider = OpenAIProvider(settings, httpx.Client(transport=httpx.MockTransport(handler), base_url=settings.base_url + "/"))
    assert provider.chat(request()).message.content == "hola"
    assert provider.list_models()[0].name == "test-model"
    frames = list(provider.stream(request()))
    assert "".join(frame.get("content", "") for frame in frames) == "hola"
    assert frames[-1]["usage"]["total_tokens"] == 2


def test_openai_compatible_normalizes_timeout() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("late")

    settings = OpenAICompatibleProviderSettings(name="gateway", enabled=True, base_url="https://gateway.test/v1", api_key="secret")
    provider = OpenAICompatibleProvider(settings, httpx.Client(transport=httpx.MockTransport(handler), base_url=settings.base_url + "/"))
    with pytest.raises(NOVAProviderError) as error:
        provider.chat(request())
    assert error.value.code == ProviderErrorCode.timeout
    assert error.value.provider == "gateway"


def test_gemini_chat_models_stream_and_cancellation() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["x-goog-api-key"] == "secret"
        if req.method == "GET":
            return httpx.Response(200, json={"models": [{"name": "models/gemini-test", "inputTokenLimit": 1234, "supportedGenerationMethods": ["generateContent"]}]})
        if "streamGenerateContent" in req.url.path:
            return httpx.Response(200, text='data: {"candidates":[{"content":{"parts":[{"text":"hola"}]}}]}\n\n')
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hola"}]}}], "usageMetadata": {"totalTokenCount": 2}})

    settings = GeminiProviderSettings(enabled=True, api_key="secret", default_model="gemini-test")
    provider = GeminiProvider(settings, httpx.Client(transport=httpx.MockTransport(handler), base_url=settings.base_url + "/", headers={"x-goog-api-key": "secret"}))
    assert provider.chat(request()).message.content == "hola"
    assert provider.list_models()[0].context_window == 1234
    cancellation = StreamCancellation.fresh()
    assert next(iter(provider.stream(request(), cancellation)))["content"] == "hola"


def test_registry_is_provider_aware() -> None:
    settings = OpenAIProviderSettings(enabled=True, api_key="secret")
    provider = OpenAIProvider(settings, httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"data": [{"id": "cloud-model"}]})), base_url=settings.base_url + "/"))
    registry = ModelRegistry()
    rows = registry.register_provider_models("openai", provider)
    assert rows[0].provider == "openai"
    assert rows[0].location == ModelLocation.cloud
    assert registry.candidates(location=ModelLocation.cloud)[0].model_id == "cloud-model"


def test_privacy_redaction_never_mutates_original() -> None:
    original = ChatCompletionRequest([ChatMessage("user", "API_KEY=abc123456789 password=hunter2 C:\\Users\\Ana\\Private")])
    sanitized, count = PrivacyRedactor().request(original)
    assert count == 3
    assert "abc123456789" not in sanitized.messages[0].content
    assert "[LOCAL_PATH]" in sanitized.messages[0].content
    assert "abc123456789" in original.messages[0].content


class FakeProvider(LLMProvider):
    def __init__(self, name: str, *, fail: bool = False, chunks: list[str] | None = None) -> None:
        self.provider_name = name
        self.location = "local" if name == "ollama" else "cloud"
        self.fail = fail
        self.chunks = chunks or [name]
        self.calls = 0

    def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        self.calls += 1
        if self.fail:
            raise NOVAProviderError("failed", provider=self.provider_name)
        return ChatCompletionResponse(ChatMessage("assistant", self.provider_name), request.model or "model", provider=self.provider_name)

    def stream(self, request, cancellation=None):
        self.calls += 1
        for chunk in self.chunks:
            yield {"content": chunk}
        if self.fail:
            raise NOVAProviderError("failed", provider=self.provider_name)

    def list_models(self) -> list[ModelInfo]:
        return []

    def health(self) -> bool:
        return not self.fail


def test_cloud_fallback_is_blocked_without_router_candidates() -> None:
    local, cloud = FakeProvider("ollama", fail=True), FakeProvider("openai")
    orchestrator = InferenceOrchestrator({"ollama": local, "openai": cloud})
    decision = RoutingDecision("heavy", "local", "local", "local policy")
    with pytest.raises(NOVAProviderError):
        orchestrator.chat(request(), decision)
    assert cloud.calls == 0


def test_controlled_cloud_fallback_and_confirmation() -> None:
    local, cloud = FakeProvider("ollama", fail=True), FakeProvider("openai")
    orchestrator = InferenceOrchestrator({"ollama": local, "openai": cloud})
    decision = RoutingDecision(
        "heavy", "local", "local", "fallback configured",
        fallback_allowed=True, fallbacks=(("openai", "cloud-model"),),
        fallback_confirmation_required=True, redact=True,
    )
    with pytest.raises(CloudConfirmationRequired):
        orchestrator.chat(request(), decision)
    assert cloud.calls == 0
    assert orchestrator.chat(request(), decision, cloud_confirmed=True).provider == "openai"
    assert cloud.calls == 1


def test_stream_never_duplicates_after_a_delta() -> None:
    cloud = FakeProvider("openai", fail=True, chunks=["partial"])
    local = FakeProvider("ollama")
    orchestrator = InferenceOrchestrator({"ollama": local, "openai": cloud})
    decision = RoutingDecision("heavy", "cloud", "reasoning", "cloud", provider="openai", location="cloud")
    emitted: list[str] = []
    with pytest.raises(NOVAProviderError):
        for frame in orchestrator.stream(request(), decision, cloud_confirmed=True):
            emitted.append(frame.get("content", ""))
    assert emitted == ["partial"]
    assert local.calls == 0


def test_secret_value_is_not_in_settings_repr() -> None:
    settings = OpenAIProviderSettings(api_key="top-secret-value")
    assert "top-secret-value" not in repr(settings)

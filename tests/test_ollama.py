from __future__ import annotations

import httpx
import pytest

from nova.core.config import LLMSettings
from nova.llm.base import ChatCompletionRequest, ChatMessage, NOVAProviderError
from nova.llm.ollama import OllamaProvider


def _settings() -> LLMSettings:
    return LLMSettings(base_url="http://ollama.test:11434")


def _ok_chat_response(url: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "model": "llama3.1:8b",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hola"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
        },
        request=httpx.Request("POST", url),
    )


class _FakeClient:
    def __init__(self, **kwargs) -> None:
        self.posted: list[tuple[str, dict]] = []
        self.getted: list[str] = []

    def post(self, url: str, json=None) -> httpx.Response:
        self.posted.append((url, json or {}))
        return _ok_chat_response(url)

    def get(self, url: str) -> httpx.Response:
        self.getted.append(url)
        if url == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "llama3.1:8b", "size": 1024, "modified_at": "2026-01-01"},
                        {"name": "qwen2.5-coder:7b", "size": 4096, "modified_at": "2026-01-02"},
                    ]
                },
                request=httpx.Request("GET", url),
            )
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", url))

    def close(self) -> None:
        pass


class _BoomClient:
    def post(self, url: str, json=None) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=httpx.Request("POST", url))

    def get(self, url: str) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=httpx.Request("GET", url))

    def close(self) -> None:
        pass


def test_chat_uses_openai_compatible_payload() -> None:
    client = _FakeClient()
    provider = OllamaProvider(_settings(), client=client)
    request = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="di hola")],
        model="qwen2.5-coder:7b",
    )

    response = provider.chat(request)

    assert response.message.role == "assistant"
    assert response.message.content == "hola"
    assert response.model == "llama3.1:8b"
    assert response.usage["total_tokens"] == 5

    url, payload = client.posted[0]
    assert url == "/v1/chat/completions"
    assert payload["model"] == "qwen2.5-coder:7b"
    assert payload["messages"] == [{"role": "user", "content": "di hola"}]


def test_chat_uses_default_model_when_not_specified() -> None:
    client = _FakeClient()
    provider = OllamaProvider(_settings(), client=client)
    request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="hola")])
    provider.chat(request)
    _, payload = client.posted[0]
    assert payload["model"] == _settings().default_model


def test_list_models_parses_tags() -> None:
    client = _FakeClient()
    provider = OllamaProvider(_settings(), client=client)
    models = provider.list_models()
    assert [m.name for m in models] == ["llama3.1:8b", "qwen2.5-coder:7b"]
    assert models[0].size == 1024


def test_health_ok() -> None:
    provider = OllamaProvider(_settings(), client=_FakeClient())
    assert provider.health() is True


def test_chat_wraps_connection_error() -> None:
    provider = OllamaProvider(_settings(), client=_BoomClient())
    with pytest.raises(NOVAProviderError):
        provider.chat(ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")]))


def test_health_false_when_unreachable() -> None:
    provider = OllamaProvider(_settings(), client=_BoomClient())
    assert provider.health() is False


def test_unknown_provider_rejected() -> None:
    from nova.llm.registry import create_provider

    with pytest.raises(ValueError):
        create_provider(LLMSettings(provider="nope"))


def test_ollama_integration_reachable():
    settings = LLMSettings()
    provider = OllamaProvider(settings)
    try:
        if not provider.health():
            pytest.skip("Ollama is not reachable")
        models = provider.list_models()
        assert isinstance(models, list)
        assert any(m.name == settings.default_model for m in models)
    finally:
        provider.close()
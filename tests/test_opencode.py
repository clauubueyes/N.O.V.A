from __future__ import annotations

import httpx
import pytest

from nova.core.config import OpenCodeProviderSettings
from nova.llm.base import ChatCompletionRequest, ChatMessage, NOVAProviderError
from nova.llm.opencode import (
    OpenCodeProvider,
    _messages_to_prompt,
    _parse_model,
)


def _provider(responses: dict, base_url: str = "http://127.0.0.1:4096") -> OpenCodeProvider:
    """Build an OpenCodeProvider whose HTTP client short-circuits to `responses`.

    `responses` is a dict mapping the request path to either a JSON payload or a
    ``(status_code, payload)`` tuple.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        item = responses.get(path)
        if item is None:
            return httpx.Response(404, request=request)
        if isinstance(item, tuple):
            status, payload = item
            return httpx.Response(status, json=payload, request=request)
        return httpx.Response(200, json=item, request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(base_url=base_url, transport=transport)
    return OpenCodeProvider(settings=OpenCodeProviderSettings(), client=client)


class TestParseModel:
    def test_provider_and_model(self) -> None:
        assert _parse_model("openai/gpt-4o") == ("openai", "gpt-4o")

    def test_bare_model(self) -> None:
        assert _parse_model("gpt-4o") == ("", "gpt-4o")

    def test_whitespace(self) -> None:
        assert _parse_model("  anthropic / claude-3  ") == ("anthropic", "claude-3")


class TestMessagesToPrompt:
    def test_system_and_user(self) -> None:
        prompt = _messages_to_prompt(
            [
                ChatMessage(role="system", content="Be brief."),
                ChatMessage(role="user", content="Hello"),
            ]
        )
        assert "[System context]" in prompt
        assert "Be brief." in prompt
        assert prompt.endswith("Hello")

    def test_assistant_prefix(self) -> None:
        prompt = _messages_to_prompt(
            [
                ChatMessage(role="user", content="2+2?"),
                ChatMessage(role="assistant", content="4"),
                ChatMessage(role="user", content="ok"),
            ]
        )
        assert "Assistant: 4" in prompt


class TestChat:
    def test_chat_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/session":
                return httpx.Response(200, json={"id": "s1"}, request=request)
            if request.url.path == "/session/s1/message":
                return httpx.Response(
                    200,
                    json={"parts": [{"type": "text", "text": "Hello there."}]},
                    request=request,
                )
            if request.url.path == "/session/s1":
                return httpx.Response(200, json={}, request=request)
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        provider = OpenCodeProvider(
            settings=OpenCodeProviderSettings(),
            client=httpx.Client(base_url="http://127.0.0.1:4096", transport=transport),
        )
        response = provider.chat(
            ChatCompletionRequest(
                messages=[ChatMessage(role="user", content="Hi")],
                model="openai/gpt-4o",
            )
        )
        assert response.message.content == "Hello there."
        assert response.model == "openai/gpt-4o"

    def test_chat_error_maps_to_nova_error(self) -> None:
        provider = _provider({"/session": (401, {"error": "no"})})
        with pytest.raises(NOVAProviderError):
            provider.chat(
                ChatCompletionRequest(
                    messages=[ChatMessage(role="user", content="Hi")],
                    model="openai/gpt-4o",
                )
            )

    def test_chat_empty_response_raises(self) -> None:
        provider = _provider(
            {
                "/session": {"id": "s1"},
                "/session/s1/message": {"parts": [{"type": "tool"}]},
            }
        )
        with pytest.raises(NOVAProviderError, match="empty response"):
            provider.chat(
                ChatCompletionRequest(
                    messages=[ChatMessage(role="user", content="Hi")],
                    model="gpt-4o",
                )
            )

    def test_chat_cleans_up_session(self) -> None:
        deleted: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/session":
                return httpx.Response(200, json={"id": "s2"}, request=request)
            if request.url.path == "/session/s2/message":
                return httpx.Response(
                    200, json={"parts": [{"type": "text", "text": "ok"}]}, request=request
                )
            if request.url.path == "/session/s2":
                deleted.append(request.method)
                return httpx.Response(200, json={}, request=request)
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        provider = OpenCodeProvider(
            settings=OpenCodeProviderSettings(),
            client=httpx.Client(base_url="http://127.0.0.1:4096", transport=transport),
        )
        provider.chat(ChatCompletionRequest(messages=[ChatMessage(role="user", content="x")]))
        assert deleted == ["DELETE"]


class TestListModels:
    def test_list_models_from_providers(self) -> None:
        provider = _provider(
            {
                "/config/providers": {
                    "providers": [
                        {
                            "id": "openai",
                            "models": [{"id": "gpt-4o", "name": "GPT-4o"}, {"id": "gpt-4o-mini"}],
                        },
                        {"id": "anthropic", "models": ["claude-3"]},
                    ]
                }
            }
        )
        models = provider.list_models()
        names = {m.name for m in models}
        assert names == {"openai/gpt-4o", "openai/gpt-4o-mini", "anthropic/claude-3"}

    def test_list_models_on_error_returns_empty(self) -> None:
        provider = _provider({"/config/providers": (500, {"detail": "boom"})})
        assert provider.list_models() == []


class TestHealth:
    def test_health_true(self) -> None:
        provider = _provider({"/global/health": {"healthy": True}})
        assert provider.health() is True

    def test_health_false_on_unreachable(self) -> None:
        provider = _provider({"/global/health": (503, {})})
        assert provider.health() is False
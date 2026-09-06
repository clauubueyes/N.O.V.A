from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from nova.api.app import create_app
from nova.core.config import load_settings
from nova.llm.base import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    LLMProvider,
    ModelInfo,
    NOVAProviderError,
)


class FakeProvider(LLMProvider):
    supports_embedding = False

    def __init__(self) -> None:
        self.chat_calls: list[ChatCompletionRequest] = []

    def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        self.chat_calls.append(request)
        last_user = next(
            (m.content for m in reversed(request.messages) if m.role == "user"), ""
        )
        return ChatCompletionResponse(
            message=ChatMessage(role="assistant", content=f"echo: {last_user}"),
            model=request.model or "fake-model",
        )

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="fake-model", size=1024)]

    def health(self) -> bool:
        return True

    def close(self) -> None:
        pass


class BoomProvider(LLMProvider):
    supports_embedding = False

    def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        raise NOVAProviderError("boom chat")

    def list_models(self) -> list[ModelInfo]:
        raise NOVAProviderError("boom models")

    def health(self) -> bool:
        return False

    def close(self) -> None:
        pass


def _client(tmp_path, provider=None) -> TestClient:
    settings = load_settings()
    settings.memory.db_file = str(tmp_path / "memory.db")
    settings.audit.file = str(tmp_path / "audit.jsonl")
    return TestClient(create_app(settings, provider=provider or FakeProvider()))


def _session_id(client: TestClient) -> str:
    response = client.post("/v1/sessions")
    assert response.status_code == 200
    return response.json()["session_id"]


class TestMeta:
    def test_healthz(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            data = client.get("/healthz").json()
            assert data["status"] == "ok"
            assert data["provider"] is True
            assert data["version"]

    def test_healthz_degraded(self, tmp_path) -> None:
        with _client(tmp_path, provider=BoomProvider()) as client:
            data = client.get("/healthz").json()
            assert data["status"] == "degraded"

    def test_serves_web_interface(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            response = client.get("/")
            assert response.status_code == 200
            assert "N.O.V.A." in response.text

    def test_tools_include_standard_and_memory(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            tools = client.get("/v1/tools").json()
            names = {tool["name"] for tool in tools}
            assert {"calculate", "date_time", "list_dir", "remember", "memory_search"} <= names


class TestModels:
    def test_list_models(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            models = client.get("/v1/models").json()
            assert models[0]["name"] == "fake-model"

    def test_list_models_provider_error(self, tmp_path) -> None:
        with _client(tmp_path, provider=BoomProvider()) as client:
            response = client.get("/v1/models")
            assert response.status_code == 502


class TestChat:
    def test_stateless_chat(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            response = client.post(
                "/v1/chat",
                json={"messages": [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "hola"}], "model": "m"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["message"] == {"role": "assistant", "content": "echo: hola"}
            assert data["model"] == "m"

    def test_stateless_chat_provider_error(self, tmp_path) -> None:
        with _client(tmp_path, provider=BoomProvider()) as client:
            response = client.post("/v1/chat", json={"messages": [{"role": "user", "content": "x"}]})
            assert response.status_code == 502


class TestSessions:
    def test_create_session(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            created = client.post("/v1/sessions")
            assert created.status_code == 200
            assert created.json()["session_id"]
            assert created.json()["model"]

    def test_session_chat_roundtrip(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            session_id = _session_id(client)
            response = client.post(
                f"/v1/sessions/{session_id}/chat", json={"message": "cuentame algo"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["reply"] == "echo: cuentame algo"
            assert data["session_id"] == session_id

            messages = client.get(f"/v1/sessions/{session_id}/messages").json()["messages"]
            assert [m["role"] for m in messages] == ["system", "user", "assistant"]
            assert messages[-1]["content"] == "echo: cuentame algo"

    def test_session_chat_recovers_memory_context(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            session_id = _session_id(client)
            remembered = client.post(
                f"/v1/sessions/{session_id}/remember",
                json={"content": "proyecto favorito es atlas", "kind": "fact"},
            )
            assert remembered.status_code == 200

            response = client.post(
                f"/v1/sessions/{session_id}/chat", json={"message": "atlas"}
            )
            data = response.json()
            assert "atlas" in data["context"]

    def test_session_unknown_404(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            assert client.get("/v1/sessions/nope/messages").status_code == 404
            assert client.post("/v1/sessions/nope/chat", json={"message": "x"}).status_code == 404
            assert client.delete("/v1/sessions/nope").status_code == 404

    def test_delete_session(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            session_id = _session_id(client)
            deleted = client.delete(f"/v1/sessions/{session_id}")
            assert deleted.status_code == 200
            assert client.get(f"/v1/sessions/{session_id}/messages").status_code == 404

    def test_run_tool_allowed_by_permissions(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            session_id = _session_id(client)
            response = client.post(
                f"/v1/sessions/{session_id}/run",
                json={"tool": "calculate", "args": {"expression": "2 + 2"}},
            )
            assert response.status_code == 200
            result = response.json()["result"]
            assert result["ok"] is True
            assert result["data"]["result"] == 4

    def test_run_tool_asking_autonomy_is_denied(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            session_id = _session_id(client)
            response = client.post(
                f"/v1/sessions/{session_id}/run",
                json={"tool": "list_dir", "args": {"path": "."}},
            )
            assert response.status_code == 200
            result = response.json()["result"]
            assert result["ok"] is False
            assert "permission denied" in result["message"]

    def test_remember_and_search_memory(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            session_id = _session_id(client)
            client.post(
                f"/v1/sessions/{session_id}/remember",
                json={"content": "el wifi se corta a las 3", "kind": "fact"},
            )
            recent = client.get(f"/v1/sessions/{session_id}/memory").json()
            assert recent["type"] == "recent"
            assert recent["memories"][0]["content"] == "el wifi se corta a las 3"

            search = client.get(f"/v1/sessions/{session_id}/memory", params={"q": "wifi"}).json()
            assert search["type"] == "search"
            assert search["hits"][0]["content"] == "el wifi se corta a las 3"


class TestSessionChatProviderError:
    def test_session_chat_provider_error(self, tmp_path) -> None:
        with _client(tmp_path, provider=BoomProvider()) as client:
            session_id = _session_id(client)
            response = client.post(f"/v1/sessions/{session_id}/chat", json={"message": "x"})
            assert response.status_code == 502

    def test_session_does_not_accumulate_after_error(self, tmp_path) -> None:
        with _client(tmp_path, provider=BoomProvider()) as client:
            session_id = _session_id(client)
            client.post(f"/v1/sessions/{session_id}/chat", json={"message": "x"})
            messages = client.get(f"/v1/sessions/{session_id}/messages").json()["messages"]
            roles = [m["role"] for m in messages]
            # user turn was stored before the provider call, assistant was not
            assert "assistant" not in roles
            assert "user" in roles
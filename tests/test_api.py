from __future__ import annotations

import json
import threading
import time

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
            assert isinstance(data["uptime_s"], int) and data["uptime_s"] >= 0
            assert isinstance(data["sessions"], int)
            assert data["model"]
            assert data["rag_enabled"] is True
            assert isinstance(data["streams"], int)

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


class CountingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.model_list_calls = 0

    def list_models(self) -> list[ModelInfo]:
        self.model_list_calls += 1
        return super().list_models()


class TestModelsCache:
    def _cached_client(self, tmp_path, provider, ttl):
        settings = load_settings()
        settings.memory.db_file = str(tmp_path / "memory.db")
        settings.audit.file = str(tmp_path / "audit.jsonl")
        return TestClient(create_app(settings, provider=provider, models_cache_ttl_s=ttl))

    def test_models_cached_within_ttl(self, tmp_path) -> None:
        provider = CountingProvider()
        with self._cached_client(tmp_path, provider, 60) as client:
            first = client.get("/v1/models").json()
            assert first[0]["name"] == "fake-model"
            second = client.get("/v1/models").json()
            assert second == first
            assert provider.model_list_calls == 1

    def test_models_cache_expires(self, tmp_path) -> None:
        provider = CountingProvider()
        with self._cached_client(tmp_path, provider, 0.05) as client:
            client.get("/v1/models")
            assert provider.model_list_calls == 1
            time.sleep(0.08)
            client.get("/v1/models")
            assert provider.model_list_calls == 2

    def test_models_cache_does_not_cache_errors(self, tmp_path) -> None:
        with self._cached_client(tmp_path, BoomProvider(), 60) as client:
            assert client.get("/v1/models").status_code == 502
            assert client.get("/v1/models").status_code == 502


class TestAuditEndpoint:
    def test_list_audit_empty(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            data = client.get("/v1/audit").json()
            assert data["count"] == 0
            assert data["entries"] == []

    def test_list_audit_records_tool_runs(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            session_id = _session_id(client)
            client.post(
                f"/v1/sessions/{session_id}/run",
                json={"tool": "calculate", "args": {"expression": "2+2"}},
            )
            data = client.get("/v1/audit?limit=10").json()
            assert data["count"] >= 1
            entry = data["entries"][-1]
            assert entry["tool"] == "calculate"
            assert "decision" in entry
            assert "ts" in entry

    def test_list_audit_limit_validation(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            assert client.get("/v1/audit?limit=0").status_code == 422
            assert client.get("/v1/audit?limit=999").status_code == 422


class TestRouting:
    def _routed_client(self, tmp_path, provider=None) -> TestClient:
        settings = load_settings()
        settings.memory.db_file = str(tmp_path / "memory.db")
        settings.audit.file = str(tmp_path / "audit.jsonl")
        settings.llm.models = {
            "small": "small-model",
            "coding": "coder-model",
            "local": "local-model",
        }
        # Deterministic resources (abundant RAM) so routing tests are
        # machine-independent instead of depending on live readings.
        from nova.llm.resources import ResourceManager

        abundant = ResourceManager(
            ram_reader=lambda: {"total": 64.0, "available": 56.0},
            cpu_reader=lambda: (20.0, 8),
            gpu_reader=lambda: (True, 24.0),
            battery_reader=lambda: (100.0, True),
        )
        return TestClient(create_app(settings, provider=provider or FakeProvider(), router_resources=abundant))

    def test_route_simple(self, tmp_path) -> None:
        with self._routed_client(tmp_path) as client:
            data = client.post("/v1/route", json={"messages": [{"role": "user", "content": "hola!"}]}).json()
            assert data["task_kind"] == "simple"
            assert data["model"] == "small-model"

    def test_route_coding(self, tmp_path) -> None:
        with self._routed_client(tmp_path) as client:
            data = client.post(
                "/v1/route", json={"messages": [{"role": "user", "content": "escribe una funcion en python"}]}
            ).json()
            assert data["task_kind"] == "coding"
            assert data["model"] == "coder-model"

    def test_session_chat_routes_model_when_not_specified(self, tmp_path) -> None:
        provider = FakeProvider()
        with self._routed_client(tmp_path, provider=provider) as client:
            session_id = _session_id(client)
            response = client.post(
                f"/v1/sessions/{session_id}/chat", json={"message": "hola como estas?"}
            )
            assert response.status_code == 200
            # no explicit model -> router picks the small greeting model
            assert provider.chat_calls[-1].model == "small-model"

    def test_session_chat_keeps_explicit_model(self, tmp_path) -> None:
        provider = FakeProvider()
        with self._routed_client(tmp_path, provider=provider) as client:
            session_id = _session_id(client)
            client.post(
                f"/v1/sessions/{session_id}/chat",
                json={"message": "hola", "model": "explicit-model"},
            )
            assert provider.chat_calls[-1].model == "explicit-model"


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
            assert [m["role"] for m in messages] == ["user", "assistant"]
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
            assert "assistant" not in roles
            assert "user" not in roles


class ScriptedAgentProvider(LLMProvider):
    """Returns a queued response per chat call (tool JSON first, then an answer)."""

    supports_embedding = False

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests: list[ChatCompletionRequest] = []

    def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        self.requests.append(request)
        content = self.responses.pop(0)
        return ChatCompletionResponse(
            message=ChatMessage(role="assistant", content=content),
            model="fake-model",
        )

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="fake-model", size=1024)]

    def health(self) -> bool:
        return True

    def close(self) -> None:
        pass


def _full_autonomy_client(tmp_path, provider) -> TestClient:
    from nova.core.config import AutonomyLevel

    settings = load_settings()
    settings.memory.db_file = str(tmp_path / "memory.db")
    settings.audit.file = str(tmp_path / "audit.jsonl")
    settings.permissions.autonomy = AutonomyLevel.full
    return TestClient(create_app(settings, provider=provider))


class TestAgentsApi:
    def test_list_agents(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            agents = client.get("/v1/agents").json()
            assert {agent["name"] for agent in agents} == {
                "general",
                "coding",
                "research",
                "system",
                "automation",
            }
            assert all(agent["description"] for agent in agents)

    def test_agent_chat_runs_tool_and_returns_steps(self, tmp_path) -> None:
        provider = ScriptedAgentProvider(
            ['{"tool": "calculate", "args": {"expression": "2+2"}}', "El resultado es 4."]
        )
        with _full_autonomy_client(tmp_path, provider) as client:
            response = client.post(
                "/v1/agents/system/chat", json={"message": "cuanto es 2+2?"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["agent"] == "system"
            assert data["reply"] == "El resultado es 4."
            assert data["steps"][0]["tool"] == "calculate"
            assert data["steps"][0]["ok"] is True
            assert data["steps"][0]["data"]["result"] == 4

    def test_agent_chat_tool_denied_in_ask(self, tmp_path) -> None:
        from nova.core.config import AutonomyLevel

        def ask_client(provider) -> TestClient:
            settings = load_settings()
            settings.memory.db_file = str(tmp_path / "memory.db")
            settings.audit.file = str(tmp_path / "audit.jsonl")
            settings.permissions.autonomy = AutonomyLevel.ask  # API never confirms
            settings.permissions.deny = ["calculate"]  # explicit deny wins over allow
            return TestClient(create_app(settings, provider=provider))

        call_provider = ScriptedAgentProvider(
            ['{"tool": "calculate", "args": {"expression": "2+2"}}', "no puedo"]
        )
        with ask_client(call_provider) as client:
            data = client.post("/v1/agents/system/chat", json={"message": "x"}).json()
            assert data["steps"][0]["ok"] is False
            assert "permission denied" in data["steps"][0]["message"]
            assert data["steps"][0]["tool"] == "calculate"

    def test_session_with_agent(self, tmp_path) -> None:
        provider = ScriptedAgentProvider(["respuesta directa"])
        with _full_autonomy_client(tmp_path, provider) as client:
            created = client.post("/v1/sessions", json={"agent": "coding"}).json()
            assert created["agent"] == "coding"
            session_id = created["session_id"]
            data = client.post(
                f"/v1/sessions/{session_id}/chat", json={"message": "hola"}
            ).json()
            assert data["agent"] == "coding"
            assert data["reply"] == "respuesta directa"
            assert data["steps"] == []
            messages = client.get(f"/v1/sessions/{session_id}/messages").json()["messages"]
            assert messages[-1]["content"] == "respuesta directa"

    def test_unknown_agent_returns_404(self, tmp_path) -> None:
        with _client(tmp_path) as client:
            assert (
                client.post("/v1/agents/unknown/chat", json={"message": "x"}).status_code
                == 404
            )
            assert client.post("/v1/sessions", json={"agent": "unknown"}).status_code == 404


class TestPersistence:
    def _restartable_client(self, tmp_path):
        memory_path = tmp_path / "memory.db"
        audit_path = tmp_path / "audit.jsonl"

        def make_client() -> TestClient:
            settings = load_settings()
            settings.memory.db_file = str(memory_path)
            settings.audit.file = str(audit_path)
            return TestClient(create_app(settings, provider=FakeProvider()))

        return make_client

    def test_session_survives_restart(self, tmp_path) -> None:
        make_client = self._restartable_client(tmp_path)
        with make_client() as client:
            session_id = _session_id(client)
            response = client.post(
                f"/v1/sessions/{session_id}/chat",
                json={"message": "hola persistente", "model": "fake-model"},
            )
            assert response.status_code == 200
            assert response.json()["model"] == "fake-model"
        with make_client() as client:
            sessions = client.get("/v1/sessions").json()
            assert any(s["session_id"] == session_id for s in sessions)
            restored = client.get("/v1/sessions").json()
            row = next(s for s in restored if s["session_id"] == session_id)
            assert row["model"] == "fake-model"
            messages = client.get(f"/v1/sessions/{session_id}/messages").json()["messages"]
            assert messages[-2]["content"] == "hola persistente"
            assert messages[-1]["content"] == "echo: hola persistente"

    def test_delete_session_removes_persistence(self, tmp_path) -> None:
        make_client = self._restartable_client(tmp_path)
        with make_client() as client:
            session_id = _session_id(client)
            client.post(
                f"/v1/sessions/{session_id}/chat", json={"message": "hola"}
            )
        with make_client() as client:
            assert client.delete(f"/v1/sessions/{session_id}").status_code == 200
        with make_client() as client:
            assert client.get(f"/v1/sessions/{session_id}/messages").status_code == 404


class StreamingFakeProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.stream_calls: list[ChatCompletionRequest] = []

    def stream(self, request, cancellation=None):
        self.stream_calls.append(request)
        for piece in ["hola ", "cruel ", "mundo"]:
            if cancellation is not None and cancellation.cancelled:
                return
            yield {"content": piece}
        yield {"usage": {"total_tokens": 6}}


class GateStreamingProvider(StreamingFakeProvider):
    def __init__(self, gate: threading.Event) -> None:
        super().__init__()
        self.gate = gate
        self.emitted = threading.Event()

    def stream(self, request, cancellation=None):
        for piece in ["hola ", "cruel "]:
            if cancellation is not None and cancellation.cancelled:
                return
            yield {"content": piece}
            self.emitted.set()
        while not self.gate.is_set():
            if cancellation is not None and cancellation.cancelled:
                return
            time.sleep(0.01)
        yield {"content": "mundo"}
        yield {"usage": {"total_tokens": 6}}


class TestStreaming:
    @staticmethod
    def _events(body: str) -> list[dict]:
        return [
            json.loads(line[len("data: "):])
            for line in body.splitlines()
            if line.startswith("data: ")
        ]

    def test_chat_stream_yields_delta_and_done(self, tmp_path) -> None:
        with _client(tmp_path, provider=StreamingFakeProvider()) as client:
            with client.stream(
                "POST",
                "/v1/chat",
                json={"messages": [{"role": "user", "content": "hola"}], "stream": True},
            ) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                body = "".join(response.iter_text())
        frames = self._events(body)
        assert [f for f in frames if "delta" in f]
        assert "".join(f.get("delta", "") for f in frames) == "hola cruel mundo"
        done = [f for f in frames if f.get("done") is True]
        assert len(done) == 1
        assert done[0]["content"] == "hola cruel mundo"
        assert done[0]["model"]

    def test_session_chat_stream_and_records(self, tmp_path) -> None:
        provider = StreamingFakeProvider()
        with _client(tmp_path, provider=provider) as client:
            session_id = _session_id(client)
            with client.stream(
                "POST",
                f"/v1/sessions/{session_id}/chat",
                json={"message": "hola", "stream": True},
            ) as response:
                body = "".join(response.iter_text())
        frames = self._events(body)
        done = [f for f in frames if f.get("done") is True and not f.get("cancelled")]
        assert len(done) == 1
        assert done[0]["content"] == "hola cruel mundo"
        assert "usage" in done[0]
        assert len(provider.stream_calls) == 1
        with _client(tmp_path, provider=provider) as client:
            messages = client.get(f"/v1/sessions/{session_id}/messages").json()["messages"]
            assert messages[-2]["content"] == "hola"
            assert messages[-1]["content"] == "hola cruel mundo"

    def test_session_stream_does_not_stitch_after_error(self, tmp_path) -> None:
        with _client(tmp_path, provider=BoomProvider()) as client:
            session_id = _session_id(client)
            with client.stream(
                "POST",
                f"/v1/sessions/{session_id}/chat",
                json={"message": "hola", "stream": True},
            ) as response:
                body = "".join(response.iter_text())
        frames = self._events(body)
        assert any("error" in f for f in frames)
        assert any(f.get("done") is not None and f.get("done") for f in frames)
        with _client(tmp_path, provider=BoomProvider()) as client:
            messages = client.get(f"/v1/sessions/{session_id}/messages").json()["messages"]
            assert messages == []

    def test_session_stream_stop_cancels(self, tmp_path) -> None:
        gate = threading.Event()
        provider = GateStreamingProvider(gate)
        with _client(tmp_path, provider=provider) as client:
            session_id = _session_id(client)
            captured = {}

            def consume() -> None:
                with client.stream(
                    "POST",
                    f"/v1/sessions/{session_id}/chat",
                    json={"message": "hola", "stream": True},
                ) as response:
                    captured["status"] = response.status_code
                    captured["body"] = "".join(response.iter_text())

            thread = threading.Thread(target=consume)
            thread.start()
            assert provider.emitted.wait(timeout=5)
            stop = client.post(f"/v1/sessions/{session_id}/stop")
            assert stop.status_code == 200
            thread.join(timeout=5)
            assert not thread.is_alive()
        frames = self._events(captured["body"])
        done = [f for f in frames if f.get("done") is True]
        assert len(done) == 1
        assert done[0]["cancelled"] is True
        assert done[0]["content"] == "hola cruel "

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from nova.api.app import create_app
from nova.core.config import AutonomyLevel, load_settings
from nova.llm.base import ChatCompletionRequest, ChatCompletionResponse, ChatMessage, LLMProvider, ModelInfo


class FakeProvider(LLMProvider):
    supports_embedding = False

    def __init__(self) -> None:
        self.chat_calls: list[ChatCompletionRequest] = []

    def chat(self, request):
        self.chat_calls.append(request)
        return ChatCompletionResponse(
            message=ChatMessage(role="assistant", content="ok"),
            model=request.model or "fake",
        )

    def list_models(self):
        return [ModelInfo(name="fake", size=1)]

    def health(self):
        return True

    def close(self):
        pass


def _settings(tmp_path, **api):
    settings = load_settings()
    settings.memory.db_file = str(tmp_path / "memory.db")
    settings.audit.file = str(tmp_path / "audit.jsonl")
    for key, value in api.items():
        setattr(settings.api, key, value)
    return settings


class TestAuth:
    def test_no_token_configured_allows_v1(self, tmp_path) -> None:
        with TestClient(create_app(_settings(tmp_path), provider=FakeProvider())) as client:
            assert client.get("/v1/models").status_code == 200

    def test_token_required_401(self, tmp_path) -> None:
        with TestClient(create_app(_settings(tmp_path, token="secret"), provider=FakeProvider())) as client:
            assert client.get("/v1/models").status_code == 401
            assert client.get("/healthz").status_code == 200  # healthz not protected

    def test_valid_token_200(self, tmp_path) -> None:
        with TestClient(create_app(_settings(tmp_path, token="secret"), provider=FakeProvider())) as client:
            response = client.get("/v1/models", headers={"Authorization": "Bearer secret"})
            assert response.status_code == 200

    def test_wrong_token_401(self, tmp_path) -> None:
        with TestClient(create_app(_settings(tmp_path, token="secret"), provider=FakeProvider())) as client:
            assert (
                client.get("/v1/models", headers={"Authorization": "Bearer nope"}).status_code
                == 401
            )

    def test_token_protects_session_chat(self, tmp_path) -> None:
        settings = _settings(tmp_path, token="secret")
        with TestClient(create_app(settings, provider=FakeProvider())) as client:
            created = client.post("/v1/sessions", headers={"Authorization": "Bearer secret"})
            assert created.status_code == 200
            sid = created.json()["session_id"]
            no_auth = client.post(f"/v1/sessions/{sid}/chat", json={"message": "hola"})
            assert no_auth.status_code == 401
            auth = client.post(
                f"/v1/sessions/{sid}/chat",
                json={"message": "hola"},
                headers={"Authorization": "Bearer secret"},
            )
            assert auth.status_code == 200

    def test_host_enabled_refuses_without_token(self, tmp_path) -> None:
        with pytest.raises(ValueError):
            create_app(_settings(tmp_path, host_enabled=True))


class TestHostToolsApi:
    def _host_client(self, tmp_path):
        settings = _settings(tmp_path, token="secret", host_enabled=True)
        settings.host.apps = {"notepad": "notepad.exe"}
        settings.host.commands = ["echo"]
        settings.permissions.autonomy = AutonomyLevel.full
        return TestClient(create_app(settings, provider=FakeProvider()))

    def _auth(self):
        return {"Authorization": "Bearer secret"}

    def test_host_tools_listed_when_enabled(self, tmp_path) -> None:
        with self._host_client(tmp_path) as client:
            tools = client.get("/v1/tools", headers=self._auth()).json()
            names = {tool["name"] for tool in tools}
            assert {"open_app", "open_url", "run", "read_file", "write_file", "list_files"} <= names

    def test_host_tools_not_listed_when_disabled(self, tmp_path) -> None:
        with TestClient(create_app(_settings(tmp_path, token="secret"), provider=FakeProvider())) as client:
            tools = client.get("/v1/tools", headers=self._auth()).json()
            names = {tool["name"] for tool in tools}
            assert "open_app" not in names

    def test_run_allowed_command_via_api(self, tmp_path) -> None:
        settings = _settings(tmp_path, token="secret", host_enabled=True)
        settings.host.commands = [sys.executable]
        settings.permissions.autonomy = AutonomyLevel.full
        with TestClient(create_app(settings, provider=FakeProvider())) as client:
            created = client.post("/v1/sessions", headers=self._auth()).json()
            sid = created["session_id"]
            response = client.post(
                f"/v1/sessions/{sid}/run",
                json={"tool": "run", "args": {"command": sys.executable, "args": ["-c", "print('hi')"]}},
                headers=self._auth(),
            )
            assert response.status_code == 200
            result = response.json()["result"]
            assert result["ok"] is True
            assert "hi" in result["data"]["stdout"]

    def test_host_by_default_denied_in_ask(self, tmp_path) -> None:
        # With the default `ask` autonomy the API denies ASK tools (there is no
        # human confirmation channel), even when the tool is configured.
        settings = _settings(tmp_path, token="secret", host_enabled=True)
        settings.host.commands = ["echo"]
        with TestClient(create_app(settings, provider=FakeProvider())) as client:
            created = client.post("/v1/sessions", headers=self._auth()).json()
            sid = created["session_id"]
            response = client.post(
                f"/v1/sessions/{sid}/run",
                json={"tool": "run", "args": {"command": "echo", "args": ["hola"]}},
                headers=self._auth(),
            )
            result = response.json()["result"]
            assert result["ok"] is False
            assert "permission denied" in result["message"]

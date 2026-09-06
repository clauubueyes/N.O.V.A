from __future__ import annotations

import json

import pytest

from nova.agents import agent_presets, create_agent, get_preset, parse_tool_call
from nova.agents.core import AgentStep
from nova.core.audit import AuditLog
from nova.core.config import AutonomyLevel, PermissionSettings
from nova.llm.base import ChatCompletionRequest, ChatCompletionResponse, ChatMessage, LLMProvider, ModelInfo
from nova.memory import MemoryService, MemoryStore
from nova.tools.permissions import PermissionSystem
from nova.tools.registry import ToolRegistry, create_registry
from nova.tools.runner import ToolRunner
from nova.memory.tools import RememberTool, MemorySearchTool
from nova.tools.standard import CalculateTool, DateTimeTool, ListDirTool


def _permissions():
    return PermissionSystem(PermissionSettings(autonomy=AutonomyLevel.full))


def _registry() -> ToolRegistry:
    return create_registry(
        [CalculateTool(), DateTimeTool(), ListDirTool(), RememberTool(None), MemorySearchTool(None)]
    )


class ScriptedProvider(LLMProvider):
    supports_embedding = False

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests: list[ChatCompletionRequest] = []

    def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        self.requests.append(request)
        content = self.responses.pop(0)
        return ChatCompletionResponse(
            message=ChatMessage(role="assistant", content=content),
            model="scripted",
        )

    def list_models(self) -> list[ModelInfo]:
        return []

    def health(self) -> bool:
        return True

    def close(self) -> None:
        pass


def _runner(tmp_path, permissions=None, confirm=None) -> ToolRunner:
    return ToolRunner(
        registry=_registry(),
        permissions=permissions or _permissions(),
        audit=AuditLog(str(tmp_path / "audit.jsonl")),
        confirm=confirm or (lambda _q: True),
    )


def _agent(tmp_path, responses, *, permissions=None, memory=None, max_steps=4):
    provider = ScriptedProvider(responses)
    return provider, create_agent(
        "general",
        provider=provider,
        runner=_runner(tmp_path, permissions=permissions),
        memory=memory,
        max_steps=max_steps,
        history_limit=10,
    )


class TestParseToolCall:
    def test_plain_answer_is_not_a_call(self) -> None:
        assert parse_tool_call("Hola, soy tu asistente.") is None

    def test_json_call(self) -> None:
        assert parse_tool_call('{"tool": "calculate", "args": {"expression": "2+2"}}') == (
            "calculate",
            {"expression": "2+2"},
        )

    def test_fenced_json_call(self) -> None:
        content = '```json\n{"tool": "date_time", "args": {}}\n```'
        assert parse_tool_call(content) == ("date_time", {})

    def test_invalid_json_is_final(self) -> None:
        assert parse_tool_call("Esto no es JSON {") is None

    def test_missing_tool_field_is_final(self) -> None:
        assert parse_tool_call('{"foo": 1}') is None

    def test_json_with_explanation_is_final(self) -> None:
        assert parse_tool_call('Voy a calcular:\n{"tool": "calculate", "args": {}}') is None


class TestAgent:
    def test_direct_answer_no_tool(self, tmp_path) -> None:
        provider, agent = _agent(tmp_path, ["Respuesta directa"])
        result = agent.act("dime algo")
        assert result.answer == "Respuesta directa"
        assert result.steps == []
        assert provider.requests[0].messages[0].role == "system"
        assert '"calculate"' in provider.requests[0].messages[0].content

    def test_calls_tool_then_answers(self, tmp_path) -> None:
        provider, agent = _agent(
            tmp_path,
            [
                '{"tool": "calculate", "args": {"expression": "2+2"}}',
                "El resultado es 4.",
            ],
        )
        result = agent.act("cuanto es 2+2?")
        assert [step.tool for step in result.steps] == ["calculate"]
        assert result.steps[0].ok is True
        assert result.steps[0].data["result"] == 4
        assert result.answer == "El resultado es 4."

        # the second provider call must include the tool result message
        second = provider.requests[1]
        assert any(m.role == "tool" for m in second.messages)
        tool_message = next(m for m in second.messages if m.role == "tool")
        assert '"tool": "calculate"' in tool_message.content

    def test_denied_tool_keeps_loop_alive(self, tmp_path) -> None:
        denied = PermissionSystem(
            PermissionSettings(autonomy=AutonomyLevel.full, deny=["calculate"])
        )
        provider, agent = _agent(
            tmp_path,
            [
                '{"tool": "calculate", "args": {"expression": "2+2"}}',
                "No puedo calcularlo ahora.",
            ],
            permissions=denied,
        )
        result = agent.act("calcula algo")
        assert result.steps[0].ok is False
        assert "permission denied" in result.steps[0].message
        assert result.answer == "No puedo calcularlo ahora."

    def test_unknown_tool_feeds_result_and_continues(self, tmp_path) -> None:
        provider, agent = _agent(
            tmp_path,
            [
                '{"tool": "nope", "args": {}}',
                "Eso no existe, pero da igual.",
            ],
        )
        result = agent.act("prueba")
        assert result.steps[0].tool == "nope"
        assert result.steps[0].ok is False
        assert "Unknown tool" in result.steps[0].message

    def test_max_steps_guard(self, tmp_path) -> None:
        calls = ['{"tool": "calculate", "args": {"expression": "1"}}'] * 4
        provider, agent = _agent(tmp_path, calls, max_steps=3)
        result = agent.act("repite")
        assert len(result.steps) == 3
        assert "could not finish" in result.answer

    def test_provider_error_returns_graceful_answer(self, tmp_path) -> None:
        class BoomProvider(LLMProvider):
            supports_embedding = False

            def chat(self, request):
                from nova.llm.base import NOVAProviderError

                raise NOVAProviderError("down")

            def list_models(self):
                return []

            def health(self):
                return False

            def close(self):
                pass

        agent = create_agent(
            "general",
            provider=BoomProvider(),
            runner=_runner(tmp_path),
            max_steps=2,
            history_limit=10,
        )
        result = agent.act("hola")
        assert "could not reach the model" in result.answer

    def test_injects_memory_context(self, tmp_path) -> None:
        memory = MemoryService(MemoryStore(str(tmp_path / "memory.db")))
        memory.remember("el nombre del robot es K-2SO")
        provider, agent = _agent(tmp_path, ["Entendido."], memory=memory)
        agent.act("hablame de K-2SO")
        system_messages = [m for m in provider.requests[0].messages if m.role == "system"]
        assert any("K-2SO" in m.content for m in system_messages)
        memory.close()

    def test_records_conversation_in_memory(self, tmp_path) -> None:
        memory = MemoryService(MemoryStore(str(tmp_path / "memory.db")))
        provider, agent = _agent(tmp_path, ["hola usuario"], memory=memory)
        agent.act("hola")
        hits = memory.search("hola")
        assert any(hit.kind == "user" for hit in hits)
        assert any(hit.kind == "assistant" for hit in hits)
        memory.close()

    def test_steps_serialization(self) -> None:
        step = AgentStep(tool="t", args={"a": 1}, ok=True, message="ok", data={"r": 2})
        assert step.to_dict()["tool"] == "t"
        assert json.loads(json.dumps(step.to_dict()))["args"] == {"a": 1}


class TestPresets:
    def test_five_presets(self) -> None:
        names = {preset.name for preset in agent_presets()}
        assert names == {"general", "coding", "research", "system", "automation"}

    def test_coding_agent_has_tools(self, tmp_path) -> None:
        provider = ScriptedProvider(["ok"])
        agent = create_agent(
            "coding",
            provider=provider,
            runner=_runner(tmp_path),
            max_steps=6,
            history_limit=10,
        )
        assert {tool.name for tool in agent._tools} == {
            "calculate",
            "list_dir",
            "date_time",
            "remember",
            "memory_search",
        }

    def test_unknown_preset_rejected(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            get_preset("nope")
        assert "agent" in str(excinfo.value)

    def test_description_in_presets(self, tmp_path) -> None:
        for preset in agent_presets():
            assert preset.description
            assert preset.system_prompt
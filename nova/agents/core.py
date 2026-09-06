from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from nova.core.logging import get_logger
from nova.core.session import ChatSession
from nova.llm.base import ChatCompletionRequest, ChatMessage, LLMProvider, NOVAProviderError
from nova.memory.service import MemoryService
from nova.tools.base import BaseTool
from nova.tools.runner import ToolRunner

logger = get_logger("agents.core")

_TOOL_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


@dataclass
class AgentStep:
    tool: str
    args: dict[str, Any]
    ok: bool
    message: str
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "ok": self.ok,
            "message": self.message,
            "data": self.data,
        }


@dataclass
class AgentResult:
    answer: str
    model: str
    steps: list[AgentStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "model": self.model,
            "steps": [step.to_dict() for step in self.steps],
        }


def parse_tool_call(content: str) -> tuple[str, dict[str, Any]] | None:
    """Extract a proposed tool call from the LLM output.

    The LLM signals a tool call by replying with ONLY a JSON object like
    ``{"tool": "<name>", "args": {...}}`` (optionally inside a code fence).
    Returns ``(tool, args)`` or ``None`` when the content is a final answer.
    """
    text = content.strip()
    match = _TOOL_FENCE_RE.match(text)
    if match:
        text = match.group(1).strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    tool = data.get("tool")
    args = data.get("args", {})
    if not isinstance(tool, str) or not tool:
        return None
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    return tool, args


class Agent:
    """A persona with automatic tool selection.

    The LLM **proposes** tool calls (JSON protocol); N.O.V.A. **decides** via the
    `ToolRunner` (Permission System + audit) and feeds results back until the LLM
    answers directly or the step budget runs out.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        system_prompt: str,
        tools: list[BaseTool],
        provider: LLMProvider,
        runner: ToolRunner,
        memory: MemoryService | None = None,
        session: ChatSession | None = None,
        model: str | None = None,
        max_steps: int = 4,
        history_limit: int = 20,
    ) -> None:
        self.name = name
        self.description = description
        self._provider = provider
        self._runner = runner
        self._memory = memory
        self._model = model
        self._max_steps = max_steps
        self._tools = sorted(tools, key=lambda tool: tool.name)
        combined = f"{system_prompt}\n\n# Tools\n{self._tools_prompt()}"
        self._session = session or ChatSession(
            max_history_messages=history_limit,
            system_prompt=combined,
        )
        self._session.set_system_prompt(combined)

    @property
    def session(self) -> ChatSession:
        return self._session

    @property
    def model(self) -> str | None:
        return self._model

    def _tools_prompt(self) -> str:
        lines = [
            "You can use the following tools to fulfill requests. Each tool has a JSON schema "
            "describing its parameters.",
        ]
        for tool in self._tools:
            lines.append(
                json.dumps(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.json_schema(),
                    },
                    ensure_ascii=False,
                )
            )
        lines += [
            "To call a tool, reply with ONLY a JSON object: "
            '{"tool": "<name>", "args": {<parameters>}}',
            "Do not add explanations around the JSON call.",
            "After receiving a tool result, continue with the task; when done, reply in plain text.",
        ]
        return "\n".join(lines)

    def act(self, user_input: str) -> AgentResult:
        self._session.add_user(user_input)
        if self._memory is not None:
            self._memory.record("user", user_input)

        context = self._memory.context(user_input) if self._memory is not None else ""
        steps: list[AgentStep] = []
        model_used = self._model

        for _ in range(self._max_steps):
            request = self._session.build_request(model=self._model)
            if context:
                request = _with_context(request, context)
            try:
                response = self._provider.chat(request)
            except NOVAProviderError as exc:
                answer = f"I could not reach the model: {exc}"
                logger.warning("agent %s provider error: %s", self.name, exc)
                if self._memory is not None:
                    self._memory.record("assistant", answer)
                return AgentResult(answer=answer, model=model_used or "", steps=steps)
            model_used = response.model

            content = response.message.content.strip()
            proposed = parse_tool_call(content)
            if proposed is None:
                self._session.add_assistant(content)
                if self._memory is not None:
                    self._memory.record("assistant", content)
                return AgentResult(answer=content, model=model_used, steps=steps)

            tool_name, args = proposed
            self._session.add_assistant(content)
            result = self._runner.run(tool_name, args)
            steps.append(
                AgentStep(
                    tool=tool_name,
                    args=args,
                    ok=result.ok,
                    message=result.message,
                    data=result.data,
                )
            )
            logger.info("agent %s step: tool=%s ok=%s", self.name, tool_name, result.ok)
            self._session.add_tool(json.dumps(result.to_dict(), ensure_ascii=False, default=str))
            if self._memory is not None:
                self._memory.record(
                    "assistant",
                    f"[tool {tool_name}] {result.message or ('ok' if result.ok else 'failure')}",
                )

        answer = (
            "I could not finish within the allowed number of tool steps. "
            "Try a more specific request or approve the permissions needed."
        )
        self._session.add_assistant(answer)
        if self._memory is not None:
            self._memory.record("assistant", answer)
        return AgentResult(answer=answer, model=model_used, steps=steps)


def _with_context(request: ChatCompletionRequest, context: str) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        messages=request.messages[:-1]
        + [ChatMessage(role="system", content=context)]
        + [request.messages[-1]],
        model=request.model,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )
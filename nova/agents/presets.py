from __future__ import annotations

from dataclasses import dataclass

from nova.agents.core import Agent
from nova.core.logging import get_logger
from nova.core.session import ChatSession
from nova.llm.base import LLMProvider
from nova.memory.service import MemoryService
from nova.tools.base import BaseTool
from nova.tools.runner import ToolRunner

logger = get_logger("agents.presets")

_GENERIC = (
    "You are an N.O.V.A. agent running locally on the user's machine. Be concise, precise "
    "and helpful. Use tools only when they add real value; never fabricate tool results."
)


@dataclass(frozen=True)
class AgentPreset:
    name: str
    description: str
    system_prompt: str
    tool_names: tuple[str, ...]


AGENT_PRESETS: dict[str, AgentPreset] = {
    "general": AgentPreset(
        name="general",
        description="General-purpose assistant; matches the main chat, tools on demand.",
        system_prompt=_GENERIC,
        tool_names=("*",),
    ),
    "coding": AgentPreset(
        name="coding",
        description="Coding assistant: calculations, reading directories, memory of the project.",
        system_prompt=(
            _GENERIC
            + " Specialize in software engineering: read only what is needed, compute precisely "
            "and explain solutions step by step when asked."
        ),
        tool_names=("calculate", "list_dir", "date_time", "remember", "memory_search"),
    ),
    "research": AgentPreset(
        name="research",
        description="Research assistant: gather context, keep notes in memory, answer with sources.",
        system_prompt=(
            _GENERIC
            + " Specialize in research: retrieve relevant memory, note findings with the "
            "remember tool, and answer clearly separating facts from assumptions."
        ),
        tool_names=("list_dir", "date_time", "memory_search", "remember"),
    ),
    "system": AgentPreset(
        name="system",
        description="System assistant: machine state, dates and read-only inspection.",
        system_prompt=(
            _GENERIC
            + " You operate on system-level requests. Prefer read-only inspection; never modify "
            "anything without explicit permission."
        ),
        tool_names=("date_time", "list_dir"),
    ),
    "automation": AgentPreset(
        name="automation",
        description="Automation assistant: deterministic steps and idempotent workflows.",
        system_prompt=(
            _GENERIC
            + " You plan and execute automation steps in order. Check results after each tool "
            "call and stop when the goal is reached."
        ),
        tool_names=("calculate", "date_time", "list_dir", "remember", "memory_search"),
    ),
}


def agent_presets() -> list[AgentPreset]:
    return sorted(AGENT_PRESETS.values(), key=lambda preset: preset.name)


def get_preset(name: str) -> AgentPreset:
    try:
        return AGENT_PRESETS[name]
    except KeyError:
        raise ValueError(
            f"Unknown agent: {name!r}. Available: {sorted(AGENT_PRESETS)}"
        ) from None


def _resolve_tools(preset: AgentPreset, runner: ToolRunner) -> list[BaseTool]:
    if preset.tool_names == ("*",):
        return runner.tools
    available = {tool.name: tool for tool in runner.tools}
    return [available[name] for name in preset.tool_names if name in available]


def create_agent(
    name: str,
    *,
    provider: LLMProvider,
    runner: ToolRunner,
    memory: MemoryService | None = None,
    model: str | None = None,
    max_steps: int = 4,
    history_limit: int = 20,
    session: ChatSession | None = None,
) -> Agent:
    """Build an agent from a named preset, wired to the shared Core services."""
    preset = get_preset(name)
    tools = _resolve_tools(preset, runner)
    return Agent(
        name=preset.name,
        description=preset.description,
        system_prompt=preset.system_prompt,
        tools=tools,
        provider=provider,
        runner=runner,
        memory=memory,
        model=model,
        max_steps=max_steps,
        history_limit=history_limit,
        session=session,
    )
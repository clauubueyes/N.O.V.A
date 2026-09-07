from __future__ import annotations

"""Local Desktop Agent scaffold (PHASE 6).

This process loads provider, memory, host tools and permission system — identical
to the interactive CLI but identified as a local autonomous agent process. PHASE 8
will add remote connectivity and a message bus from here without rewriting the
wiring below.
"""

import json
import sys

from nova.core.audit import AuditLog
from nova.core.config import load_settings
from nova.core.logging import get_logger, setup_logging
from nova.core.session import ChatSession
from nova.llm.registry import create_provider
from nova.llm.router import build_router
from nova.memory import MemorySearchTool, MemoryService, MemoryStore, RememberTool
from nova.plugins import load_plugin_tools
from nova.tools import ToolResult, registry as tool_registry
from nova.tools.host import all_host_tools
from nova.tools.permissions import PermissionSystem
from nova.tools.registry import create_registry
from nova.tools.runner import ToolRunner
from nova.tools.web import all_web_tools

BANNER = """\
+--------------------------------------------------------------+
| N.O.V.A. - Desktop Agent (local process)                      |
| Phase 7 - Model Router + Resource Manager | Ollama            |
| Phase 9 - Web Tools (web_search / web_fetch / web_extract)    |
+--------------------------------------------------------------+
| Type /help for commands.  Type /exit to shut down.            |
+--------------------------------------------------------------+"""

HELP = """\
Commands:
  /exit             shut down the agent
  /clear            clear conversation context (keeps system prompt)
  /models           list available Ollama models
  /model <name>     switch model for current session
  /route <text>     show which model the ModelRouter would pick (PHASE 7)
  /catalog          list the configured model catalog (PHASE 7)
  /tools            list registered tools
  /run <name> <json> run a tool (e.g. /run calculate {"expression":"2+2"})
  /run web_search   search the web (PHASE 9): /run web_search {"query":"..."}
  /run web_fetch    read a page:   /run web_fetch {"url":"https://..."}
  /run web_extract  list links:    /run web_extract {"url":"https://..."}
  /remember <text>  store a fact in persistent memory
  /memory [query]   search memories or list the most recent ones
  /help             show this help"""


def _format_result(result: ToolResult) -> str:
    if not result.ok:
        return f"[{result.tool} error] {result.message}"
    if result.data:
        pretty = json.dumps(result.data, ensure_ascii=False, default=str, indent=2)
        return f"[{result.tool} ok] {result.message}\n{pretty}"
    return f"[{result.tool} ok] {result.message or 'done'}"


def main() -> int:
    settings = load_settings()
    setup_logging(settings.logging)
    logger = get_logger("desktop.agent")

    provider = create_provider(settings.llm)
    session = ChatSession(
        max_history_messages=settings.session.max_history_messages,
        system_prompt=settings.session.system_prompt,
    )
    current_model = settings.llm.default_model
    router = build_router(settings.llm, settings.model_router)

    memory = MemoryService(
        MemoryStore(settings.memory.db_file),
        embed=provider.embed_text if provider.supports_embedding else None,
        session_id=settings.memory.session_id,
        max_context=settings.memory.max_context,
        similarity_threshold=settings.memory.similarity_threshold,
    )
    memory_tools = [RememberTool(memory), MemorySearchTool(memory)]
    host_tools = all_host_tools(settings.host)
    web_tools = all_web_tools(settings.web)

    registry = create_registry(memory_tools + host_tools + web_tools, base=tool_registry)
    load_plugin_tools(settings.plugins, registry=registry)
    runner = ToolRunner(
        registry=registry,
        permissions=PermissionSystem(settings.permissions),
        audit=AuditLog(settings.audit.file),
        confirm=lambda question: input(question).strip().lower() in ("y", "yes", "s", "si"),
    )

    print(BANNER)
    print(f"Model: {current_model}. Type /help for commands.")
    logger.info("desktop agent started, model=%s", current_model)

    while True:
        try:
            user_input = input("\nYou> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nShutting down.")
            break

        if not user_input:
            continue
        if user_input == "/exit":
            print("Shutting down.")
            break
        if user_input == "/help":
            print(HELP)
            continue
        if user_input == "/clear":
            session.clear()
            print("Context cleared.")
            continue
        if user_input == "/tools":
            for tool in registry.all():
                print(f"  {tool.name:12s} {tool.description[:60]}")
            continue
        if user_input == "/catalog":
            for role, model in router.catalog().items():
                resolved = model or f"(default: {current_model})"
                print(f"  - {role:<10} {resolved}")
            continue
        if user_input.startswith("/route"):
            text = user_input[6:].strip()
            if not text:
                print("Usage: /route <text>")
                continue
            decision = router.route_for(text)
            print(
                f"[route] {decision.task_kind} -> {decision.model} "
                f"(role={decision.role}). {decision.reason}"
            )
            continue
        if user_input == "/model":
            print(f"Current model: {current_model}")
            continue
        if user_input.startswith("/run "):
            args_str = user_input[5:].strip()
            if not args_str:
                print("Usage: /run <tool_name> {json_args}")
                continue
            parts = args_str.split(" ", 1)
            tool_name = parts[0]
            tool_args = {}
            if len(parts) == 2 and parts[1].strip():
                try:
                    tool_args = json.loads(parts[1])
                except json.JSONDecodeError as exc:
                    print(f"Invalid JSON args: {exc}")
                    continue
            result = runner.run(tool_name, tool_args)
            print(_format_result(result))
            continue
        if user_input.startswith("/remember "):
            text = user_input[10:].strip()
            result = runner.run("remember", {"text": text, "importance": 0.6})
            print(_format_result(result))
            continue
        if user_input.startswith("/memory"):
            query = user_input[7:].strip()
            if query:
                result = runner.run("memory_search", {"query": query, "max_results": 5})
            else:
                result = runner.run("memory_search", {"max_results": 5})
            print(_format_result(result))
            continue

        result = runner.run_llm(
            provider=provider,
            session=session,
            model=router.route_for(user_input).model,
            user_message=user_input,
        )
        if result.answer:
            print(f"\nAgent> {result.answer}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
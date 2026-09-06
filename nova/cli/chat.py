from __future__ import annotations

import json
import sys

from nova.core.audit import AuditLog
from nova.core.config import load_settings
from nova.core.logging import get_logger, setup_logging
from nova.core.session import ChatSession
from nova.llm.base import ChatCompletionRequest, ChatMessage, NOVAProviderError
from nova.llm.registry import create_provider
from nova.memory import MemorySearchTool, MemoryService, MemoryStore, RememberTool
from nova.memory.retriever import MemoryHit
from nova.tools import ToolResult, registry as tool_registry
from nova.tools.permissions import PermissionSystem
from nova.tools.registry import create_registry
from nova.tools.runner import ToolRunner

BANNER = """\
+--------------------------------------------------------------+
| N.O.V.A. - Neural Operations & Virtual Assistant              |
| Phase 3 - Memory (SQLite + embeddings) | local LLM via Ollama |
+--------------------------------------------------------------+"""

HELP = """\
Commands:
  /exit             quit (also Ctrl+C or Ctrl+Z)
  /clear            clear conversation context (keeps system prompt)
  /models           list available Ollama models
  /model <name>     switch model for current session
  /tools            list registered tools
  /run <name> <json> run a tool (e.g. /run calculate {"expression":"2+2"})
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


def _format_memory_hit(hit: MemoryHit) -> str:
    source = "memory" if hit.source == "memory" else "past"
    return f"  [{source}] ({hit.created_at}): {hit.content}"


def main() -> int:
    settings = load_settings()
    setup_logging(settings.logging)
    logger = get_logger("cli.chat")

    provider = create_provider(settings.llm)
    session = ChatSession(
        max_history_messages=settings.session.max_history_messages,
        system_prompt=settings.session.system_prompt,
    )
    current_model = settings.llm.default_model

    memory = MemoryService(
        MemoryStore(settings.memory.db_file),
        embed=provider.embed_text if provider.supports_embedding else None,
        session_id=settings.memory.session_id,
        max_context=settings.memory.max_context,
        similarity_threshold=settings.memory.similarity_threshold,
    )
    memory_tools = [RememberTool(memory), MemorySearchTool(memory)]

    tools_registry = create_registry(memory_tools, base=tool_registry)
    tools_runner = ToolRunner(
        registry=tools_registry,
        permissions=PermissionSystem(settings.permissions),
        audit=AuditLog(settings.audit.file),
        confirm=lambda question: input(question).strip().lower() in ("y", "yes", "s", "si"),
    )

    print(BANNER)
    print(f"Model: {current_model}. Type /help for commands.")
    logger.info("session started, model=%s, memory=%s", current_model, memory.session_id)

    try:
        while True:
            try:
                line = input("You   > ").strip()
            except EOFError:
                print("Bye.")
                break
            except KeyboardInterrupt:
                print("\nBye.")
                break
            if not line:
                continue
            if line.startswith("/"):
                parts = line[1:].strip().split()
                command = parts[0].lower() if parts else ""
                if command in ("exit", "quit"):
                    print("Bye.")
                    break
                elif command == "clear":
                    session.clear()
                    print("[context cleared]")
                elif command == "models":
                    try:
                        models = provider.list_models()
                    except NOVAProviderError as exc:
                        print(f"[nova error] {exc}")
                        continue
                    if not models:
                        print("No models. Run: ollama pull <model>")
                        continue
                    for model_info in models:
                        size_mb = model_info.size / (1024**2)
                        print(f"  - {model_info.name:<28} {size_mb:,.0f} MB")
                elif command == "model":
                    if len(parts) >= 2:
                        current_model = parts[1]
                        print(f"[model -> {current_model}]")
                    else:
                        print(f"Current model: {current_model}")
                elif command == "tools":
                    for tool in tools_registry.all():
                        print(f"  - {tool.name:<14} {tool.description}")
                elif command == "remember":
                    text = " ".join(parts[1:]).strip()
                    if not text:
                        print("Usage: /remember <text>")
                        continue
                    memory_id = memory.remember(text, source="user")
                    print(f"[stored as memory #{memory_id}]")
                elif command == "memory":
                    query = " ".join(parts[1:]).strip()
                    if query:
                        hits = memory.search(query)
                        if not hits:
                            print("[no memories found]")
                            continue
                        print(f"[{len(hits)} results for '{query}']")
                        for hit in hits:
                            print(_format_memory_hit(hit))
                    else:
                        for record in memory.recent_memories(limit=10):
                            print(f"  [memory #{record.id}] ({record.created_at}): {record.content}")
                elif command == "run":
                    if len(parts) < 2:
                        print("Usage: /run <tool> <json args>   e.g. /run calculate {\"expression\":\"2+2\"}")
                        continue
                    tool_name = parts[1]
                    raw_args = line.split(None, 2)
                    args: dict = {}
                    if len(raw_args) >= 3:
                        try:
                            args = json.loads(raw_args[2])
                        except json.JSONDecodeError as exc:
                            print(f"[parse error] {exc}")
                            continue
                        if not isinstance(args, dict):
                            print("[parse error] arguments must be a JSON object")
                            continue
                    result = tools_runner.run(tool_name, args)
                    print(_format_result(result))
                elif command == "help":
                    print(HELP)
                else:
                    print(f"Unknown command: {command}. Type /help.")
                continue

            session.add_user(line)
            memory.record("user", line)
            logger.info("user: %s", line)
            try:
                request = session.build_request(model=current_model)
                context = memory.context(line)
                if context:
                    request = ChatCompletionRequest(
                        messages=request.messages[:-1]
                        + [ChatMessage(role="system", content=context)]
                        + [request.messages[-1]],
                        model=request.model,
                    )
                    logger.debug("injected memory context:\n%s", context)
                response = provider.chat(request)
            except NOVAProviderError as exc:
                print(f"[nova error] {exc}")
                logger.warning("nova error: %s", exc)
                continue
            answer = response.message.content
            session.add_assistant(answer)
            memory.record("assistant", answer)
            print(f"N.O.V.A> {answer}")
            logger.info("assistant (model=%s): %s", response.model, answer)
    finally:
        provider.close()
        memory.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
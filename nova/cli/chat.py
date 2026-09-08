from __future__ import annotations

import json
import sys

from nova.agents import Agent, agent_presets, create_agent
from nova.automation import AutomationExecutor, Scheduler
from nova.core.audit import AuditLog
from nova.core.config import load_settings
from nova.core.logging import get_logger, setup_logging
from nova.core.session import ChatSession
from nova.llm.base import ChatCompletionRequest, ChatMessage, NOVAProviderError
from nova.llm.registry import create_provider
from nova.llm.router import build_router
from nova.memory import MemorySearchTool, MemoryService, MemoryStore, RememberTool
from nova.memory.retriever import MemoryHit
from nova.plugins import load_plugin_tools
from nova.tools import ToolResult, registry as tool_registry
from nova.tools.host import all_host_tools
from nova.tools.permissions import PermissionSystem
from nova.tools.registry import create_registry
from nova.tools.runner import ToolRunner
from nova.tools.web import all_web_tools
from nova.voice import VoiceSession, build_voice

BANNER = """\
+--------------------------------------------------------------+
| N.O.V.A. - Neural Operations & Virtual Assistant              |
| Phase 6 - Desktop Agent (host: open_app/open_url/run + files) | Ollama |
| Phase 7 - Model Router + Resource Manager                    |
| Phase 9 - Web Tools (web_search / web_fetch / web_extract)    |
| Phase 10 - Voice (STT/Vosk + TTS/pyttsx3, local)              |
| Phase 11 - Plugins (text_tools / units, PHASE 11)             |
| Phase 12 - Automation (scheduler + workflows, PHASE 12)       |
| Phase 13 - Installer (nova setup / doctor / autostart)        |
+--------------------------------------------------------------+"""

HELP = """\
Commands:
  /exit             quit (also Ctrl+C or Ctrl+Z)
  /clear            clear conversation context (keeps system prompt)
  /models           list available Ollama models
  /model <name>     switch model for current session
  /route <text>     show which model the ModelRouter would pick (PHASE 7)
  /catalog          list the configured model catalog (PHASE 7)
  /tools            list registered tools
  /plugins          list loaded plugins and their tools (PHASE 11)
  /run <name> <json> run a tool (e.g. /run calculate {"expression":"2+2"})
  /run open_app     launch a configured application (e.g. /run open_app {"app":"notepad"})
  /run open_url     open a URL in the browser (e.g. /run open_url {"url":"https://example.com"})
  /run run          execute an allowlisted command (e.g. /run run {"command":"echo","args":["hi"]})
  /run read_file    read a file inside host.roots (e.g. /run read_file {"path":"..."})
  /run write_file   write a file inside host.roots (parent dir must exist)
  /run list_files   list a directory inside host.roots (e.g. /run list_files {"path":"..."})
  /run web_search   search the web (PHASE 9): /run web_search {"query":"..."}
  /run web_fetch    read a page:   /run web_fetch {"url":"https://..."}
  /run web_extract  list links:    /run web_extract {"url":"https://..."}
  /voice            start the voice loop (STT -> chat -> TTS, PHASE 10)
  /voice stop       stop the voice loop  (also type /voice while listening)
  /say <text>       speak a line with the local TTS
  /remember <text>  store a fact in persistent memory
  /memory [query]   search memories or list the most recent ones
  /agents           list available agents
  /agent <name> <text> run a text through an agent (tools proposed by the LLM)
  /workflow <name>  run a configured automation workflow (PHASE 12)
  /workflows        list configured automation workflows (PHASE 12)
  /automation       scheduler status + scheduled tasks (PHASE 12)
  /setup            run the guided first-run installer (detect machine, pull models)
  /doctor           print a diagnostic summary (Ollama, models, packages)
  /status           alias of /doctor
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


def _format_agent_result(result) -> str:
    lines = []
    for step in result.steps:
        status = "ok" if step.ok else "error"
        detail = step.message or ("done" if step.ok else "failure")
        lines.append(f"  [step {status}] {step.tool}({json.dumps(step.args, ensure_ascii=False)}) -> {detail}")
    lines.append(f"{result.answer}")
    return "\n".join(lines)


def _format_workflow_result(result) -> str:
    if result is None:
        return "[workflow error] unknown workflow"
    lines = []
    for step in result.steps:
        status = "ok" if step.ok else "error"
        detail = step.message or ("done" if step.ok else "failure")
        lines.append(f"  [step {status}] {step.kind} {step.name} -> {detail}")
    lines.append(f"[workflow {result.workflow}] {'ok' if result.ok else 'failed'}: {result.message}")
    return "\n".join(lines)


def _hint_setup(provider, settings) -> None:
    """Best-effort first-run hint; never raises or blocks the chat session."""
    from nova.llm.base import NOVAProviderError

    try:
        models = provider.list_models()
    except NOVAProviderError:
        models = []
    if not models:
        print(
            "\n[setup hint] No local models found. Run `nova setup` to detect your "
            "machine, install Ollama models and write config.yaml in one go."
        )


def main(argv: list[str] | None = None) -> int:
    # `nova setup ...` / `nova doctor ...` dispatch to the installer CLI.
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in ("setup", "doctor"):
        from nova.setup.cli import main as setup_main

        return setup_main(argv[1:] if argv[0] == "setup" else argv)

    settings = load_settings()
    setup_logging(settings.logging)
    logger = get_logger("cli.chat")

    if settings.llm.provider == "ollama":
        from nova.setup.detect import ensure_ollama_running

        st = ensure_ollama_running()
        logger.info("ollama check: %s (started_now=%s)", st.message, st.started_now)
        if st.started_now:
            print("Ollama no estaba activo: lo he arrancado en segundo plano.")
        elif st.installed and not st.running:
            print("Aviso: Ollama no responde. Arrancalo con `ollama serve`.")

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

    tools_registry = create_registry(memory_tools + host_tools + web_tools, base=tool_registry)
    plugin_infos = load_plugin_tools(settings.plugins, registry=tools_registry)
    tools_runner = ToolRunner(
        registry=tools_registry,
        permissions=PermissionSystem(settings.permissions),
        audit=AuditLog(settings.audit.file),
        confirm=lambda question: input(question).strip().lower() in ("y", "yes", "s", "si"),
    )

    print(BANNER)
    print(f"Model: {current_model}. Try /agents or /help for commands.")
    logger.info("session started, model=%s, memory=%s", current_model, memory.session_id)

    # Friendly first-run hint: if Ollama is unreachable or has no models, point
    # the user at the one-command installer instead of failing mid-conversation.
    _hint_setup(provider, settings)

    agents: dict[str, Agent] = {}

    def get_agent(name: str, model: str | None = None) -> Agent | None:
        preset_names = {preset.name for preset in agent_presets()}
        if name not in preset_names:
            return None
        agent = agents.get(name)
        if agent is None:
            agent = create_agent(
                name,
                provider=provider,
                runner=tools_runner,
                memory=memory,
                model=model or current_model,
            )
            agents[name] = agent
        return agent

    voice = build_voice(settings.voice)
    if voice is not None and voice.available():
        voice.say(
            "Bienvenido, señor. Todos los sistemas están operativos. "
            "¿En qué puedo ayudarle hoy?"
        )

    # PHASE 12 — Automation. Dedicated runner WITHOUT interactive confirm: a
    # scheduled task has no human to ask, so autonomy=ask tools are DENIED
    # (add them to permissions.allow to automate them). Everything still goes
    # through the Permission System + audit.
    automation_runner = ToolRunner(
        registry=tools_registry,
        permissions=PermissionSystem(settings.permissions),
        audit=AuditLog(settings.audit.file),
    )
    automation_workflows = {wf.name: wf for wf in settings.automation.workflows}
    automation_agents: dict[str, Agent] = {}

    def get_automation_agent(name: str, model: str | None = None) -> Agent | None:
        preset_names = {preset.name for preset in agent_presets()}
        if name not in preset_names:
            return None
        agent = automation_agents.get(name)
        if agent is None:
            agent = create_agent(
                name,
                provider=provider,
                runner=automation_runner,
                memory=memory,
                model=model or current_model,
            )
            automation_agents[name] = agent
        return agent

    automation_executor = AutomationExecutor(
        automation_runner,
        get_agent=get_automation_agent,
        workflows=automation_workflows,
    )
    scheduler = Scheduler(
        settings.automation.tasks,
        poll_s=settings.automation.poll_s,
    )
    if settings.automation.enabled:
        scheduler.start(automation_executor.run_task)
        logger.info(
            "automation enabled: scheduler running with %d task(s)",
            len(scheduler.task_names),
        )

    def chat_line(text: str) -> str | None:
        """Run one user utterance through the chat state and return the
        assistant answer (printed and stored). Shared by typed and voice input."""
        session.add_user(text)
        memory.record("user", text)
        logger.info("user: %s", text)
        try:
            decision = router.route_for(text)
            request = session.build_request(model=decision.model)
            context = memory.context(text)
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
            return None
        answer = response.message.content
        session.add_assistant(answer)
        memory.record("assistant", answer)
        print(f"N.O.V.A> {answer}")
        logger.info("assistant (model=%s): %s", response.model, answer)
        return answer

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
                elif command == "catalog":
                    for role, model in router.catalog().items():
                        resolved = model or "(default)"
                        print(f"  - {role:<10} {resolved}")
                elif command == "route":
                    text = " ".join(parts[1:]).strip()
                    if not text:
                        print("Usage: /route <text>")
                        continue
                    decision = router.route_for(text)
                    print(
                        f"[route] {decision.task_kind} -> {decision.model} "
                        f"(role={decision.role}). {decision.reason}"
                    )
                elif command == "tools":
                    for tool in tools_registry.all():
                        print(f"  - {tool.name:<14} {tool.description}")
                elif command == "plugins":
                    if not plugin_infos:
                        print("No plugins loaded. Enable them in config/config.yaml -> plugins.")
                        continue
                    for info in plugin_infos:
                        print(f"  - {info.name:<14} {info.description}")
                        for tool_name in info.tools:
                            print(f"       {tool_name}")
                elif command == "automation":
                    if not settings.automation.enabled:
                        print("[automation disabled] enable it in config/config.yaml -> automation.")
                        continue
                    print(f"Scheduler: running={scheduler.running} (poll {settings.automation.poll_s}s)")
                    if not settings.automation.tasks:
                        print("  no scheduled tasks configured")
                    for row in scheduler.status():
                        print(
                            f"  - {row['task']:<20} next={row['next_run']} "
                            f"(in {row['seconds_until']:.0f}s, last ok={row['last_ok']})"
                        )
                elif command == "workflows":
                    if not automation_workflows:
                        print("[no workflows configured] add them in config/config.yaml -> automation.")
                        continue
                    for wf_name, wf in automation_workflows.items():
                        print(f"  - {wf_name:<20} {wf.description or wf.name} ({len(wf.steps)} steps)")
                elif command == "workflow":
                    if len(parts) < 2:
                        print("Usage: /workflow <name>")
                        continue
                    if parts[1] not in automation_workflows:
                        print(f"Unknown workflow '{parts[1]}'. See /workflows.")
                        continue
                    logger.info("workflow %s started", parts[1])
                    print(_format_workflow_result(automation_executor.run_workflow(parts[1])))
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
                elif command == "agents":
                    for preset in agent_presets():
                        print(f"  - {preset.name:<12} {preset.description}")
                elif command == "agent":
                    if len(parts) < 2:
                        print("Usage: /agent <name> <text>   e.g. /agent coding modulo de filtros")
                        print("  /agents for the list of names")
                        continue
                    name = parts[1]
                    text = " ".join(parts[2:]).strip()
                    if not text:
                        print("Usage: /agent <name> <text>")
                        continue
                    agent = get_agent(name, router.route_for(text).model)
                    if agent is None:
                        print(f"Skipping: unknown agent '{name}'. See /agents.")
                        continue
                    logger.info("agent %s: %s", name, text)
                    try:
                        result = agent.act(text)
                    except NOVAProviderError as exc:
                        print(f"[nova error] {exc}")
                        logger.warning("agent error: %s", exc)
                        continue
                    print(f"{name.capitalize()} > {_format_agent_result(result)}")
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
                elif command == "say":
                    text = " ".join(parts[1:]).strip()
                    if voice is None:
                        print("[voice not configured] enable voice.enabled in config/config.yaml")
                        continue
                    if not text:
                        print("Usage: /say <text>")
                        continue
                    print(f"[tts] {text}" if voice.say(text) else "[tts error]")
                elif command == "voice":
                    if voice is None:
                        print("[voice not configured] enable voice.enabled in config/config.yaml")
                        continue
                    missing = voice.status()
                    if missing:
                        print("[voice not available] missing: " + ", ".join(missing))
                        continue
                    print("[voice mode] speak to N.O.V.A. Press ENTER when done or type /voice stop.")
                    stop_voice = False
                    typed: list[str] = []

                    def wait_utterance_end() -> bool:
                        try:
                            typed.append(
                                input("[voice] speak now, press ENTER when you finish... ").strip()
                            )
                        except (EOFError, KeyboardInterrupt):
                            pass
                        return True

                    try:
                        while not stop_voice:
                            transcript = voice.listen_once(wait_fn=wait_utterance_end)
                            last = typed[-1] if typed else ""
                            if last.lower() in ("/voice", "/voice stop", "stop", "/exit"):
                                stop_voice = True
                                continue
                            if transcript is None:
                                print("[voice] (no speech detected)")
                                continue
                            if transcript == "":
                                print(f"[voice] ignored (wake word '{voice.wake_word}' not present)")
                                continue
                            print(f"You   > [voice] {transcript}")
                            answer = chat_line(transcript)
                            if answer:
                                voice.say(answer)
                    except KeyboardInterrupt:
                        pass
                    print("[voice mode] stopped.")
                elif command in ("setup", "install"):
                    print("[setup] run from the shell: `nova setup` (interactive) or `nova setup --help`.")
                    from nova.setup.detect import detect_machine, detect_ollama

                    profile = detect_machine()
                    print("Machine:")
                    for line in profile.summary():
                        print(f"  {line}")
                    st = detect_ollama()
                    print(f"Ollama: {st.message}")
                    continue
                elif command in ("doctor", "status"):
                    from nova.setup.cli import _do_doctor

                    _do_doctor()
                    continue
                elif command == "help":
                    print(HELP)
                else:
                    print(f"Unknown command: {command}. Type /help.")
                continue

            chat_line(line)
    finally:
        scheduler.stop()
        provider.close()
        memory.close()
        if voice is not None:
            voice.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
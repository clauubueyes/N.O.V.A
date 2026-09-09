from __future__ import annotations

import json
import sys

from nova.agents import Agent, agent_presets, create_agent
from nova.automation import AutomationExecutor, Scheduler
from nova.core.audit import AuditLog
from nova.core.config import AIMode, PrivacyPolicy, load_settings
from nova.core.health import check_ollama, check_opencode, gather_system_info
from nova.core.logging import get_logger, setup_logging
from nova.core.session import ChatSession
from nova.llm.base import ChatCompletionRequest, ChatMessage, NOVAProviderError
from nova.llm.registry import create_provider
from nova.llm.router import RoutingDecision, build_router
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

from nova.cli import ui


HELP_TEXT = """\
  /exit             quit (also Ctrl+C or Ctrl+Z)
  /clear            clear conversation context
  /models           list available Ollama models
  /model <name>     switch model for current session
  /route <text>     show which model the router would pick
  /catalog          list the configured model catalog
  /tools            list registered tools
  /plugins          list loaded plugins and their tools
  /run <tool> <json> run a tool
  /voice            start the voice loop (STT -> chat -> TTS)
  /voice stop       stop the voice loop
  /say <text>       speak a line with the local TTS
  /remember <text>  store a fact in persistent memory
  /memory [query]   search memories or list recent ones
  /agents           list available agents
  /agent <name> <text> run a text through an agent
  /workflow <name>  run a configured automation workflow
  /workflows        list configured automation workflows
  /automation       scheduler status + scheduled tasks
  /setup            run the guided first-run installer
  /doctor           print a diagnostic summary
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


def _parse_args(argv: list[str]) -> tuple[list[str], bool]:
    """Separate --debug from the rest of argv."""
    debug = False
    clean: list[str] = []
    for arg in argv:
        if arg == "--debug":
            debug = True
        else:
            clean.append(arg)
    return clean, debug


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    argv, debug = _parse_args(argv)

    if argv and argv[0] in ("setup", "doctor"):
        from nova.setup.cli import main as setup_main
        return setup_main(argv[1:] if argv[0] == "setup" else argv)

    settings = load_settings()
    setup_logging(settings.logging, debug=debug)
    logger = get_logger("cli.chat")

    ollama_status = None
    if settings.llm.provider == "ollama":
        from nova.setup.detect import ensure_ollama_running
        ollama_status = ensure_ollama_running()
        logger.info("ollama check: %s (started_now=%s)", ollama_status.message, ollama_status.started_now)

    provider = create_provider(settings.llm)

    cloud_provider = None
    if settings.ai.mode == AIMode.hybrid and settings.open_code.enabled:
        from nova.llm.opencode import OpenCodeProvider
        cloud_provider = OpenCodeProvider(settings=settings.open_code)
        if cloud_provider.health():
            logger.info("opencode cloud provider: connected")
        else:
            logger.info("opencode cloud provider: not reachable (local-only fallback)")
            cloud_provider = None

    installed_models: list[str] = []
    try:
        installed_models = [m.name for m in provider.list_models()]
    except NOVAProviderError:
        pass

    router = build_router(
        settings.llm,
        settings.model_router,
        ai_mode=settings.ai.mode,
        ai_privacy=settings.ai.privacy,
        open_code=settings.open_code,
    )

    validation_warnings = router.validate_against(installed_models)
    for w in validation_warnings:
        logger.warning("catalog: %s", w)

    active_model = router.default_model
    cat = router.catalog()
    if cat.get("local"):
        active_model = cat["local"]
    elif cat.get("small"):
        active_model = cat["small"]

    if not installed_models:
        ui.print_warning(
            "No local models found. Run `nova setup` to install models."
        )

    system_info = gather_system_info()
    ollama_svc = check_ollama(settings.llm.base_url)
    opencode_svc = check_opencode() if settings.open_code.enabled else None
    model_provider = "local"

    ui.print_welcome(
        system=system_info,
        ollama=ollama_svc,
        opencode=opencode_svc,
        model_name=active_model,
        model_provider=model_provider,
        warnings=validation_warnings,
    )

    session = ChatSession(
        max_history_messages=settings.session.max_history_messages,
        system_prompt=settings.session.system_prompt,
    )
    current_model = active_model

    logger.info("session started, model=%s, memory=%s", current_model, settings.memory.session_id)

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
            "Bienvenido, senor. Todos los sistemas estan operativos. "
            "En que puedo ayudarle hoy?"
        )

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
        session.add_user(text)
        memory.record("user", text)
        logger.info("user: %s", text)
        try:
            decision = router.route_for(text)
            active_provider = provider
            if decision.provider == "opencode" and cloud_provider is not None:
                active_provider = cloud_provider
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

            provider_label = "ollama" if decision.provider == "ollama" else decision.provider
            if provider_label == "ollama":
                spinner_msg = "Generating response..."
            else:
                spinner_msg = "Processing request..."

            with ui.ThinkingIndicator(spinner_msg):
                response = active_provider.chat(request)

        except NOVAProviderError as exc:
            if active_provider is not provider:
                logger.warning("cloud provider failed (%s); falling back to local", exc)
                try:
                    with ui.ThinkingIndicator("Falling back to local..."):
                        response = provider.chat(request)
                    decision = RoutingDecision(
                        task_kind=decision.task_kind,
                        model=request.model,
                        role=decision.role,
                        reason=f"cloud failed ({exc}); fell back to local ({request.model})",
                        provider="ollama",
                    )
                except NOVAProviderError as local_exc:
                    logger.warning("local fallback failed: %s", local_exc)
                    _handle_chat_error(local_exc, decision.model, installed_models)
                    return None
            else:
                logger.warning("nova error: %s", exc)
                _handle_chat_error(exc, decision.model, installed_models)
                return None

        answer = response.message.content
        session.add_assistant(answer)
        memory.record("assistant", answer)
        ui.print_assistant_message(answer, model=response.model, provider=decision.provider)
        logger.info(
            "assistant (provider=%s, model=%s): %s",
            decision.provider, response.model, answer,
        )
        return answer

    def _handle_chat_error(exc: NOVAProviderError, model_used: str, installed: list[str]) -> None:
        exc_str = str(exc)
        if "404" in exc_str or "not found" in exc_str.lower():
            suggestions = []
            if installed:
                suggestions.append(f"Available models: {', '.join(installed[:5])}")
            suggestions.append("Install the model: ollama pull <model-name>")
            suggestions.append("Or switch model: /model <name>")
            ui.print_error(
                "Model Error",
                f'Model "{model_used}" is not available.',
                suggestions=suggestions,
            )
        else:
            ui.print_error("Provider Error", exc_str)

    try:
        while True:
            try:
                line = input("\033[1;36mYou\033[0m > ").strip()
            except EOFError:
                ui.print_info("Bye.")
                break
            except KeyboardInterrupt:
                print()
                ui.print_info("Bye.")
                break
            if not line:
                continue
            if line.startswith("/"):
                parts = line[1:].strip().split()
                command = parts[0].lower() if parts else ""
                if command in ("exit", "quit"):
                    ui.print_info("Bye.")
                    break
                elif command == "clear":
                    session.clear()
                    ui.print_info("Context cleared.")
                elif command == "models":
                    try:
                        models = provider.list_models()
                    except NOVAProviderError as exc:
                        ui.print_error("Error", str(exc))
                        continue
                    if not models:
                        ui.print_warning("No models. Run: ollama pull <model>")
                        continue
                    model_list = [(m.name, m.size / (1024**2)) for m in models]
                    ui.print_model_list(model_list, active_model=current_model)
                elif command == "model":
                    if len(parts) >= 2:
                        current_model = parts[1]
                        ui.print_info(f"Model switched to: {current_model}")
                    else:
                        ui.print_info(f"Current model: {current_model}")
                elif command == "catalog":
                    ui.print_command_header("Model Catalog")
                    cat = router.catalog()
                    for role, model in cat.items():
                        resolved = model or "(default)"
                        ui.print_info(f"  {role:<12} {resolved}")
                elif command == "route":
                    text = " ".join(parts[1:]).strip()
                    if not text:
                        ui.print_warning("Usage: /route <text>")
                        continue
                    decision = router.route_for(text)
                    ui.print_info(
                        f"{decision.task_kind} -> {decision.model} "
                        f"(provider={decision.provider}, role={decision.role})"
                    )
                    ui.print_info(f"  Reason: {decision.reason}")
                elif command == "tools":
                    ui.print_command_header("Registered Tools")
                    for tool in tools_registry.all():
                        ui.print_info(f"  {tool.name:<14} {tool.description}")
                elif command == "plugins":
                    if not plugin_infos:
                        ui.print_warning("No plugins loaded. Enable in config.yaml -> plugins.")
                        continue
                    ui.print_command_header("Plugins")
                    for info in plugin_infos:
                        ui.print_info(f"  {info.name:<14} {info.description}")
                        for tool_name in info.tools:
                            ui.print_info(f"       {tool_name}")
                elif command == "automation":
                    if not settings.automation.enabled:
                        ui.print_warning("Automation disabled. Enable in config.yaml -> automation.")
                        continue
                    ui.print_command_header("Automation Scheduler")
                    ui.print_info(f"Running: {scheduler.running} (poll {settings.automation.poll_s}s)")
                    if not settings.automation.tasks:
                        ui.print_info("  No scheduled tasks configured.")
                    for row in scheduler.status():
                        ui.print_info(
                            f"  {row['task']:<20} next={row['next_run']} "
                            f"(in {row['seconds_until']:.0f}s, last ok={row['last_ok']})"
                        )
                elif command == "workflows":
                    if not automation_workflows:
                        ui.print_warning("No workflows configured. Add in config.yaml -> automation.")
                        continue
                    ui.print_command_header("Workflows")
                    for wf_name, wf in automation_workflows.items():
                        ui.print_info(f"  {wf_name:<20} {wf.description or wf.name} ({len(wf.steps)} steps)")
                elif command == "workflow":
                    if len(parts) < 2:
                        ui.print_warning("Usage: /workflow <name>")
                        continue
                    if parts[1] not in automation_workflows:
                        ui.print_warning(f"Unknown workflow '{parts[1]}'. See /workflows.")
                        continue
                    logger.info("workflow %s started", parts[1])
                    print(_format_workflow_result(automation_executor.run_workflow(parts[1])))
                elif command == "remember":
                    text = " ".join(parts[1:]).strip()
                    if not text:
                        ui.print_warning("Usage: /remember <text>")
                        continue
                    memory_id = memory.remember(text, source="user")
                    ui.print_info(f"Stored as memory #{memory_id}.")
                elif command == "memory":
                    query = " ".join(parts[1:]).strip()
                    if query:
                        hits = memory.search(query)
                        if not hits:
                            ui.print_info("No memories found.")
                            continue
                        ui.print_command_header(f"{len(hits)} results for '{query}'")
                        for hit in hits:
                            print(_format_memory_hit(hit))
                    else:
                        ui.print_command_header("Recent Memories")
                        for record in memory.recent_memories(limit=10):
                            ui.print_info(f"  #{record.id} ({record.created_at}): {record.content}")
                elif command == "agents":
                    ui.print_command_header("Agents")
                    for preset in agent_presets():
                        ui.print_info(f"  {preset.name:<12} {preset.description}")
                elif command == "agent":
                    if len(parts) < 2:
                        ui.print_warning("Usage: /agent <name> <text>")
                        ui.print_info("  /agents for the list of names")
                        continue
                    name = parts[1]
                    text = " ".join(parts[2:]).strip()
                    if not text:
                        ui.print_warning("Usage: /agent <name> <text>")
                        continue
                    agent = get_agent(name, router.route_for(text).model)
                    if agent is None:
                        ui.print_warning(f"Unknown agent '{name}'. See /agents.")
                        continue
                    logger.info("agent %s: %s", name, text)
                    try:
                        with ui.ThinkingIndicator(f"Running agent {name}..."):
                            result = agent.act(text)
                    except NOVAProviderError as exc:
                        ui.print_error("Agent Error", str(exc))
                        logger.warning("agent error: %s", exc)
                        continue
                    print(_format_agent_result(result))
                elif command == "run":
                    if len(parts) < 2:
                        ui.print_warning('Usage: /run <tool> <json>   e.g. /run calculate {"expression":"2+2"}')
                        continue
                    tool_name = parts[1]
                    raw_args = line.split(None, 2)
                    args: dict = {}
                    if len(raw_args) >= 3:
                        try:
                            args = json.loads(raw_args[2])
                        except json.JSONDecodeError as exc:
                            ui.print_error("Parse Error", str(exc))
                            continue
                        if not isinstance(args, dict):
                            ui.print_error("Parse Error", "Arguments must be a JSON object.")
                            continue
                    with ui.ThinkingIndicator(f"Running {tool_name}..."):
                        result = tools_runner.run(tool_name, args)
                    print(_format_result(result))
                elif command == "say":
                    text = " ".join(parts[1:]).strip()
                    if voice is None:
                        ui.print_warning("Voice not configured. Enable voice.enabled in config.yaml.")
                        continue
                    if not text:
                        ui.print_warning("Usage: /say <text>")
                        continue
                    if voice.say(text):
                        ui.print_info(f"TTS: {text}")
                    else:
                        ui.print_error("TTS Error", "Failed to speak text.")
                elif command == "voice":
                    if voice is None:
                        ui.print_warning("Voice not configured. Enable voice.enabled in config.yaml.")
                        continue
                    missing = voice.status()
                    if missing:
                        ui.print_error("Voice Unavailable", f"Missing: {', '.join(missing)}")
                        continue
                    ui.print_info("Voice mode active. Speak to N.O.V.A. Press ENTER or type /voice stop.")
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
                                ui.print_info("(no speech detected)")
                                continue
                            if transcript == "":
                                ui.print_info(f"Ignored (wake word '{voice.wake_word}' not present)")
                                continue
                            ui.print_user_message(f"[voice] {transcript}")
                            answer = chat_line(transcript)
                            if answer:
                                voice.say(answer)
                    except KeyboardInterrupt:
                        pass
                    ui.print_info("Voice mode stopped.")
                elif command in ("setup", "install"):
                    ui.print_info("Run from shell: `nova setup` (interactive) or `nova setup --help`.")
                    from nova.setup.detect import detect_machine, detect_ollama
                    profile = detect_machine()
                    ui.print_command_header("Machine")
                    for ln in profile.summary():
                        ui.print_info(f"  {ln}")
                    st = detect_ollama()
                    ui.print_info(f"Ollama: {st.message}")
                elif command in ("doctor", "status"):
                    from nova.setup.cli import _do_doctor
                    _do_doctor()
                elif command == "help":
                    ui.print_command_header("Commands")
                    print(HELP_TEXT)
                else:
                    ui.print_warning(f"Unknown command: {command}. Type /help.")
                continue

            chat_line(line)
    finally:
        scheduler.stop()
        provider.close()
        if cloud_provider is not None:
            cloud_provider.close()
        memory.close()
        if voice is not None:
            voice.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

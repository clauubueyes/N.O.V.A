from __future__ import annotations

"""Guided "JARVIS" first-run assistant.

Walks a new user through the whole bootstrap in visible phases: system check,
dependency check/install/verification, hardware, Ollama, OpenCode (optional),
model selection/install, configuration and a final ready summary. Uses the
pretty `Terminal` layer with a plain-text fallback and — when the local voice
stack is installed and configured — can greet and speak through N.O.V.A. itself
(ADR-017: the audio never leaves the device).
"""

from pathlib import Path

from nova.setup.detect import (
    MachineProfile,
    detect_machine,
    detect_ollama,
    detect_opencode,
)
from nova.setup.models import default_model_for, missing_models
from nova.setup.provision import autoconfigure
from nova.setup.state import exclusive
from nova.setup.terminal import Terminal

CONFIG_DEFAULT = Path("config/config.yaml")


def _say(text: str) -> None:
    """Best-effort spoken line via the local TTS (never raises).

    Uses pyttsx3 directly so the wizard can greet even before the full voice
    pipeline (mic + Vosk) is configured. Falls back silently when unavailable.
    """
    try:
        import pyttsx3

        engine = pyttsx3.init()
        try:
            engine.say(text)
            engine.runAndWait()
        finally:
            engine.stop()
    except Exception:  # noqa: BLE001 - a greeting must never block setup
        pass


def _ask(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    raw = input(prompt + suffix).strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "s", "si")


def _ask_ai_mode() -> str:
    """Let the user pick the AI mode: local, hybrid or configure later.

    Never offers OpenCode as a requirement: N.O.V.A. never installs it and
    hybrid stays optional. Hybrid is only suggested when OpenCode is detected,
    so the user never buys into an unverifiable setup.
    """
    print(
        "\nAI mode:\n"
        "  [1] Local  - Ollama only, fully offline (recommended default)\n"
        "  [2] Hybrid - Ollama first, OpenCode cloud fallback (optional)\n"
        "  [3] Configure later"
    )
    selection = input("Select [1]: ").strip().lower()
    if selection in ("2", "hybrid", "h", "mixto"):
        return "hybrid"
    if selection in ("3", "later", "l", "despues", "después"):
        return "configure_later"
    return "local"


def _ollama_serve(profile: MachineProfile, term: Terminal | None = None) -> bool:
    """Start Ollama in the background (best-effort) and return success."""
    out = term or Terminal(interactive=False)
    out.arrow("Starting Ollama...")
    from nova.setup.detect import ensure_ollama_running

    return ensure_ollama_running().running


@exclusive
def _pull_models(
    profile: MachineProfile,
    config_path: str | Path | None = None,
    term: Terminal | None = None,
) -> list[str]:
    import httpx

    from nova.setup.detect import detect_ollama
    from nova.setup.models import normalize_model_name
    from nova.setup.selector import fallback_candidate, plan_lines, select_stack

    from nova.core.config import load_settings
    from nova.setup.state import StateStore

    out = term or Terminal(interactive=False)
    base = load_settings(str(config_path) if config_path else None).llm.base_url
    store = StateStore()
    store.load()
    from nova.setup.ollama_lifecycle import local_endpoint, snapshot

    st = snapshot(base)
    if not local_endpoint(base) or not st.running:
        out.warn("Ollama is not running; models not pulled.")
        return []
    installed = set(st.models)
    for name in st.models:
        store.record_model(name, "preexisting", base, owned=False)

    out.section("Model Selection")
    stack = select_stack(profile, check_disk=True)
    for line in plan_lines(profile):
        out.sub(line)

    targets = [c for c in stack if c.install and normalize_model_name(c.spec.name) not in installed]
    pulled: list[str] = []
    if not targets:
        out.ok("All recommended models already present.")
        return pulled

    try:
        with httpx.Client(base_url=base, timeout=3600) as client:
            for choice in targets:
                name = choice.spec.name
                if normalize_model_name(name) in installed:
                    out.ok(f"{name} already installed")
                    pulled.append(name)
                    continue
                out.arrow(f"Pulling {name} ({choice.spec.description})...")
                if _pull_one(client, name):
                    store.record_model(name, choice.kind, base, owned=True)
                    digest = snapshot(base).models.get(normalize_model_name(name), "")
                    store.record_model(name, choice.kind, base, owned=True, digest=digest)
                    installed.add(normalize_model_name(name))
                    pulled.append(name)
                    out.ok(f"{name} ready")
                    continue
                # Fallback chain: next compatible candidate, then a smaller one.
                fb = fallback_candidate(choice.kind, name)
                from nova.setup.selector import Availability, capability_profile, evaluate

                if fb and evaluate(fb, capability_profile(profile))[0] is Availability.NOT_RECOMMENDED:
                    fb = None
                if fb and fb.name != name:
                    if normalize_model_name(fb.name) in installed:
                        pulled.append(fb.name)
                        continue
                    out.arrow(f"Trying smaller fallback {fb.name} instead...")
                    if _pull_one(client, fb.name):
                        store.record_model(fb.name, choice.kind, base, owned=True)
                        installed.add(normalize_model_name(fb.name))
                        pulled.append(fb.name)
                        out.ok(f"{fb.name} ready")
                    else:
                        out.fail(f"{fb.name} failed too; skipping this optional role.")
                else:
                    out.fail(f"{name} failed and no smaller fallback exists.")
    except Exception as exc:  # noqa: BLE001
        out.fail(f"Could not reach Ollama to pull models: {exc}")
    return pulled


def _pull_one(client, name: str) -> bool:
    """Stream a single `ollama pull`; True on success."""
    import json

    try:
        last = ""
        with client.stream("POST", "/api/pull", json={"name": name, "stream": True}) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    continue
                status = frame.get("status", "")
                last = status
                if status in ("success", "error"):
                    break
        return last == "success"
    except Exception as exc:  # noqa: BLE001
        print(f"    pull error: {exc}")
        return False


def _install_missing_required(
    audit: object,
    *,
    term: Terminal,
    interactive: bool,
) -> bool:
    """Install missing *required python* dependencies (safe via pip). Returns
    True when every required dependency is satisfied afterwards."""
    from nova.setup.dependencies import Audit, audit_dependencies, install_dependency

    assert isinstance(audit, Audit)
    term.section("Install Missing")
    for dep in audit.missing_required:
        if dep.python_module:
            if interactive and not _ask(
                f"Missing required package {dep.name}. Install it now?", default=True
            ):
                term.warn(f"{dep.name} skipped; you can rerun `nova setup` later.")
                continue
            with term.spinner(f"Installing {dep.name}..."):
                ok = install_dependency(dep, interactive_confirm=False, auto=True)
            if ok:
                term.ok(f"{dep.name} installed")
            else:
                term.fail(f"{dep.name} could not be installed: {dep.manual_url or 'see diagnostics'}")
        else:
            term.warn(
                f"{dep.name} required but cannot be auto-installed safely: "
                f"{dep.manual_url or dep.install_hint}"
            )
    checked = audit_dependencies()
    if checked.missing_required:
        term.warn("Verification: some required dependencies are still missing.")
        return False
    term.ok("Verification: required dependencies satisfied.")
    return True


def run_assistant(
    interactive: bool = True,
    config_path: str | Path = CONFIG_DEFAULT,
    auto: bool = False,
    non_interactive: bool = False,
) -> dict:
    """Run the guided setup. Returns a report dict.

    `auto=True` runs a non-interactive "just do it" bootstrap for a machine that
    only needs sensible defaults (enabling safe plugins, no voice/web/host).
    `non_interactive=True` additionally disables prompts and spinners (CI/pipes).
    """
    if non_interactive:
        interactive = False
        auto = True
    term = Terminal(interactive=interactive and not auto)
    from nova.setup.dependencies import audit_dependencies

    term.header("N.O.V.A. First-Run Setup", "Installer & first-run wizard")
    report: dict = {}

    # 1/11 System Check
    term.section("System Check")
    profile = detect_machine()
    for line in profile.summary():
        term.sub(line)

    # 2/11 Dependency Check
    term.section("Dependency Check")
    audit = audit_dependencies(features=("core", "hybrid"))
    rows = []
    for dep in audit.dependencies:
        if dep.effective_status == "skip":
            continue
        state = "OK" if dep.effective_status == "installed" else (
            "outdated" if dep.effective_status == "outdated" else ("missing" if dep.required else "optional")
        )
        version = f"v{dep.version}" if dep.version else "-"
        role = "required" if dep.required else "optional"
        rows.append([dep.name, dep.purpose[:44], version, state, role])
    term.table(["Dependency", "Purpose", "Version", "State", "Role"], rows)
    required_ok = not (audit.missing_required or audit.outdated)
    term.ok("All core dependencies present." if required_ok else "Some dependencies need attention.")

    # 3/11 Install Missing
    if not required_ok:
        _install_missing_required(audit, term=term, interactive=interactive)

    # 4/11 Verification
    term.section("Verification")
    audit = audit_dependencies(features=("core", "hybrid"))
    if audit.missing_required or audit.outdated:
        for dep in audit.missing_required + audit.outdated:
            term.fail(f"{dep.name} still {dep.effective_status} ({dep.manual_url or dep.install_hint or 'manual install'})")
    else:
        term.ok("Required dependencies satisfied.")

    # 5/11 Hardware
    term.section("Hardware")
    for line in profile.summary():
        term.sub(line)

    # 6/11 Ollama
    term.section("Ollama")
    st = detect_ollama()
    ollama_running = st.running
    if st.running:
        term.ok(f"Ollama serving at {st.base_url}.")
    elif st.installed:
        term.warn("Ollama installed but not serving.")
        if auto or (interactive and _ask("Start Ollama now?", default=True)):
            if _ollama_serve(profile, term):
                term.ok("Ollama started.")
                ollama_running = True
            else:
                term.warn("Ollama still not reachable; you can start it later and rerun `nova setup`.")
    else:
        term.fail("Ollama is not installed. It is required for LOCAL and HYBRID modes.")
        if auto:
            from nova.setup.dependencies import install_ollama

            with term.spinner("Installing Ollama..."):
                installed = install_ollama()
            if installed:
                term.ok("Ollama installed.")
                ollama_running = _ollama_serve(profile, term)
            else:
                term.warn(f"Automatic install failed. Install Ollama manually: https://ollama.com/download")
        elif interactive and _ask("Open ollama.com to download Ollama?", default=True):
            import webbrowser

            webbrowser.open("https://ollama.com/download")
            term.warn("Install Ollama, start it, then rerun `nova setup`.")

    # 7/11 Models
    models_pulled: list[str] = []
    st = detect_ollama()
    if st.running:
        term.section("Model Check")
        missing = missing_models(st.models, profile.ram_total_gb, profile.gpu_vram_gb)
        if missing:
            term.warn(f"Models to install: {', '.join(sorted(missing))}")
            if auto or (interactive and _ask("Install them now? (this downloads GBs)", default=True)):
                models_pulled = _pull_models(profile, config_path=config_path, term=term)
        else:
            term.ok("All recommended models already present.")

    # 8/11 OpenCode + AI mode
    oc = detect_opencode()
    ai_mode = "local"
    if interactive and not auto:
        term.section("OpenCode (optional)")
        term.sub(oc.message)
        ai_mode = _ask_ai_mode()
        if ai_mode == "hybrid" and not oc.running:
            term.warn(
                "OpenCode server is not running. N.O.V.A. will stay LOCAL "
                f"until the server answers at {oc.base_url}."
            )
            term.warn(
                "N.O.V.A. never installs OpenCode; you install and run "
                "it yourself (opencode.ai), then rerun `nova setup`."
            )

    # 9/11 Config
    report = autoconfigure(
        profile,
        path=config_path,
        enable_plugins=True,
        enable_voice=False,
        enable_web=False,
        enable_automation=False,
        ai_mode="local" if ai_mode == "configure_later" else ai_mode,
    )
    term.section("Configuration")
    term.sub(f"Config ready: {report.config_path}")
    if report.changed:
        term.sub(f"Wrote: {report.summary}")
    else:
        term.sub("(no changes - your config was already set up)")
    term.sub(f"Default model: {default_model_for(profile.ram_total_gb, profile.gpu_vram_gb).model}")

    # 10/11 Optional autostart (interactive only)
    autostart_enabled = False
    if interactive and _ask("Start N.O.V.A. automatically on login?", default=False):
        try:
            from nova.setup.autostart import set_autostart

            set_autostart(True)
            autostart_enabled = True
        except Exception as exc:  # noqa: BLE001
            term.warn(f"Could not enable autostart: {exc}")

    # 11/11 Optional spoken JARVIS-style greeting (best-effort, local TTS only)
    greeted = False
    if interactive and _ask("Probar la voz local y recibir un saludo?", default=False):
        term.arrow("Speaking...")
        _say(
            "Bienvenido, señor. Todos los sistemas están operativos y "
            "listos para trabajar."
        )
        greeted = True

    # Final: Ready summary
    term.section("Ready")
    term.ok("N.O.V.A. setup complete.")
    term.sub("Run `nova` to chat, or `nova setup --help` for options.")
    from nova.setup.dependencies import audit_dependencies

    ready = audit_dependencies(features=("core", "hybrid")).ready
    return {
        "profile": profile,
        "ollama_running": detect_ollama().running,
        "models_pulled": models_pulled,
        "config": str(report.config_path),
        "autostart": autostart_enabled,
        "greeted": greeted,
        "ai_mode": ai_mode,
        "opencode": oc.message,
        "dependencies_ready": ready,
    }
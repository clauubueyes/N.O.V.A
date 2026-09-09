from __future__ import annotations

"""Guided "JARVIS" first-run assistant.

Walks a new user through the whole bootstrap: detect hardware, check/start
Ollama, import the recommended models, write config.yaml and (optionally) enable
autostart + voice. Uses plain text by default and — when the local voice stack is
installed and configured — can greet and speak through N.O.V.A. itself
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


def _ollama_serve(profile: MachineProfile) -> bool:
    """Start Ollama in the background (best-effort) and return success."""
    print("  Starting Ollama...")
    from nova.setup.detect import ensure_ollama_running

    return ensure_ollama_running().running


@exclusive
def _pull_models(profile: MachineProfile, config_path: str | Path | None = None) -> list[str]:
    import httpx

    from nova.setup.detect import detect_ollama
    from nova.setup.models import normalize_model_name
    from nova.setup.selector import fallback_candidate, plan_lines, select_stack

    from nova.core.config import load_settings
    from nova.setup.state import StateStore

    base = load_settings(str(config_path) if config_path else None).llm.base_url
    store = StateStore()
    store.load()
    from nova.setup.ollama_lifecycle import local_endpoint, snapshot

    st = snapshot(base)
    if not local_endpoint(base) or not st.running:
        print("Ollama not running; models not pulled.")
        return []
    installed = set(st.models)
    for name in st.models:
        store.record_model(name, "preexisting", base, owned=False)

    print("\nSelecting models for this machine...")
    stack = select_stack(profile, check_disk=True)
    for line in plan_lines(profile):
        print(line)

    targets = [c for c in stack if c.install and normalize_model_name(c.spec.name) not in installed]
    pulled: list[str] = []
    if not targets:
        print("\nAll recommended models already present.")
        return pulled

    try:
        with httpx.Client(base_url=base, timeout=3600) as client:
            for choice in targets:
                name = choice.spec.name
                if normalize_model_name(name) in installed:
                    print(f"  [ok] {name} already installed")
                    pulled.append(name)
                    continue
                print(f"\n  Pulling {name} ({choice.spec.description})...")
                if _pull_one(client, name):
                    store.record_model(name, choice.kind, base, owned=True)
                    digest = snapshot(base).models.get(normalize_model_name(name), "")
                    store.record_model(name, choice.kind, base, owned=True, digest=digest)
                    installed.add(normalize_model_name(name))
                    pulled.append(name)
                    print(f"  [ok] {name} ready")
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
                    print(f"  Trying smaller fallback {fb.name} instead...")
                    if _pull_one(client, fb.name):
                        store.record_model(fb.name, choice.kind, base, owned=True)
                        installed.add(normalize_model_name(fb.name))
                        pulled.append(fb.name)
                        print(f"  [ok] {fb.name} ready")
                    else:
                        print(f"  [err] {fb.name} failed too; skipping this optional role.")
                else:
                    print(f"  [err] {name} failed and no smaller fallback exists.")
    except Exception as exc:  # noqa: BLE001
        print(f"  Could not reach Ollama to pull models: {exc}")
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


def run_assistant(
    interactive: bool = True,
    config_path: str | Path = CONFIG_DEFAULT,
    auto: bool = False,
) -> dict:
    """Run the guided setup. Returns a report dict.

    `auto=True` runs a non-interactive "just do it" bootstrap for a machine that
    only needs sensible defaults (enabling safe plugins, no voice/web/host).
    """
    print("=" * 60)
    print("  N.O.V.A. First-Run Setup")
    print("=" * 60)
    profile = detect_machine()
    print("\nDetected machine:")
    for line in profile.summary():
        print(f"  {line}")

    # 1) Ollama
    st = detect_ollama()
    if st.installed and not st.running:
        print("\nOllama is installed but not serving.")
        if auto or (interactive and _ask("Start Ollama now?", default=True)):
            if not _ollama_serve(profile):
                print("  (Ollama still not reachable; you can start it later and rerun `nova setup`.)")
    elif not st.installed:
        print("\nOllama is not installed. N.O.V.A. needs it to run models locally.")
        if auto or (interactive and _ask("Open ollama.com to download Ollama?", default=True)):
            import webbrowser

            webbrowser.open("https://ollama.com/download")
            print("  Install Ollama, start it, then rerun `nova setup`.")

    # 2) Models (only meaningful if Ollama is reachable)
    models_pulled: list[str] = []
    st = detect_ollama()
    if st.running:
        missing = missing_models(st.models, profile.ram_total_gb, profile.gpu_vram_gb)
        if missing:
            print(f"\nModels to install: {', '.join(sorted(missing))}")
            if auto or (interactive and _ask("Install them now? (this downloads GBs)", default=True)):
                models_pulled = _pull_models(profile, config_path=config_path)
        else:
            print("\nAll recommended models already present.")

    # 3) Helper for OpenCode status + AI mode
    oc = detect_opencode()
    ai_mode = "local"
    if interactive and not auto:
        print("\nOpenCode (optional cloud fallback): " + oc.message)
        ai_mode = _ask_ai_mode()
        if ai_mode == "hybrid" and not oc.running:
            print(
                "\n  [warn] OpenCode server is not running. N.O.V.A. will stay "
                "LOCAL until the server answers at {}.".format(oc.base_url)
            )
            print(
                "  [warn] N.O.V.A. never installs OpenCode; you install and run "
                "it yourself (opencode.ai), then rerun `nova setup`."
            )

    # 4) Config
    report = autoconfigure(
        profile,
        path=config_path,
        enable_plugins=True,
        enable_voice=False,
        enable_web=False,
        enable_automation=False,
        ai_mode="local" if ai_mode == "configure_later" else ai_mode,
    )
    print(f"\nConfig ready: {report.config_path}")
    if report.changed:
        print(f"  Wrote: {report.summary}")
    else:
        print("  (no changes - your config was already set up)")
    print(f"  Default model: {default_model_for(profile.ram_total_gb, profile.gpu_vram_gb).model}")

    # 5) Optional autostart (interactive only)
    autostart_enabled = False
    if interactive and _ask("Start N.O.V.A. automatically on login?", default=False):
        try:
            from nova.setup.autostart import set_autostart

            set_autostart(True)
            autostart_enabled = True
        except Exception as exc:  # noqa: BLE001
            print(f"  Could not enable autostart: {exc}")

    # 6) Optional spoken JARVIS-style greeting (best-effort, local TTS only)
    greeted = False
    if interactive and _ask("Probar la voz local y recibir un saludo?", default=False):
        print("  Speaking...")
        _say(
            "Bienvenido, señor. Todos los sistemas están operativos y "
            "listos para trabajar."
        )
        greeted = True

    print("\n  Done. Run `nova` to chat, or `nova setup --help` for options.")
    return {
        "profile": profile,
        "ollama_running": detect_ollama().running,
        "models_pulled": models_pulled,
        "config": str(report.config_path),
        "autostart": autostart_enabled,
        "greeted": greeted,
        "ai_mode": ai_mode,
        "opencode": oc.message,
    }

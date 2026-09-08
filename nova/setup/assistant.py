from __future__ import annotations

"""Guided "JARVIS" first-run assistant.

Walks a new user through the whole bootstrap: detect hardware, check/start
Ollama, import the recommended models, write config.yaml and (optionally) enable
autostart + voice. Uses plain text by default and — when the local voice stack is
installed and configured — can greet and speak through N.O.V.A. itself
(ADR-017: the audio never leaves the device).
"""

from pathlib import Path

from nova.setup.detect import MachineProfile, detect_machine, detect_ollama
from nova.setup.models import default_model_for, missing_models, recommended_models
from nova.setup.provision import autoconfigure

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


def _ollama_serve(profile: MachineProfile) -> bool:
    """Start Ollama in the background (best-effort) and return success."""
    print("  Starting Ollama...")
    from nova.setup.detect import ensure_ollama_running

    return ensure_ollama_running().running


def _pull_models(profile: MachineProfile) -> list[str]:
    import httpx

    base = "http://localhost:11434"
    recs = recommended_models(profile.ram_total_gb, profile.gpu_vram_gb)
    pulled: list[str] = []
    try:
        with httpx.Client(base_url=base, timeout=3600) as client:
            for rec in recs:
                name = rec.model
                print(f"\n  Pulling {name} ({rec.reason})...")
                try:
                    last = ""
                    with client.stream("POST", "/api/pull", json={"name": name, "stream": True}) as resp:
                        resp.raise_for_status()
                        for line in resp.iter_lines():
                            if not line:
                                continue
                            import json

                            try:
                                frame = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            status = frame.get("status", "")
                            last = status
                            if status in ("success", "error"):
                                break
                    if last == "success":
                        pulled.append(name)
                        print(f"  [ok] {name} ready")
                    else:
                        print(f"  [err] {name} failed ({last or 'unknown'})")
                except Exception as exc:  # noqa: BLE001
                    print(f"  [err] {name} failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"  Could not reach Ollama to pull models: {exc}")
    return pulled


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
                models_pulled = _pull_models(profile)
        else:
            print("\nAll recommended models already present.")

    # 3) Config
    report = autoconfigure(
        profile,
        path=config_path,
        enable_plugins=True,
        enable_voice=False,
        enable_web=False,
        enable_automation=False,
    )
    print(f"\nConfig ready: {report.config_path}")
    if report.changed:
        print(f"  Wrote: {report.summary}")
    else:
        print("  (no changes - your config was already set up)")
    print(f"  Default model: {default_model_for(profile.ram_total_gb, profile.gpu_vram_gb).model}")

    # 4) Optional autostart (interactive only)
    autostart_enabled = False
    if interactive and _ask("Start N.O.V.A. automatically on login?", default=False):
        try:
            from nova.setup.autostart import set_autostart

            set_autostart(True)
            autostart_enabled = True
        except Exception as exc:  # noqa: BLE001
            print(f"  Could not enable autostart: {exc}")

    # 4) Optional spoken JARVIS-style greeting (best-effort, local TTS only)
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
    }

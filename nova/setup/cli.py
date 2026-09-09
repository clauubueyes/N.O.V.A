from __future__ import annotations

"""`nova setup` / `nova doctor` CLI.

A friendly one-command bootstrap: detects hardware, checks Ollama, pulls the
recommended models, writes config.yaml defaults and optionally enables autostart
or voice. `nova doctor` prints a diagnostic summary (health check).
"""

import argparse
import sys
from pathlib import Path

import httpx

from nova.setup.assistant import run_assistant
from nova.setup.detect import detect_machine, detect_ollama, detect_python_packages
from nova.setup.models import missing_models


def _ensure_vosk_model(config: str) -> None:
    """If voice is enabled and stt.model_dir is unset, fetch a small Spanish Vosk
    model so the microphone works out of the box (best-effort, ~46 MB)."""
    import yaml

    try:
        with open(config, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        return
    voice_cfg = data.get("voice") or {}
    if not voice_cfg.get("enabled") or voice_cfg.get("stt", {}).get("model_dir"):
        return
    import os
    import urllib.request
    import zipfile

    from nova.core.paths import installation_home
    from nova.setup.state import StateStore

    target_dir = str(installation_home() / "vosk")
    url = "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip"
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
        StateStore().record_resource(Path(target_dir), "data")
    model_dir = os.path.join(target_dir, "vosk-model-small-es-0.42")
    if not os.path.isdir(model_dir):
        print(f"  Downloading Vosk model for Spanish ({url})...", end="", flush=True)
        zip_path = os.path.join(target_dir, "vosk-model-small-es-0.42.zip")
        try:
            urllib.request.urlretrieve(url, zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(target_dir)
            os.remove(zip_path)
            print(" [ok]")
        except Exception as exc:
            print(f"\n  Vosk model download failed: {exc}")
            return
    voice_cfg.setdefault("stt", {})["model_dir"] = model_dir
    data["voice"] = voice_cfg
    with open(config, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
    print(f"  Vosk model ready at {model_dir}")


def _do_doctor() -> int:
    from nova.core.config import load_settings
    from nova.setup.detect import detect_opencode
    from nova.setup.selector import plan_lines

    print("N.O.V.A. diagnostic\n" + "=" * 40)
    profile = detect_machine()
    for line in profile.summary():
        print("  " + line)
    st = detect_ollama()
    print("Ollama:", st.message)
    if st.models:
        print("Installed models:")
        for m in st.models:
            print("  - " + m)
        missing = missing_models(st.models, profile.ram_total_gb, profile.gpu_vram_gb)
        if missing:
            print("Missing recommended:", ", ".join(sorted(missing)))
    else:
        print("No models installed. Run `nova setup`.")
    # PHASE 14 — AI mode, privacy and OpenCode diagnostics
    settings = load_settings()
    mode = settings.ai.mode
    privacy = settings.ai.privacy
    print(f"AI mode: {mode.value} ({privacy.value})")
    oc = detect_opencode(settings.open_code.base_url)
    print("OpenCode:", oc.message)
    if oc.running:
        print("OpenCode providers:", ", ".join(oc.providers) or "none")
        print("OpenCode models:", ", ".join(sorted(oc.models))[:200] or "none")
    if mode == "hybrid" and privacy != "cloud_allowed":
        print("[WARN] AI mode is hybrid but privacy is local_only: cloud fallback is disabled.")
    if mode == "hybrid" and not oc.running:
        print("[WARN] AI mode is hybrid but the OpenCode server is not reachable; all tasks stay local.")
    for line in plan_lines(profile):
        print(line)
    pkgs = detect_python_packages()
    print("Optional packages:")
    for name, present in pkgs.items():
        print(f"  - {name}: {'[x]' if present else '[ ]'}")
    return 0


def _do_autostart(args) -> int:
    from nova.setup.autostart import AutostartError, autostart_status, set_autostart

    try:
        if args.enable is None:
            print("Autostart:", "enabled" if autostart_status() else "disabled")
            return 0
        set_autostart(bool(args.enable), target=args.target)
        print("Autostart:", "enabled" if args.enable else "disabled")
        return 0
    except AutostartError as exc:
        print(f"Error: {exc}")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nova setup",
        description="N.O.V.A. installer & first-run wizard.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="print a diagnostic summary")

    p_auto = sub.add_parser("auto", help="non-interactive bootstrap with safe defaults")
    p_auto.add_argument("--voice", action="store_true", help="enable local voice (STT/TTS)")
    p_auto.add_argument("--web", action="store_true", help="enable web tools")
    p_auto.add_argument("--config", default=None)
    p_auto.add_argument("--no-models", action="store_true",
                        help="skip the Ollama model download (config only)")

    p_status = sub.add_parser("status", help="check Ollama + models + config health")

    p_install = sub.add_parser("install", help="interactive installation (same as no arguments)")
    p_install.add_argument("--config", default=None)
    p_update = sub.add_parser("update", help="re-evaluate hardware, catalog, Ollama and model stack")
    p_update.add_argument("--config", default=None)
    p_update.add_argument("--catalog-url", default=None, help="reviewed HTTPS model catalog feed")
    p_update.add_argument("--component", choices=["models", "ollama", "both"])
    sub.add_parser("remove", help="preview and confirm removal of owned local resources")

    p_autostart = sub.add_parser("autostart", help="toggle running on login")
    toggle = p_autostart.add_mutually_exclusive_group()
    toggle.add_argument("--enable", type=int, choices=[0, 1], nargs="?", const=1, default=None,
                             help="1 = enable, 0 = disable (omit to query)")
    toggle.add_argument("--disable", dest="enable", action="store_const", const=0)
    p_autostart.add_argument("--target", default="nova-agent",
                             help="entry point to run on login (default: nova-agent)")

    return parser


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cmd = args.command
    from nova.core.paths import default_config_path

    if cmd == "update":
        from nova.setup.update import run_update

        return run_update(args.config, catalog_url=args.catalog_url, component=args.component)
    if hasattr(args, "config") and args.config is None:
        args.config = str(default_config_path())
    if cmd == "remove":
        from nova.setup.remove import run_remove

        return run_remove()
    if cmd == "doctor":
        return _do_doctor()
    if cmd == "auto":
        from nova.setup.provision import autoconfigure

        profile = detect_machine()
        report = autoconfigure(
            profile,
            path=args.config,
            enable_plugins=True,
            enable_voice=args.voice,
            enable_web=args.web,
            enable_automation=False,
        )
        print(f"Config ready: {report.config_path}")
        if report.changed:
            print("Wrote:", report.summary)
        if args.voice:
            _ensure_vosk_model(args.config)
        st = detect_ollama()
        if st.running and not args.no_models:
            from nova.setup.assistant import _pull_models

            _pull_models(profile, config_path=args.config)
        else:
            from nova.setup.selector import pprint_plan

            pprint_plan(profile)
            if args.no_models:
                print("--no-models: model download skipped.")
            else:
                print("Ollama not running; models not pulled (run `nova setup`).")
        return 0
    if cmd == "status":
        _do_doctor()
        return 0
    if cmd == "autostart":
        return _do_autostart(args)
    # Default: interactive wizard.
    run_assistant(interactive=True, config_path=args.config if hasattr(args, "config") else default_config_path())
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(f"[WARN] {exc}")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled; no further changes.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

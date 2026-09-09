from __future__ import annotations

"""`nova setup` / `nova doctor` CLI.

A friendly one-command bootstrap: system check, dependency check/install,
hardware detection, Ollama, optional OpenCode, model selection/install, config
defaults and optional autostart/voice. `nova doctor` prints a full diagnostic
(REQUIRED/OPTIONAL/WARNINGS/READY). Supports non-interactive and JSON output for
pipes and CI; credentials are never printed.
"""

import argparse
import sys
from pathlib import Path

import httpx

from nova.setup.assistant import run_assistant
from nova.setup.detect import detect_machine, detect_ollama, detect_python_packages
from nova.setup.models import missing_models


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("output")
    group.add_argument("--non-interactive", action="store_true",
                       help="disable prompts, spinners and interactive decoration")
    group.add_argument("--json", action="store_true",
                       help="machine-readable output: one JSON object per line")
    group.add_argument("--no-color", action="store_true",
                       help="disable ANSI colors (also honors NO_COLOR)")
    group.add_argument("--ascii", action="store_true",
                       help="force ASCII symbols instead of Unicode")


def _make_term(args: argparse.Namespace, interactive: bool = True):
    from nova.setup.terminal import Terminal

    return Terminal(
        interactive=interactive and not getattr(args, "non_interactive", False),
        allow_color=False if getattr(args, "no_color", False) else None,
        ascii=True if (getattr(args, "ascii", False) or getattr(args, "json", False)) else None,
        json_mode=getattr(args, "json", False),
    )


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


def _dependency_rows():
    from nova.setup.dependencies import audit_dependencies

    audit = audit_dependencies()
    rows = []
    for dep in audit.dependencies:
        if dep.effective_status == "skip":
            continue
        state = {
            "installed": "OK",
            "outdated": "outdated",
            "missing": "missing" if dep.required else "optional",
        }.get(dep.effective_status, dep.effective_status)
        version = f"v{dep.version}" if dep.version else "-"
        rows.append([dep.name, dep.purpose[:46], version, state, "required" if dep.required else "optional"])
    return rows


def _do_doctor(term) -> int:
    from nova.core.config import load_settings
    from nova.setup.dependencies import audit_dependencies
    from nova.setup.detect import detect_opencode
    from nova.setup.selector import plan_lines

    audit = audit_dependencies()
    term.header("N.O.V.A. Diagnostic", "health check")

    term.section("System")
    profile = detect_machine()
    for line in profile.summary():
        term.sub(line)

    term.section("Required Dependencies")
    if audit.missing_required:
        for dep in audit.missing_required:
            term.fail(f"{dep.name} / Status: MISSING / Required: yes / Action: {dep.manual_url or dep.install_hint or 'install manually'}")
    else:
        term.ok("All required dependencies present.")
    if audit.outdated:
        for dep in audit.outdated:
            term.fail(f"{dep.name} / Status: OUTDATED ({dep.version}) / Required: min {dep.min_version} / Action: upgrade")

    term.section("Optional Dependencies")
    if audit.missing_optional:
        for dep in audit.missing_optional:
            term.warn(f"{dep.name} / Status: OPTIONAL / Action: {dep.manual_url or dep.install_hint or 'not needed'}")
    else:
        term.ok("All optional dependencies present.")

    term.section("Ollama")
    st = detect_ollama()
    term.sub(st.message)
    if st.models:
        term.sub(f"Models: {', '.join(st.models)}")
        missing = missing_models(st.models, profile.ram_total_gb, profile.gpu_vram_gb)
        if missing:
            term.warn(f"Missing recommended: {', '.join(sorted(missing))}")
    else:
        term.warn("No models installed. Run `nova setup`.")

    term.section("OpenCode (optional)")
    settings = load_settings()
    oc = detect_opencode(settings.open_code.base_url)
    mode = settings.ai.mode
    privacy = settings.ai.privacy
    term.sub(f"AI mode: {mode.value} ({privacy.value})")
    term.sub(oc.message)
    if oc.running:
        term.sub(f"Providers: {', '.join(oc.providers) or 'none'}")
        term.sub(f"Models: {', '.join(sorted(oc.models))[:200] or 'none'}")
        term.sub(f"Authentication: {oc.auth}")
    if mode == "hybrid" and privacy != "cloud_allowed":
        term.warn("AI mode is hybrid but privacy is local_only: cloud fallback is disabled.")
    if mode == "hybrid" and not oc.running:
        term.warn("AI mode is hybrid but the OpenCode server is not reachable; all tasks stay local.")

    term.section("Hardware Plan")
    for line in plan_lines(profile):
        term.sub(line)

    term.section("Result")
    warnings = len(audit.missing_optional)
    if audit.ready:
        term.ok("READY")
    else:
        term.fail("NOT READY: install the missing required dependencies, then rerun.")
    if st.installed and not st.running:
        term.warn("Ollama not running: start it (`ollama serve`) and rerun doctor.")
    if mode == "hybrid" and not oc.running:
        term.warn("Hybrid configured but OpenCode unreachable: N.O.V.A. is effectively LOCAL today.")
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
    _add_common_flags(parser)
    sub = parser.add_subparsers(dest="command")

    p_doctor = sub.add_parser("doctor", help="print a full diagnostic summary")
    _add_common_flags(p_doctor)

    p_repair = sub.add_parser("repair", help="re-check and fix missing dependencies, Ollama and config")
    _add_common_flags(p_repair)
    p_repair.add_argument("--config", default=None)

    p_auto = sub.add_parser("auto", help="non-interactive bootstrap with safe defaults")
    _add_common_flags(p_auto)
    p_auto.add_argument("--voice", action="store_true", help="enable local voice (STT/TTS)")
    p_auto.add_argument("--web", action="store_true", help="enable web tools")
    p_auto.add_argument("--config", default=None)
    p_auto.add_argument("--no-models", action="store_true",
                        help="skip the Ollama model download (config only)")

    p_status = sub.add_parser("status", help="check Ollama + models + config health")

    p_install = sub.add_parser("install", help="interactive installation (same as no arguments)")
    _add_common_flags(p_install)
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


def _do_auto(args) -> int:
    from nova.core.paths import default_config_path
    from nova.setup.dependencies import audit_dependencies, install_dependency
    from nova.setup.terminal import Terminal

    config = args.config or str(default_config_path())
    term = Terminal(interactive=False, json_mode=args.json, ascii=args.ascii)
    term.header("N.O.V.A. Auto Setup", "non-interactive bootstrap")

    profile = detect_machine()
    term.section("Dependency Check")
    audit = audit_dependencies(features=("core", "hybrid"))
    if audit.missing_required:
        term.warn("Missing required dependencies detected; installing safe ones.")
        for dep in audit.missing_required:
            ok = install_dependency(dep, auto=True)
            term.ok(f"{dep.name} installed") if ok else term.fail(
                f"{dep.name} could not be auto-installed ({dep.manual_url or 'manual install required'})"
            )
    else:
        term.ok("All required dependencies present.")

    from nova.setup.provision import autoconfigure

    report = autoconfigure(
        profile,
        path=config,
        enable_plugins=True,
        enable_voice=args.voice,
        enable_web=args.web,
        enable_automation=False,
    )
    term.section("Configuration")
    term.sub(f"Config ready: {report.config_path}")
    if report.changed:
        term.sub(f"Wrote: {report.summary}")
    if args.voice:
        _ensure_vosk_model(config)
    term.section("Ollama")
    st = detect_ollama()
    if st.installed and not st.running:
        from nova.setup.detect import ensure_ollama_running

        term.arrow("Starting Ollama...")
        st = ensure_ollama_running()
    if not st.installed:
        term.warn("Ollama missing; install it manually from https://ollama.com/download and rerun.")
    term.section("Models")
    if st.running and not args.no_models:
        from nova.setup.assistant import _pull_models

        pulled = _pull_models(profile, config_path=config, term=term)
        term.ok(f"{len(pulled)} model(s) pulled.") if pulled else term.sub("No new models pulled.")
    else:
        from nova.setup.selector import pprint_plan

        pprint_plan(profile)
        if args.no_models:
            term.sub("--no-models: model download skipped.")
        elif not st.running:
            term.sub("Ollama not running; models not pulled (run `nova setup`).")
    term.section("Ready")
    term.ok("Auto setup finished.")
    return 0


def _do_repair(args) -> int:
    from nova.core.paths import default_config_path
    from nova.setup.repair import run_repair

    term = _make_term(args, interactive=True)
    return run_repair(
        args.config or str(default_config_path()),
        term=term,
        interactive=not getattr(args, "non_interactive", False),
    )


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
        term = _make_term(args, interactive=True)
        return _do_doctor(term)
    if cmd == "status":
        from nova.setup.terminal import Terminal

        return _do_doctor(Terminal(interactive=not getattr(args, "non_interactive", False)))
    if cmd == "repair":
        return _do_repair(args)
    if cmd == "auto":
        return _do_auto(args)
    if cmd == "autostart":
        return _do_autostart(args)
    # Default: interactive wizard (also `install` alias).
    config_path = args.config if hasattr(args, "config") else str(default_config_path())
    run_assistant(
        interactive=True,
        config_path=config_path,
        non_interactive=getattr(args, "non_interactive", False),
    )
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
from __future__ import annotations

"""`nova setup` / `nova doctor` CLI.

A friendly one-command bootstrap: detects hardware, checks Ollama, pulls the
recommended models, writes config.yaml defaults and optionally enables autostart
or voice. `nova doctor` prints a diagnostic summary (health check).
"""

import argparse
import sys

from nova.setup.assistant import run_assistant
from nova.setup.detect import detect_machine, detect_ollama, detect_python_packages
from nova.setup.models import missing_models


def _do_doctor() -> int:
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
    p_auto.add_argument("--config", default="config/config.yaml")

    p_status = sub.add_parser("status", help="check Ollama + models + config health")

    p_autostart = sub.add_parser("autostart", help="toggle running on login")
    p_autostart.add_argument("--enable", type=int, choices=[0, 1], default=None,
                             help="1 = enable, 0 = disable (omit to query)")
    p_autostart.add_argument("--target", default="nova-agent",
                             help="entry point to run on login (default: nova-agent)")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cmd = args.command
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
        st = detect_ollama()
        if st.running:
            from nova.setup.assistant import _pull_models

            _pull_models(profile)
        else:
            print("Ollama not running; models not pulled (run `nova setup`).")
        return 0
    if cmd == "status":
        _do_doctor()
        return 0
    if cmd == "autostart":
        return _do_autostart(args)
    # Default: interactive wizard.
    run_assistant(interactive=True, config_path=args.config if hasattr(args, "config") else "config/config.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())

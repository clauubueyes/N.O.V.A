from __future__ import annotations

"""Record resources immediately after the platform installer creates them."""

import argparse
import re
from pathlib import Path

from nova.setup.ollama_lifecycle import snapshot
from nova.setup.state import StateStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--venv")
    parser.add_argument("--find-ollama", action="store_true")
    parser.add_argument("--ollama")
    parser.add_argument("--method", choices=["winget", "official"], default="official")
    args = parser.parse_args()
    if args.find_ollama:
        from nova.setup.detect import _find_ollama_bin

        print(_find_ollama_bin() or "")
        return
    store = StateStore()
    if args.venv:
        path = Path(args.venv).resolve()
        if not (path / "pyvenv.cfg").is_file():
            raise ValueError("Not a virtualenv")
        store.record_resource(path, "venv")
    if args.ollama:
        path = Path(args.ollama).resolve()
        if not path.is_file():
            raise ValueError("Ollama executable missing")
        def record(state):
            state.ollama.installed_by_nova = True
            state.ollama.executable = str(path)
            state.ollama.method = args.method
            state.ollama.version = snapshot("http://localhost:11434").version
            if not state.ollama.version:
                from nova.setup.detect import _run_command

                match = re.search(r"\d+\.\d+\.\d+", _run_command([str(path), "--version"]) or "")
                state.ollama.version = match[0] if match else ""
        store.change(record)


if __name__ == "__main__":
    main()

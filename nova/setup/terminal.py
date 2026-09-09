from __future__ import annotations

"""PHASE 14.2 — lightweight terminal presentation layer for ``nova setup``.

Keeps user output pretty and consistent while staying dependency-free, safe for
pipes/CI and secret-free by construction (it never receives credentials):

- ANSI colors when the terminal supports them and ``NO_COLOR`` is not set.
- Unicode symbols (✓ ✗ ! →) with an ASCII fallback for limited terminals and
  non-interactive output so scripts/CI can parse it easily.
- Sections, tables, confirmations and a simple spinner, all switchable off
  with ``--non-interactive`` / ``--json``.
"""

import os
import sys
import threading
import time
from dataclasses import dataclass, field


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    term = os.environ.get("TERM", "")
    if term == "dumb":
        return False
    try:
        return bool(sys.stdout.isatty()) and sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        return False


def _win_enable_vt() -> None:
    """Ad-hoc VT100 handling on Windows so ANSI escape codes render."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:  # noqa: BLE001
        pass


@dataclass
class Terminal:
    """Pretty, pipe-safe output for the setup flows.

    ``interactive`` allows prompts and spinners; ``allow_color`` enables ANSI;
    ``ascii`` forces the ASCII fallback; ``json_mode`` emits one JSON object per
    line instead of free-form text (machine friendly).
    """

    interactive: bool = True
    allow_color: bool | None = None
    ascii: bool | None = None
    json_mode: bool = False
    indent: str = "  "

    _spinner: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _win_enable_vt()
        if self.allow_color is None:
            self.allow_color = _supports_color()
        if self.ascii is None:
            self.ascii = not _supports_unicode()

    # -- symbol helpers ------------------------------------------------------
    def _sym(self, kind: str = "ok") -> str:
        if self.ascii:
            return {"ok": "OK", "fail": "X", "warn": "!"}.get(kind, "?")
        return {"ok": "\u2713", "fail": "\u2717", "warn": "\u0021"}.get(kind, "?")

    def _style(self, text: str, code: str) -> str:
        if not self.allow_color:
            return text
        return f"\033[{code}m{text}\033[0m"

    @staticmethod
    def _hex(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    # -- low-level output ----------------------------------------------------
    def _emit(
        self,
        level: str,
        message: str,
        symbol: str | None = None,
        color: str | None = None,
        bracket: bool = True,
    ) -> None:
        if self.json_mode:
            import json

            print(json.dumps({"level": level, "message": message}, ensure_ascii=False))
            return
        if symbol is None:
            print(f"{self.indent}{message}")
            return
        mark = f"[{self._style(symbol, color)}]" if bracket else self._style(symbol, color)
        print(f"{self.indent}{mark} {message}")

    def raw(self, text: str = "") -> None:
        if self.json_mode:
            return
        print(text)

    def blank(self) -> None:
        if not self.json_mode:
            print()

    # -- sections / header ---------------------------------------------------
    def header(self, title: str, subtitle: str = "") -> None:
        width = 56
        if self.json_mode:
            self._emit("header", title)
            return
        if self.ascii:
            rule = "=" * width
            print(f"  {rule}")
            for line in (title, subtitle):
                if line:
                    print(f"  {line:^{width}}")
            print(f"  {rule}")
            return
        bar = "\u2501" * width
        print(f"  \u256d{bar}\u256e")
        for line in (title, subtitle):
            if not line:
                continue
            label = f"{line:^{width}}"
            print(f"  \u2502  {label}  ")
        print(f"  \u2570{bar}\u256f")

    def section(self, title: str) -> None:
        if self.json_mode:
            self._emit("section", title)
            return
        print()
        print(self._style(title, "1;33"))
        print("-" * 46 if self.ascii else "\u2500" * 46)

    def sub(self, text: str) -> None:
        if self.json_mode:
            import json

            print(json.dumps({"level": "sub", "message": text}, ensure_ascii=False))
            return
        print(f"{self.indent}{self._style(text, '2')}")

    # -- status lines --------------------------------------------------------
    def ok(self, text: str) -> None:
        self._emit("ok", text, symbol=self._sym("ok"), color="32")

    def fail(self, text: str) -> None:
        self._emit("fail", text, symbol=self._sym("fail"), color="31")

    def warn(self, text: str) -> None:
        self._emit("warn", text, symbol=self._sym("warn"), color="33")

    def arrow(self, text: str) -> None:
        arrow = "\u2192" if not self.ascii else "->"
        self._emit("info", text, symbol=arrow, color="36", bracket=False)

    def muted(self, text: str) -> None:
        self._emit("info", text)

    # -- interactive ---------------------------------------------------------
    def confirm(self, prompt: str, default: bool = True) -> bool:
        if not self.interactive:
            return default
        suffix = " [Y/n] " if default else " [y/N] "
        try:
            raw = input(prompt + suffix).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            raise
        if not raw:
            return default
        return raw in ("y", "yes", "s", "si")

    def choose(self, prompt: str, options: list[tuple[str, str]], default: str) -> str:
        if not self.interactive:
            return default
        print(f"{prompt}")
        for key, label in options:
            marker = f"[{key}]"
            print(f"{self.indent}{self._style(marker, '1;34')} {label}")
        try:
            selection = input(self._style(f"{prompt} [{default}]: ", "1")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            raise
        if not selection:
            return default
        for key, _ in options:
            if selection in (key, options[0][1].lower().split()[0]):
                return key
        return default

    # -- table ---------------------------------------------------------------
    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        if self.json_mode:
            for row in rows:
                payload = {h: (r[i] if i < len(r) else "") for i, h in enumerate(headers)}
                self._emit("row", str(payload).replace("'", '"'))
            return
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(widths):
                    widths[i] = max(widths[i], len(cell))
        def render(row: list[str]) -> str:
            cells = [f"{cell:<{widths[i]}}" for i, cell in enumerate(row)]
            return f"{self.indent}{'  '.join(cells)}"
        print(render(headers))
        rule = "\u2500" if not self.ascii else "-"
        print(f"{self.indent}{rule * (sum(widths) + 2 * (len(headers) - 1))}")
        for row in rows:
            print(render(row))

    # -- spinner -------------------------------------------------------------
    def spinner(self, message: str):
        """Context manager showing a small animated spinner during an operation."""
        if not self.interactive or not self.allow_color or self.json_mode:
            class _Noop:
                def __enter__(self_):
                    return self_
                def __exit__(self_, *exc):
                    return False
            return _Noop()

        frames = "|/-\\"
        stop = threading.Event()
        started = time.monotonic()

        def _run() -> None:
            index = 0
            while not stop.is_set():
                frame = frames[index % len(frames)]
                since = time.monotonic() - started
                print(f"\r{self.indent}{self._style(frame, '36')} {message} ({since:>4.0f}s)", end="", flush=True)
                index += 1
                stop.wait(0.12)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        class _Spinner:
            def __enter__(self_):
                return self_
            def __exit__(self_, *exc):
                stop.set()
                thread.join(timeout=0.3)
                print("\r" + " " * (len(self.indent) + len(message) + 20), end="\r", flush=True)
                return False

        return _Spinner()


def _supports_unicode() -> bool:
    try:
        encoding = sys.stdout.encoding or ""
    except Exception:  # noqa: BLE001
        encoding = ""
    return "utf" in encoding.lower() or "cp65001" in encoding.lower()


__all__ = ["Terminal", "_supports_color", "_supports_unicode", "_win_enable_vt"]
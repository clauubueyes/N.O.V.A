from __future__ import annotations

"""Rich-based presentation layer for the N.O.V.A. chat CLI.

Provides styled panels, spinners, welcome screen, and formatted output.
Falls back to plain text when Rich is unavailable or output is not a TTY.
"""

import os
import sys
from typing import Any

from nova.core.health import ServiceStatus, SystemInfo

try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


def _win_enable_vt() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


_win_enable_vt()

_NO_COLOR = bool(os.environ.get("NO_COLOR"))
_STDOUT_IS_TTY = False
try:
    _STDOUT_IS_TTY = sys.stdout.isatty()
except Exception:
    pass

USE_RICH = _HAS_RICH and not _NO_COLOR and _STDOUT_IS_TTY

_console: Console | None = None


def get_console() -> Console:
    global _console
    if _console is None:
        _console = Console(highlight=False)
    return _console


# ---------------------------------------------------------------------------
# Welcome screen
# ---------------------------------------------------------------------------

def _status_icon(ok: bool) -> str:
    if not USE_RICH:
        return "OK" if ok else "X"
    return "[green]\u2713[/]" if ok else "[red]\u2717[/]"


def _service_line(name: str, online: bool, detail: str = "") -> str:
    icon = _status_icon(online)
    label = f"{name}"
    if detail:
        label += f"  {detail}"
    return f"  {icon}  {label}"


def print_welcome(
    system: SystemInfo,
    ollama: ServiceStatus,
    opencode: ServiceStatus | None,
    model_name: str,
    model_provider: str = "local",
    warnings: list[str] | None = None,
) -> None:
    if not USE_RICH:
        _print_welcome_plain(system, ollama, opencode, model_name, model_provider, warnings)
        return

    c = get_console()

    header = Text()
    header.append("  N . O . V . A .", style="bold cyan")
    header.append("\n")
    header.append("  Neural Operations & Virtual Assistant", style="dim")

    sys_lines = Text()
    sys_lines.append("  OS       ", style="bold")
    sys_lines.append(f"{system.os_name}\n")
    sys_lines.append("  CPU      ", style="bold")
    sys_lines.append(f"{system.cpu_model} \u2022 {system.cpu_cores} cores\n")
    sys_lines.append("  RAM      ", style="bold")
    sys_lines.append(f"{system.ram_gb:.1f} GB\n")
    if system.gpu_model and system.gpu_model != "N/A":
        sys_lines.append("  GPU      ", style="bold")
        sys_lines.append(f"{system.gpu_model} \u2022 {system.gpu_vram_gb:.0f} GB VRAM\n")
    if system.cuda_available:
        sys_lines.append("  CUDA     ", style="bold")
        sys_lines.append("[green]\u2713[/]\n")

    svc_text = Text()
    svc_text.append("\n  SERVICES\n", style="bold")
    svc_text.append(_service_line("Ollama", ollama.online, ollama.detail) + "\n")
    if opencode:
        svc_text.append(_service_line("OpenCode", opencode.online, opencode.detail) + "\n")
    svc_text.append(_service_line("Router", True, "ready"))

    model_text = Text()
    model_text.append("\n  MODEL\n", style="bold")
    provider_label = "Local" if model_provider == "local" else model_provider.title()
    model_text.append(f"  {_status_icon(True)}  {model_name} \u2022 {provider_label}\n")

    ready = Text("\n  N.O.V.A. ready.\n", style="bold green")

    panel_content = Text()
    panel_content.append_text(header)
    panel_content.append("\n\n  SYSTEM\n", style="bold")
    panel_content.append_text(sys_lines)
    panel_content.append_text(svc_text)
    panel_content.append_text(model_text)
    panel_content.append_text(ready)

    c.print(Panel(
        panel_content,
        border_style="cyan",
        padding=(1, 2),
    ))

    if warnings:
        for w in warnings:
            c.print(f"  [yellow]\u26a0[/] {w}")
        c.print()


def _print_welcome_plain(
    system: SystemInfo,
    ollama: ServiceStatus,
    opencode: ServiceStatus | None,
    model_name: str,
    model_provider: str,
    warnings: list[str] | None,
) -> None:
    print("+--------------------------------------+")
    print("|        N . O . V . A .               |")
    print("|  Neural Operations & Virtual Asst.   |")
    print("+--------------------------------------+")
    print()
    print("  SYSTEM")
    print(f"    OS       {system.os_name}")
    print(f"    CPU      {system.cpu_model} / {system.cpu_cores} cores")
    print(f"    RAM      {system.ram_gb:.1f} GB")
    if system.gpu_model and system.gpu_model != "N/A":
        print(f"    GPU      {system.gpu_model} / {system.gpu_vram_gb:.0f} GB VRAM")
    if system.cuda_available:
        print(f"    CUDA     OK")
    print()
    print("  SERVICES")
    print(f"    Ollama   {'OK' if ollama.online else 'X'} {ollama.detail}")
    if opencode:
        print(f"    OpenCode {'OK' if opencode.online else 'X'} {opencode.detail}")
    print(f"    Router   OK ready")
    print()
    provider_label = "Local" if model_provider == "local" else model_provider.title()
    print(f"  MODEL")
    print(f"    {model_name} / {provider_label}")
    print()
    print("  N.O.V.A. ready.")
    print()
    if warnings:
        for w in warnings:
            print(f"    ! {w}")
        print()


# ---------------------------------------------------------------------------
# Chat messages
# ---------------------------------------------------------------------------

def print_user_message(text: str) -> None:
    if not USE_RICH:
        print(f"\nYou > {text}")
        return
    c = get_console()
    c.print(Panel(
        Text(text),
        title="[bold]You[/]",
        border_style="blue",
        padding=(0, 1),
    ))


def print_assistant_message(text: str, model: str = "", provider: str = "") -> None:
    if not USE_RICH:
        print(f"\nN.O.V.A. > {text}\n")
        return
    c = get_console()
    footer = ""
    if model:
        p = "Local" if provider in ("ollama", "local") else provider.title()
        footer = f"[dim]{model} \u2022 {p}[/]"
    c.print(Panel(
        Text(text),
        title="[bold cyan]N.O.V.A.[/]",
        border_style="cyan",
        padding=(0, 1),
        subtitle=footer,
    ))


def print_error(title: str, message: str, suggestions: list[str] | None = None) -> None:
    if not USE_RICH:
        print(f"\n[error] {title}: {message}")
        if suggestions:
            for s in suggestions:
                print(f"  -> {s}")
        print()
        return
    c = get_console()
    body = Text()
    body.append(f"{message}\n", style="white")
    if suggestions:
        body.append("\n")
        for s in suggestions:
            body.append(f"  \u2022 {s}\n", style="dim")
    c.print(Panel(
        body,
        title=f"[bold red]{title}[/]",
        border_style="red",
        padding=(0, 1),
    ))


def print_warning(text: str) -> None:
    if not USE_RICH:
        print(f"! {text}")
        return
    c = get_console()
    c.print(f"  [yellow]\u26a0[/] {text}")


def print_info(text: str) -> None:
    if not USE_RICH:
        print(text)
        return
    c = get_console()
    c.print(f"  [dim]{text}[/]")


def print_tool_status(tool_name: str, status: str, detail: str = "") -> None:
    if not USE_RICH:
        parts = [f"  [{tool_name}] {status}"]
        if detail:
            parts.append(f" {detail}")
        print("".join(parts))
        return
    c = get_console()
    icon = "\u25cb" if status == "running" else (_status_icon(status == "ok"))
    detail_str = f" \u2022 {detail}" if detail else ""
    c.print(f"    {icon} [bold]{tool_name}[/]{detail_str}")


# ---------------------------------------------------------------------------
# Thinking spinner
# ---------------------------------------------------------------------------

class ThinkingIndicator:
    """Context manager that shows a thinking spinner while active."""

    def __init__(self, message: str = "Thinking...") -> None:
        self._message = message
        self._live: Any = None

    def __enter__(self) -> "ThinkingIndicator":
        if not USE_RICH:
            print(f"  {self._message}")
            return self
        c = get_console()
        spinner = Text(f"  \u25d0 {self._message}", style="cyan")
        self._live = Live(spinner, console=c, refresh_per_second=8)
        self._live.start()
        return self

    def __exit__(self, *args: Any) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None


# ---------------------------------------------------------------------------
# Command output helpers
# ---------------------------------------------------------------------------

def print_command_header(title: str) -> None:
    if not USE_RICH:
        print(f"\n--- {title} ---")
        return
    c = get_console()
    c.print(f"\n[bold]{title}[/]")


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    if not USE_RICH:
        if headers:
            print("  " + "  ".join(f"{h:<20}" for h in headers))
        for row in rows:
            print("  " + "  ".join(f"{c:<20}" for c in row))
        return
    c = get_console()
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    for h in headers:
        table.add_column(h)
    for row in rows:
        table.add_row(*row)
    c.print(table)


def print_model_list(models: list[tuple[str, float]], active_model: str = "") -> None:
    if not USE_RICH:
        for name, size_mb in models:
            marker = " *" if name == active_model else ""
            print(f"  {name:<30} {size_mb:,.0f} MB{marker}")
        return
    c = get_console()
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Model")
    table.add_column("Size", justify="right")
    for name, size_mb in models:
        label = f"[bold cyan]{name}[/]" if name == active_model else name
        table.add_row(label, f"{size_mb:,.0f} MB")
    c.print(table)

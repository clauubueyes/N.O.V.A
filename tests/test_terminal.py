from __future__ import annotations

"""PHASE 14.2 — tests for the terminal presentation layer."""

from nova.setup.terminal import Terminal


def test_json_mode_emits_json_lines(capsys) -> None:
    term = Terminal(interactive=False, ascii=True, json_mode=True)
    term.ok("hello")
    term.warn("oops")
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert '"level": "ok"' in lines[0]
    assert '"message": "hello"' in lines[0]
    assert '"level": "warn"' in lines[1]
    assert '"message": "oops"' in lines[1]


def test_ascii_fallback_symbols(capsys) -> None:
    term = Terminal(interactive=False, ascii=True, allow_color=False)
    term.ok("x")
    term.fail("y")
    term.warn("z")
    out = capsys.readouterr().out
    assert "[OK]" in out
    assert "[X]" in out
    assert "[!]" in out
    assert "\u2713" not in out
    assert "\u2717" not in out


def test_unicode_symbols_when_requested(capsys) -> None:
    term = Terminal(interactive=False, ascii=False, allow_color=False)
    term.ok("x")
    term.fail("y")
    out = capsys.readouterr().out
    assert "\u2713" in out
    assert "\u2717" in out


def test_no_color_disables_ansi(capsys) -> None:
    term = Terminal(interactive=False, allow_color=False)
    term.ok("x")
    assert "\033[" not in capsys.readouterr().out


def test_nocolor_env_disables_color(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert Terminal(interactive=False).allow_color is False


def test_confirm_non_interactive_returns_default() -> None:
    term = Terminal(interactive=False)
    assert term.confirm("proceed?", default=True) is True
    assert term.confirm("proceed?", default=False) is False


def test_confirm_interactive_reads_input(monkeypatch, capsys) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    term = Terminal(interactive=True)
    assert term.confirm("proceed?") is True


def test_confirm_interactive_default_on_enter(monkeypatch, capsys) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    term = Terminal(interactive=True)
    assert term.confirm("proceed?", default=True) is True
    assert term.confirm("proceed?", default=False) is False


def test_table_renders_headers_and_rows(capsys) -> None:
    term = Terminal(interactive=False, ascii=True, allow_color=False)
    term.table(["A", "B"], [["1", "2"], ["xx", "yy"]])
    out = capsys.readouterr().out
    assert "A" in out
    assert "B" in out
    assert "1" in out
    assert "yy" in out


def test_section_and_header_do_not_raise(capsys) -> None:
    term = Terminal(interactive=False, ascii=True, allow_color=False)
    term.header("Title", "Subtitle")
    term.section("Check")
    term.arrow("going")
    term.muted("quiet")
    capsule = capsys.readouterr()
    assert "Check" in capsule.out
    assert "Title" in capsule.out


def test_spinner_noop_when_non_interactive() -> None:
    term = Terminal(interactive=False)
    with term.spinner("working") as handle:
        assert handle is not None
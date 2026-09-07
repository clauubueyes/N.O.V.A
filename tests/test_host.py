from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from nova.core.audit import AuditLog
from nova.core.config import HostSettings, PermissionSettings, load_settings
from nova.tools.host import BLOCKED_COMMANDS, all_host_tools
from nova.tools.permissions import PermissionSystem
from nova.tools.registry import create_registry
from nova.tools.runner import ToolRunner

CONFIG_DIR = Path(__file__).resolve().parents[1]


def _runner(tools, *, autonomy="off", allow=(), tmp_path) -> ToolRunner:
    permissions = PermissionSystem(
        PermissionSettings(autonomy=autonomy, allow=list(allow), deny=[])
    )
    return ToolRunner(
        registry=create_registry(tools),
        permissions=permissions,
        audit=AuditLog(str(tmp_path / "audit.jsonl")),
        confirm=lambda _question: False,
    )


def _last_audit(tmp_path) -> dict:
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    return json.loads(lines[-1])


def test_all_host_tools_registers_three_tools() -> None:
    settings = HostSettings(apps={"notepad": "notepad.exe"}, commands=["echo"], timeout_s=5.0)
    tools = all_host_tools(settings)
    names = {tool.name for tool in tools}
    assert {"open_app", "open_url", "run"} <= names


def test_denied_by_default_when_not_allowed(tmp_path) -> None:
    tools = all_host_tools(HostSettings())
    runner = _runner(tools, tmp_path=tmp_path)
    for tool, args in (
        ("open_app", {"app": "notepad"}),
        ("open_url", {"url": "https://example.com"}),
        ("run", {"command": "echo"}),
    ):
        result = runner.run(tool, args)
        assert not result.ok
        assert "permission denied" in result.message


def test_ask_denied_until_confirmed(tmp_path) -> None:
    tools = all_host_tools(HostSettings())
    runner = _runner(tools, autonomy="ask", tmp_path=tmp_path)
    result = runner.run("open_url", {"url": "https://example.com"})
    assert not result.ok
    assert result.message == "permission denied by user"


def test_open_app_launches_configured_app(tmp_path) -> None:
    tools = all_host_tools(HostSettings(apps={"where": "where.exe"}))
    runner = _runner(tools, allow=["open_app"], tmp_path=tmp_path)
    result = runner.run("open_app", {"app": "where"})
    assert result.ok
    assert result.data == {"app": "where"}


def test_open_app_rejects_unconfigured_app(tmp_path) -> None:
    tools = all_host_tools(HostSettings(apps={"where": "where.exe"}))
    runner = _runner(tools, allow=["open_app"], tmp_path=tmp_path)
    result = runner.run("open_app", {"app": "chrome"})
    assert not result.ok
    assert "not configured" in result.message


def test_open_app_never_accepts_arbitrary_path(tmp_path) -> None:
    tools = all_host_tools(HostSettings())
    runner = _runner(tools, allow=["open_app"], tmp_path=tmp_path)
    result = runner.run("open_app", {"app": "C:\\Windows\\System32\\notepad.exe"})
    assert not result.ok
    assert "not configured" in result.message


def test_open_url_opens_valid_https(tmp_path, monkeypatch) -> None:
    opened = []
    monkeypatch.setattr("nova.tools.host.applications.webbrowser.open", opened.append)
    tools = all_host_tools(HostSettings())
    runner = _runner(tools, allow=["open_url"], tmp_path=tmp_path)
    result = runner.run("open_url", {"url": "https://example.com/path?q=1"})
    assert result.ok
    assert opened == ["https://example.com/path?q=1"]


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Users/secret.txt",
        "javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "ftp://files.example.com/pub",
        "ssh://host.example.com",
        "https://",
    ],
)
def test_open_url_rejects_dangerous_or_invalid_urls(tmp_path, url) -> None:
    tools = all_host_tools(HostSettings())
    runner = _runner(tools, allow=["open_url"], tmp_path=tmp_path)
    result = runner.run("open_url", {"url": url})
    assert not result.ok
    assert "unsupported" in result.message


def test_run_blocks_command_not_in_allowlist_even_with_autonomy_full(tmp_path) -> None:
    tools = all_host_tools(HostSettings(commands=[], timeout_s=5.0))
    runner = _runner(tools, autonomy="full", tmp_path=tmp_path)
    result = runner.run("run", {"command": "where"})
    assert not result.ok
    assert "not allowed" in result.message


def test_run_always_blocks_elevation_and_shell_commands(tmp_path) -> None:
    tools = all_host_tools(
        HostSettings(commands=["sudo", "powershell", "cmd", "runas", "git"])
    )
    runner = _runner(tools, autonomy="full", tmp_path=tmp_path)
    for command in ("sudo", "powershell", "cmd", "runas"):
        result = runner.run("run", {"command": command})
        assert not result.ok
        assert "blocked" in result.message


def test_run_allowed_command_captures_output(tmp_path) -> None:
    tools = all_host_tools(HostSettings(commands=[sys.executable], timeout_s=10.0))
    runner = _runner(tools, autonomy="full", tmp_path=tmp_path)
    result = runner.run(
        "run",
        {"command": sys.executable, "args": ["-c", "print('hello from nova')"]},
    )
    assert result.ok
    assert result.data["returncode"] == 0
    assert "hello from nova" in result.data["stdout"]


def test_run_times_out(tmp_path) -> None:
    tools = all_host_tools(HostSettings(commands=[sys.executable], timeout_s=2.0))
    runner = _runner(tools, autonomy="full", tmp_path=tmp_path)
    result = runner.run(
        "run",
        {"command": sys.executable, "args": ["-c", "import time; time.sleep(30)"], "timeout_s": 1},
    )
    assert not result.ok
    assert "timed out" in result.message


def test_run_uses_configured_default_timeout(tmp_path) -> None:
    tools = all_host_tools(HostSettings(commands=[sys.executable], timeout_s=1.0))
    runner = _runner(tools, autonomy="full", tmp_path=tmp_path)
    result = runner.run(
        "run",
        {"command": sys.executable, "args": ["-c", "import time; time.sleep(30)"]},
    )
    assert not result.ok
    assert "timed out" in result.message


def test_run_command_not_found(tmp_path) -> None:
    tools = all_host_tools(HostSettings(commands=["definitely-not-a-real-command-xyz"]))
    runner = _runner(tools, autonomy="full", tmp_path=tmp_path)
    result = runner.run("run", {"command": "definitely-not-a-real-command-xyz"})
    assert not result.ok
    assert "not found" in result.message


def test_run_requires_valid_arguments(tmp_path) -> None:
    tools = all_host_tools(HostSettings(commands=["git"]))
    runner = _runner(tools, autonomy="full", tmp_path=tmp_path)
    result = runner.run("run", {})
    assert not result.ok
    assert "invalid arguments" in result.message


def test_blocked_commands_normalised_include_exe_variants() -> None:
    assert "sudo.exe" in BLOCKED_COMMANDS or "sudo" in BLOCKED_COMMANDS
    assert "powershell.exe" in BLOCKED_COMMANDS


def test_audit_records_allow_and_deny(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("nova.tools.host.applications.webbrowser.open", lambda url: None)
    tools = all_host_tools(HostSettings())
    runner = _runner(tools, allow=["open_url"], tmp_path=tmp_path)
    runner.run("open_url", {"url": "https://example.com"})
    entry = _last_audit(tmp_path)
    assert entry["decision"] == "allow"
    assert entry["tool"] == "open_url"
    assert entry["ok"] is True
    assert entry["args"] == {"url": "https://example.com"}

    runner.run("open_app", {"app": "whatever"})
    entry = _last_audit(tmp_path)
    assert entry["decision"] == "deny"
    assert entry["ok"] is False


def test_host_settings_defaults(monkeypatch, tmp_path) -> None:
    for var in list(__import__("os").environ):
        if var.startswith("NOVA_HOST_"):
            monkeypatch.delenv(var, raising=False)
    settings = load_settings(path=str(tmp_path / "missing.yaml"))
    assert settings.host.apps == {}
    assert settings.host.commands == []
    assert settings.host.timeout_s == 30.0


def test_host_env_override_timeout(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NOVA_HOST_TIMEOUT_S", "45")
    settings = load_settings(path=str(tmp_path / "missing.yaml"))
    assert settings.host.timeout_s == 45.0
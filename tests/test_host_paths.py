from __future__ import annotations

import sys
from pathlib import Path

from nova.core.audit import AuditLog
from nova.core.config import HostSettings, PermissionSettings, load_settings
from nova.tools.host import all_host_tools
from nova.tools.permissions import PermissionSystem
from nova.tools.registry import create_registry
from nova.tools.runner import ToolRunner


def _runner(tools, *, allow=(), tmp_path, autonomy="off") -> ToolRunner:
    permissions = PermissionSystem(
        PermissionSettings(autonomy=autonomy, allow=list(allow), deny=[])
    )
    return ToolRunner(
        registry=create_registry(tools),
        permissions=permissions,
        audit=AuditLog(str(tmp_path / "audit.jsonl")),
        confirm=lambda _q: False,
    )


def _norm_path(value: str) -> str:
    return value.replace("\\", "/").lower()


def test_all_host_tools_includes_bounded_file_tools() -> None:
    tools = all_host_tools(HostSettings())
    names = {tool.name for tool in tools}
    assert {"list_files", "read_file", "write_file", "run"} <= names


def test_file_tools_denied_by_default(tmp_path) -> None:
    tools = all_host_tools(HostSettings(roots=[str(tmp_path)]))
    runner = _runner(tools, tmp_path=tmp_path)
    for tool, args in (
        ("read_file", {"path": (tmp_path / "a.txt").as_posix()}),
        ("write_file", {"path": (tmp_path / "b.txt").as_posix(), "content": "x"}),
        ("list_files", {"path": tmp_path.as_posix()}),
    ):
        result = runner.run(tool, args)
        assert not result.ok
        assert "permission denied" in result.message


def test_file_tools_disabled_without_roots(tmp_path) -> None:
    tools = all_host_tools(HostSettings())
    runner = _runner(tools, allow=["read_file", "list_files"], tmp_path=tmp_path)
    result = runner.run("read_file", {"path": (tmp_path / "a.txt").as_posix()})
    assert not result.ok
    assert "no host.roots configured" in result.message


def test_read_file_within_root(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "notas.txt"
    target.write_text("hola mundo", encoding="utf-8")
    tools = all_host_tools(HostSettings(roots=[str(root)]))
    runner = _runner(tools, allow=["read_file"], tmp_path=tmp_path)
    result = runner.run("read_file", {"path": target.as_posix()})
    assert result.ok
    assert result.data["content"] == "hola mundo"


def test_read_file_outside_root_denied(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    tools = all_host_tools(HostSettings(roots=[str(root)]))
    runner = _runner(tools, allow=["read_file"], tmp_path=tmp_path)
    result = runner.run("read_file", {"path": outside.as_posix()})
    assert not result.ok
    assert "outside allowed roots" in result.message


def test_read_file_traversal_escape_denied(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    tools = all_host_tools(HostSettings(roots=[str(root)]))
    runner = _runner(tools, allow=["read_file"], tmp_path=tmp_path)
    result = runner.run("read_file", {"path": f"{root.name}/../secret.txt"})
    assert not result.ok
    assert "outside allowed roots" in result.message


def test_read_file_nonexistent(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    tools = all_host_tools(HostSettings(roots=[str(root)]))
    runner = _runner(tools, allow=["read_file"], tmp_path=tmp_path)
    result = runner.run("read_file", {"path": (root / "missing.txt").as_posix()})
    assert not result.ok
    assert "file does not exist" in result.message


def test_read_file_too_large(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    big = root / "big.txt"
    big.write_text("x" * 101_000, encoding="utf-8")
    tools = all_host_tools(HostSettings(roots=[str(root)]))
    runner = _runner(tools, allow=["read_file"], tmp_path=tmp_path)
    result = runner.run("read_file", {"path": big.as_posix()})
    assert not result.ok
    assert "too large" in result.message


def test_write_file_within_root(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    tools = all_host_tools(HostSettings(roots=[str(root)]))
    runner = _runner(tools, allow=["write_file"], tmp_path=tmp_path)
    result = runner.run(
        "write_file",
        {"path": (root / "out.txt").as_posix(), "content": "contenido"},
    )
    assert result.ok
    assert (root / "out.txt").read_text(encoding="utf-8") == "contenido"


def test_write_file_outside_root_denied(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    tools = all_host_tools(HostSettings(roots=[str(root)]))
    runner = _runner(tools, allow=["write_file"], tmp_path=tmp_path)
    result = runner.run(
        "write_file",
        {"path": (tmp_path / "evil.txt").as_posix(), "content": "x"},
    )
    assert not result.ok
    assert "outside allowed roots" in result.message


def test_write_file_requires_existing_parent(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    tools = all_host_tools(HostSettings(roots=[str(root)]))
    runner = _runner(tools, allow=["write_file"], tmp_path=tmp_path)
    result = runner.run(
        "write_file",
        {"path": (root / "nested" / "x.txt").as_posix(), "content": "x"},
    )
    assert not result.ok
    assert "parent directory does not exist" in result.message


def test_list_files_within_root(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "sub").mkdir()
    tools = all_host_tools(HostSettings(roots=[str(root)]))
    runner = _runner(tools, allow=["list_files"], tmp_path=tmp_path)
    result = runner.run("list_files", {"path": root.as_posix()})
    assert result.ok
    names = {entry["name"] for entry in result.data["entries"]}
    assert names == {"a.txt", "sub"}


def test_list_files_outside_root_denied(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    tools = all_host_tools(HostSettings(roots=[str(root)]))
    runner = _runner(tools, allow=["list_files"], tmp_path=tmp_path)
    result = runner.run("list_files", {"path": tmp_path.as_posix()})
    assert not result.ok
    assert "outside allowed roots" in result.message


def test_run_with_cwd_inside_root(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    tools = all_host_tools(HostSettings(commands=[sys.executable], roots=[str(root)]))
    runner = _runner(tools, allow=["run"], tmp_path=tmp_path)
    result = runner.run(
        "run",
        {"command": sys.executable, "args": ["-c", "import os; print(os.getcwd())"], "cwd": root.as_posix()},
    )
    assert result.ok
    assert result.data["returncode"] == 0
    assert _norm_path(root.as_posix()) in _norm_path(result.data["stdout"])


def test_run_with_cwd_outside_root_denied(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    tools = all_host_tools(HostSettings(commands=[sys.executable], roots=[str(root)]))
    runner = _runner(tools, allow=["run"], tmp_path=tmp_path)
    result = runner.run(
        "run",
        {"command": sys.executable, "args": ["-c", "print(1)"], "cwd": tmp_path.as_posix()},
    )
    assert not result.ok
    assert "outside allowed roots" in result.message


def test_run_cwd_denied_without_roots(tmp_path) -> None:
    tools = all_host_tools(HostSettings(commands=[sys.executable]))
    runner = _runner(tools, allow=["run"], tmp_path=tmp_path)
    result = runner.run(
        "run",
        {"command": sys.executable, "args": ["-c", "print(1)"], "cwd": tmp_path.as_posix()},
    )
    assert not result.ok
    assert "no host.roots configured" in result.message


def test_run_uses_configured_working_dir(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    tools = all_host_tools(
        HostSettings(commands=[sys.executable], roots=[str(root)], working_dir=str(root))
    )
    runner = _runner(tools, allow=["run"], tmp_path=tmp_path)
    result = runner.run(
        "run",
        {"command": sys.executable, "args": ["-c", "import os; print(os.getcwd())"]},
    )
    assert result.ok
    assert _norm_path(root.as_posix()) in _norm_path(result.data["stdout"])


def test_run_working_dir_outside_roots_denied(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    tools = all_host_tools(
        HostSettings(commands=[sys.executable], roots=[str(root)], working_dir=str(tmp_path))
    )
    runner = _runner(tools, allow=["run"], tmp_path=tmp_path)
    result = runner.run(
        "run",
        {"command": sys.executable, "args": ["-c", "print(1)"]},
    )
    assert not result.ok
    assert "outside allowed roots" in result.message


def test_run_cwd_must_exist(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    tools = all_host_tools(HostSettings(commands=[sys.executable], roots=[str(root)]))
    runner = _runner(tools, allow=["run"], tmp_path=tmp_path)
    result = runner.run(
        "run",
        {"command": sys.executable, "args": ["-c", "print(1)"], "cwd": (root / "ghost").as_posix()},
    )
    assert not result.ok
    assert "working directory does not exist" in result.message


def test_host_roots_defaults_and_env(monkeypatch, tmp_path) -> None:
    for var in list(__import__("os").environ):
        if var.startswith("NOVA_HOST_"):
            monkeypatch.delenv(var, raising=False)
    settings = load_settings(path=str(tmp_path / "missing.yaml"))
    assert settings.host.roots == []
    assert settings.host.working_dir is None

    monkeypatch.setenv("NOVA_HOST_ROOTS", str(tmp_path))
    monkeypatch.setenv("NOVA_HOST_WORKING_DIR", str(tmp_path))
    settings = load_settings(path=str(tmp_path / "missing.yaml"))
    assert settings.host.roots == [str(tmp_path)]
    assert settings.host.working_dir == str(tmp_path)
from __future__ import annotations

import json

from pydantic import BaseModel

from nova.core.audit import AuditLog
from nova.core.config import AutonomyLevel, PermissionSettings
from nova.tools.base import BaseTool, ToolError, ToolResult
from nova.tools.permissions import PermissionDecision, PermissionSystem
from nova.tools.registry import ToolRegistry, create_registry
from nova.tools.runner import ToolRunner
from nova.tools.standard import CalculateTool, DateTimeTool, ListDirTool


def _registry() -> ToolRegistry:
    return create_registry([CalculateTool(), DateTimeTool(), ListDirTool()])


def _permissions(autonomy=AutonomyLevel.ask, allow=None, deny=None) -> PermissionSystem:
    return PermissionSystem(
        PermissionSettings(autonomy=autonomy, allow=allow or [], deny=deny or [])
    )


def _runner(registry, permissions, audit=None, confirm=None) -> ToolRunner:
    return ToolRunner(registry=registry, permissions=permissions, audit=audit, confirm=confirm)


class TestRegistry:
    def test_standard_tools_registered(self) -> None:
        registry = _registry()
        assert registry.names() == ["calculate", "date_time", "list_dir"]

    def test_unknown_tool_rejected(self) -> None:
        runner = _runner(_registry(), _permissions(autonomy=AutonomyLevel.full))
        result = runner.run("nope")
        assert result.ok is False
        assert "Unknown tool" in result.message


class TestCalculate:
    def test_basic_arithmetic(self) -> None:
        runner = _runner(_registry(), _permissions(autonomy=AutonomyLevel.full))
        result = runner.run("calculate", {"expression": "2 + 3 * 4"})
        assert result.ok is True
        assert result.data["result"] == 14

    def test_constants_and_functions(self) -> None:
        runner = _runner(_registry(), _permissions(autonomy=AutonomyLevel.full))
        result = runner.run("calculate", {"expression": "round(sqrt(2), 3) + pi"})
        assert result.ok is True
        assert result.data["result"] == round(2**0.5, 3) + 3.141592653589793

    def test_invalid_expression_is_failure(self) -> None:
        runner = _runner(_registry(), _permissions(autonomy=AutonomyLevel.full))
        result = runner.run("calculate", {"expression": "__import__('os')"})
        assert result.ok is False
        assert "cannot evaluate" in result.message

    def test_missing_required_arg_is_failure(self) -> None:
        runner = _runner(_registry(), _permissions(autonomy=AutonomyLevel.full))
        result = runner.run("calculate", {})
        assert result.ok is False
        assert "invalid arguments" in result.message


class TestDateTime:
    def test_returns_formatted_time(self) -> None:
        runner = _runner(_registry(), _permissions(autonomy=AutonomyLevel.full))
        result = runner.run("date_time", {"format": "%Y"})
        assert result.ok is True
        assert len(result.data["formatted"]) == 4


class TestListDir:
    def test_lists_directory(self, tmp_path) -> None:
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        runner = _runner(_registry(), _permissions(autonomy=AutonomyLevel.full))
        result = runner.run("list_dir", {"path": str(tmp_path)})
        assert result.ok is True
        names = [entry["name"] for entry in result.data["entries"]]
        assert names == ["a.txt", "sub"]

    def test_missing_path_is_failure(self) -> None:
        runner = _runner(_registry(), _permissions(autonomy=AutonomyLevel.full))
        result = runner.run("list_dir", {"path": "Z:/definitely/missing"})
        assert result.ok is False


class TestPermissions:
    def test_allow_rule_runs_directly(self) -> None:
        permissions = _permissions(autonomy=AutonomyLevel.ask, allow=["calculate"])
        assert permissions.authorize("calculate").decision is PermissionDecision.ALLOW
        assert permissions.authorize("list_dir").decision is PermissionDecision.ASK

    def test_deny_overrides_allow(self) -> None:
        permissions = _permissions(autonomy=AutonomyLevel.full, allow=["list_dir"], deny=["list_dir"])
        assert permissions.authorize("list_dir").decision is PermissionDecision.DENY

    def test_autonomy_off_default_deny(self) -> None:
        permissions = _permissions(autonomy=AutonomyLevel.off)
        assert permissions.authorize("calculate").decision is PermissionDecision.DENY
        assert permissions.authorize("other").decision is PermissionDecision.DENY

    def test_autonomy_off_allows_explicit(self) -> None:
        permissions = _permissions(autonomy=AutonomyLevel.off, allow=["calculate"])
        assert permissions.authorize("calculate").decision is PermissionDecision.ALLOW

    def test_autonomy_full_denies_only_explicit(self) -> None:
        permissions = _permissions(autonomy=AutonomyLevel.full, deny=["list_dir"])
        assert permissions.authorize("simple").decision is PermissionDecision.ALLOW
        assert permissions.authorize("list_dir").decision is PermissionDecision.DENY


class TestRunner:
    def test_denied_tool_never_executes(self) -> None:
        executed: list[str] = []

        class SpyTool(BaseTool):
            name = "spy"
            description = ""
            input_schema = type("Empty", (BaseModel,), {})

            def execute(self, params):
                executed.append(self.name)
                return ToolResult.success(self.name)

        permissions = PermissionSystem(PermissionSettings(autonomy=AutonomyLevel.full, deny=["spy"]))
        runner = _runner(create_registry([SpyTool()]), permissions)
        result = runner.run("spy")
        assert result.ok is False
        assert executed == []

    def test_ask_confirmed_runs(self) -> None:
        runner = _runner(_registry(), _permissions(), confirm=lambda _q: True)
        result = runner.run("date_time")
        assert result.ok is True

    def test_ask_denied_blocks(self) -> None:
        runner = _runner(_registry(), _permissions(), confirm=lambda _q: False)
        result = runner.run("date_time")
        assert result.ok is False
        assert "denied by user" in result.message

    def test_audit_records_allow_and_deny(self, tmp_path) -> None:
        audit_path = tmp_path / "audit.jsonl"
        audit = AuditLog(str(audit_path))
        permissions = _permissions(autonomy=AutonomyLevel.ask, allow=["date_time"], deny=["list_dir"])
        runner = _runner(_registry(), permissions, audit=audit, confirm=lambda _q: True)

        allowed = runner.run("date_time")
        denied = runner.run("list_dir")
        assert allowed.ok is True
        assert denied.ok is False

        lines = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        assert lines[0]["tool"] == "date_time"
        assert lines[0]["decision"] == "allow"
        assert lines[0]["ok"] is True
        assert lines[1]["tool"] == "list_dir"
        assert lines[1]["decision"] == "deny"
        assert lines[1]["ok"] is False

    def test_audit_records_validation_error(self, tmp_path) -> None:
        audit_path = tmp_path / "audit.jsonl"
        audit = AuditLog(str(audit_path))
        runner = _runner(_registry(), _permissions(autonomy=AutonomyLevel.full), audit=audit)
        runner.run("calculate", {})
        lines = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        assert lines[0]["decision"] == "error"
        assert lines[0]["ok"] is False
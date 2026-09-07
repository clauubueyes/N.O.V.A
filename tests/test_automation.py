from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

from nova.automation import AutomationExecutor, Scheduler
from nova.automation.scheduler import TaskRunResult, _parse_hhmm, next_run
from nova.automation.workflow import WorkflowEngine
from nova.core.audit import AuditLog
from nova.core.config import (
    AutomationScheduleSettings,
    AutomationSettings,
    AutomationStepSettings,
    AutomationTaskSettings,
    AutomationWorkflowSettings,
    AutonomyLevel,
    PermissionSettings,
    load_settings,
)
from nova.core.logging import get_logger
from nova.tools import registry as base_tools_registry
from nova.tools.permissions import PermissionSystem
from nova.tools.registry import create_registry
from nova.tools.runner import ToolRunner

logger = get_logger("test.automation")


def _runner(registry, permissions, audit_path: str = "logs/_test_automation_audit.jsonl") -> ToolRunner:
    return ToolRunner(
        registry=registry,
        permissions=permissions,
        audit=AuditLog(audit_path),
    )


def _base_registry():
    return create_registry([], base=base_tools_registry)


def _full_runner() -> ToolRunner:
    return _runner(
        _base_registry(),
        PermissionSystem(PermissionSettings(autonomy=AutonomyLevel.full)),
    )


def _task(
    name: str,
    *,
    interval_s: float = 0,
    at: str = "",
    tool: str | None = None,
    args: dict[str, Any] | None = None,
    agent: str | None = None,
    text: str = "",
    workflow: str | None = None,
    enabled: bool = True,
) -> AutomationTaskSettings:
    return AutomationTaskSettings(
        name=name,
        enabled=enabled,
        schedule=AutomationScheduleSettings(interval_s=interval_s, at=at),
        tool=tool,
        args=args or {},
        agent=agent,
        text=text,
        workflow=workflow,
    )


class Team:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _ok_result(task):
    return TaskRunResult(task.name, ok=True, message="done")


class TestScheduleMath:
    def test_interval_next_run_is_strictly_after(self) -> None:
        task = _task("t", interval_s=30)
        start = datetime(2026, 9, 7, 10, 0, 0)
        assert next_run(task, start) == datetime(2026, 9, 7, 10, 0, 30)

    def test_at_next_run_today_when_future(self) -> None:
        task = _task("t", at="10:30")
        start = datetime(2026, 9, 7, 10, 0, 0)
        assert next_run(task, start) == datetime(2026, 9, 7, 10, 30, 0)

    def test_at_rolls_to_tomorrow_when_passed(self) -> None:
        task = _task("t", at="09:00")
        start = datetime(2026, 9, 7, 10, 0, 0)
        assert next_run(task, start) == datetime(2026, 9, 8, 9, 0, 0)

    def test_no_schedule_is_none(self) -> None:
        assert next_run(_task("t"), datetime(2026, 9, 7, 10, 0, 0)) is None

    def test_interval_takes_precedence_over_at(self) -> None:
        task = _task("t", interval_s=60, at="09:00")
        start = datetime(2026, 9, 7, 10, 0, 0)
        assert next_run(task, start) == datetime(2026, 9, 7, 10, 1, 0)

    def test_invalid_at_is_rejected(self) -> None:
        for bad in ("25:00", "ab:cd"):
            try:
                _parse_hhmm(bad)
            except ValueError:
                continue
            raise AssertionError(f"{bad!r} should be rejected")


class TestScheduler:
    def test_disabled_tasks_are_not_scheduled(self) -> None:
        clock = Team(datetime(2026, 9, 7, 10, 0, 0))
        scheduler = Scheduler(
            [_task("on", interval_s=10), _task("off", interval_s=10, enabled=False)],
            clock=clock,
        )
        assert scheduler.task_names == ["on"]

    def test_tasks_without_schedule_are_ignored(self) -> None:
        clock = Team(datetime(2026, 9, 7, 10, 0, 0))
        scheduler = Scheduler([_task("none", tool="date_time")], clock=clock)
        assert scheduler.task_names == []

    def test_due_tasks_are_returned(self) -> None:
        clock = Team(datetime(2026, 9, 7, 10, 0, 0))
        scheduler = Scheduler([_task("t", interval_s=60)], clock=clock)
        assert scheduler.due() == []

        clock.advance(61)
        due = scheduler.due()
        assert [task.name for task in due] == ["t"]
        # reading due() again does not consume it; only run_due reschedules
        assert scheduler.due()[0].name == "t"

    def test_run_due_executes_and_reschedules(self) -> None:
        clock = Team(datetime(2026, 9, 7, 10, 0, 0))
        ran: list[str] = []
        scheduler = Scheduler([_task("t", interval_s=60)], clock=clock)

        def executor(task):
            ran.append(task.name)
            return _ok_result(task)

        clock.advance(120)
        results = scheduler.run_due(executor)
        assert [r.task for r in results] == ["t"]
        assert ran == ["t"]
        # rescheduled strictly after now -> not due yet
        assert scheduler.due() == []
        clock.advance(61)
        assert scheduler.due()[0].name == "t"

    def test_executor_exception_is_captured(self) -> None:
        clock = Team(datetime(2026, 9, 7, 10, 0, 0))
        scheduler = Scheduler([_task("t", interval_s=60)], clock=clock)

        def boom(_task):
            raise RuntimeError("kaboom")

        clock.advance(61)
        results = scheduler.run_due(boom)
        assert results[0].ok is False
        assert "kaboom" in results[0].message

    def test_start_stop_runs_tasks_in_background(self) -> None:
        clock = Team(datetime(2026, 9, 7, 10, 0, 0))
        scheduler = Scheduler([_task("t", interval_s=1)], clock=clock)
        ran: list[str] = []
        scheduler.start(lambda task: ran.append(task.name) or _ok_result(task), poll_s=0.01)

        assert scheduler.running
        clock.advance(2)
        time.sleep(0.15)
        assert ran  # at least one background execution

        scheduler.stop()
        assert not scheduler.running

    def test_status_reports_next_run(self) -> None:
        clock = Team(datetime(2026, 9, 7, 10, 0, 0))
        scheduler = Scheduler([_task("t", interval_s=60)], clock=clock)
        status = scheduler.status()
        assert status[0]["task"] == "t"
        assert "next_run" in status[0]


class TestWorkflowEngine:
    def test_tool_steps_run_in_order(self) -> None:
        engine = WorkflowEngine(_full_runner())
        workflow = AutomationWorkflowSettings(
            name="wf",
            steps=[
                AutomationStepSettings(tool="calculate", args={"expression": "2 + 2"}),
                AutomationStepSettings(tool="date_time"),
            ],
        )
        result = engine.run_workflow(workflow)
        assert result.ok is True
        assert [step.kind for step in result.steps] == ["tool", "tool"]
        assert [step.name for step in result.steps] == ["calculate", "date_time"]
        assert result.steps[0].data["result"] == 4

    def test_denied_step_stops_by_default(self) -> None:
        engine = WorkflowEngine(
            _runner(
                _base_registry(),
                PermissionSystem(PermissionSettings(autonomy=AutonomyLevel.off)),
            )
        )
        workflow = AutomationWorkflowSettings(
            name="wf",
            steps=[
                AutomationStepSettings(tool="calculate", args={"expression": "1+1"}),
                AutomationStepSettings(tool="date_time"),
            ],
        )
        result = engine.run_workflow(workflow)
        assert result.ok is False
        assert len(result.steps) == 1
        assert "permission denied" in result.steps[0].message

    def test_continue_on_error_keeps_going(self) -> None:
        engine = WorkflowEngine(
            _runner(
                _base_registry(),
                PermissionSystem(PermissionSettings(autonomy=AutonomyLevel.off)),
            )
        )
        workflow = AutomationWorkflowSettings(
            name="wf",
            steps=[
                AutomationStepSettings(tool="calculate", args={"expression": "1+1"}, on_error="continue"),
                AutomationStepSettings(tool="date_time", on_error="continue"),
            ],
        )
        result = engine.run_workflow(workflow)
        assert result.ok is True
        assert len(result.steps) == 2
        assert all(step.ok is False for step in result.steps)

    def test_agent_step_uses_factory(self) -> None:
        class FakeAgent:
            def act(self, text: str):
                return type("R", (), {"answer": f"agent said: {text}", "steps": []})()

        engine = WorkflowEngine(
            _full_runner(),
            get_agent=lambda name: FakeAgent() if name == "general" else None,
        )
        workflow = AutomationWorkflowSettings(
            name="wf",
            steps=[AutomationStepSettings(agent="general", text="do it")],
        )
        result = engine.run_workflow(workflow)
        assert result.ok is True
        assert result.steps[0].kind == "agent"
        assert result.steps[0].message == "agent said: do it"

    def test_unknown_agent_step_fails(self) -> None:
        engine = WorkflowEngine(_full_runner(), get_agent=lambda _name: None)
        workflow = AutomationWorkflowSettings(
            name="wf",
            steps=[AutomationStepSettings(agent="ghost", text="x")],
        )
        result = engine.run_workflow(workflow)
        assert result.ok is False
        assert "unknown agent" in result.steps[0].message

    def test_nested_workflow_runs(self) -> None:
        inner = AutomationWorkflowSettings(
            name="inner",
            steps=[AutomationStepSettings(tool="calculate", args={"expression": "6*7"})],
        )
        outer = AutomationWorkflowSettings(
            name="outer",
            steps=[
                AutomationStepSettings(workflow="inner"),
                AutomationStepSettings(tool="date_time"),
            ],
        )
        engine = WorkflowEngine(_full_runner(), workflows={"inner": inner})
        result = engine.run_workflow(outer)
        assert result.ok is True
        assert result.steps[0].kind == "workflow"
        assert result.steps[0].data["steps"][0]["data"]["result"] == 42

    def test_unknown_nested_workflow_fails(self) -> None:
        outer = AutomationWorkflowSettings(
            name="outer",
            steps=[AutomationStepSettings(workflow="missing")],
        )
        engine = WorkflowEngine(_full_runner(), workflows={})
        result = engine.run_workflow(outer)
        assert result.ok is False

    def test_workflow_nesting_too_deep_fails(self) -> None:
        b = AutomationWorkflowSettings(name="b", steps=[AutomationStepSettings(tool="date_time")])
        engine = WorkflowEngine(_full_runner(), workflows={"b": b}, max_depth=1)
        result = engine.run_workflow(
            AutomationWorkflowSettings(
                name="a",
                steps=[AutomationStepSettings(workflow="b")],
            )
        )
        assert result.ok is False
        assert "too deep" in result.steps[0].message

    def test_workflow_by_name(self) -> None:
        engine = WorkflowEngine(
            _full_runner(),
            workflows={
                "wf": AutomationWorkflowSettings(
                    name="wf",
                    steps=[AutomationStepSettings(tool="date_time")],
                )
            },
        )
        assert engine.run_workflow_by_name("wf").ok is True
        assert engine.run_workflow_by_name("nope").ok is False


class TestExecutor:
    def test_task_tool_action(self) -> None:
        executor = AutomationExecutor(_full_runner())
        result = executor.run_task(_task("t", tool="calculate", args={"expression": "2 * 3"}))
        assert result.ok is True
        assert result.data["result"] == 6

    def test_task_workflow_action(self) -> None:
        executor = AutomationExecutor(
            _full_runner(),
            workflows={
                "wf": AutomationWorkflowSettings(
                    name="wf",
                    steps=[AutomationStepSettings(tool="date_time")],
                )
            },
        )
        result = executor.run_task(_task("t", workflow="wf"))
        assert result.ok is True
        assert result.data["workflow"] == "wf"

    def test_task_crashes_are_reported(self) -> None:
        executor = AutomationExecutor(_full_runner())
        saved = executor._engine._runner.run

        def run(_tool, _args):
            raise RuntimeError("boom")

        executor._engine._runner.run = run
        try:
            result = executor.run_task(_task("t", tool="calculate", args={}))
        finally:
            executor._engine._runner.run = saved
        assert result.ok is False
        assert "boom" in result.message

    def test_task_without_action_fails(self) -> None:
        executor = AutomationExecutor(_full_runner())
        result = executor.run_task(_task("t"))
        assert result.ok is False
        assert "no tool/agent/workflow" in result.message

    def test_run_workflow_returns_none_for_unknown(self) -> None:
        executor = AutomationExecutor(_full_runner(), workflows={})
        assert executor.run_workflow("nope") is None


class TestConfig:
    def test_automation_settings_from_yaml(self, tmp_path, monkeypatch) -> None:
        import os

        for var in list(os.environ):
            if var.startswith("NOVA_"):
                monkeypatch.delenv(var, raising=False)
        config = tmp_path / "config.yaml"
        config.write_text(
            "automation:\n"
            "  enabled: true\n"
            "  poll_s: 0.5\n"
            "  tasks:\n"
            "    - name: heartbeat\n"
            "      schedule:\n"
            "        interval_s: 60\n"
            "      tool: date_time\n"
            "  workflows:\n"
            "    - name: daily\n"
            "      steps:\n"
            "        - tool: calculate\n"
            "          args: {expression: '1+1'}\n",
            encoding="utf-8",
        )
        settings = load_settings(path=str(config))
        assert settings.automation.enabled is True
        assert settings.automation.poll_s == 0.5
        assert settings.automation.tasks[0].name == "heartbeat"
        assert settings.automation.tasks[0].schedule.interval_s == 60
        assert settings.automation.tasks[0].tool == "date_time"
        assert settings.automation.workflows[0].name == "daily"
        assert settings.automation.workflows[0].steps[0].tool == "calculate"

    def test_automation_env_overrides(self, tmp_path, monkeypatch) -> None:
        import os

        for var in list(os.environ):
            if var.startswith("NOVA_"):
                monkeypatch.delenv(var, raising=False)
        config = tmp_path / "config.yaml"
        config.write_text("automation:\n  enabled: false\n", encoding="utf-8")
        monkeypatch.setenv("NOVA_AUTOMATION_ENABLED", "true")
        monkeypatch.setenv("NOVA_AUTOMATION_POLL_S", "2.5")
        settings = load_settings(path=str(config))
        assert settings.automation.enabled is True
        assert settings.automation.poll_s == 2.5

    def test_audit_covers_workflow_tool_calls(self, tmp_path) -> None:
        audit_path = tmp_path / "audit.jsonl"
        engine = WorkflowEngine(
            _runner(
                _base_registry(),
                PermissionSystem(PermissionSettings(autonomy=AutonomyLevel.full)),
                audit_path=str(audit_path),
            )
        )
        engine.run_workflow(
            AutomationWorkflowSettings(
                name="wf",
                steps=[AutomationStepSettings(tool="calculate", args={"expression": "4+4"})],
            )
        )
        lines = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        assert lines[0]["tool"] == "calculate"
        assert lines[0]["ok"] is True


class TestApi:
    def test_automation_endpoints(self, tmp_path) -> None:
        import pytest
        from fastapi.testclient import TestClient

        from nova.api.app import create_app
        from nova.llm.base import (
            ChatCompletionRequest,
            ChatCompletionResponse,
            ChatMessage,
            LLMProvider,
            ModelInfo,
        )

        class FakeProvider(LLMProvider):
            supports_embedding = False

            def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
                last_user = next(
                    (m.content for m in reversed(request.messages) if m.role == "user"), ""
                )
                return ChatCompletionResponse(
                    message=ChatMessage(role="assistant", content=f"echo: {last_user}"),
                    model="fake-model",
                )

            def list_models(self) -> list[ModelInfo]:
                return [ModelInfo(name="fake-model", size=1024)]

            def health(self) -> bool:
                return True

            def close(self) -> None:
                pass

        settings = load_settings()
        settings.memory.db_file = str(tmp_path / "memory.db")
        settings.audit.file = str(tmp_path / "audit_api.jsonl")
        settings.automation.enabled = True
        settings.automation.tasks = [_task("t", interval_s=60, tool="date_time")]
        settings.automation.workflows = [
            AutomationWorkflowSettings(
                name="wf",
                steps=[AutomationStepSettings(tool="calculate", args={"expression": "2+2"})],
            )
        ]

        with TestClient(create_app(settings, provider=FakeProvider())) as client:
            status = client.get("/v1/automation").json()
            assert status["enabled"] is True
            assert status["scheduler"][0]["task"] == "t"

            wf = client.post("/v1/automation/workflows/wf/run").json()
            assert wf["ok"] is True
            assert wf["steps"][0]["data"]["result"] == 4

            missing = client.post("/v1/automation/workflows/nope/run")
            assert missing.status_code == 404

            task = client.post("/v1/automation/tasks/t/run").json()
            assert task["ok"] is True

            task_missing = client.post("/v1/automation/tasks/nope/run")
            assert task_missing.status_code == 404
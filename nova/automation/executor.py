from __future__ import annotations

from typing import Any, Callable

from nova.automation.scheduler import TaskRunResult
from nova.automation.workflow import WorkflowEngine, WorkflowResult
from nova.core.config import AutomationTaskSettings, AutomationWorkflowSettings
from nova.core.logging import get_logger
from nova.tools.base import ToolResult
from nova.tools.runner import ToolRunner

logger = get_logger("automation.executor")


class AutomationExecutor:
    """Runs the action of an automation task/workflow step.

    Tool calls go through the `ToolRunner` (Permission System + audit). Agent
    steps/tasks use the injected `get_agent` factory; workflow references are
    resolved from the configured workflows. Results are normalized to
    `TaskRunResult` for the scheduler.
    """

    def __init__(
        self,
        runner: ToolRunner,
        *,
        get_agent: Callable[[str], Any | None] | None = None,
        workflows: dict[str, AutomationWorkflowSettings] | None = None,
    ) -> None:
        self._runner = runner
        self._get_agent = get_agent or (lambda _name: None)
        self._engine = WorkflowEngine(
            runner,
            get_agent=self._get_agent,
            workflows=workflows or {},
        )

    def run_task(self, task: AutomationTaskSettings) -> TaskRunResult:
        if not task.tool and not task.agent and not task.workflow:
            return TaskRunResult(task.name, ok=False, message="task has no tool/agent/workflow action")
        try:
            result = self._engine.run_task(task)
        except Exception as exc:  # noqa: BLE001 - report as a failed run
            logger.error("task %s crashed: %s", task.name, exc)
            return TaskRunResult(task.name, ok=False, message=f"error: {exc}")
        if isinstance(result, ToolResult):
            return TaskRunResult(task.name, ok=result.ok, message=result.message, data=result.data)
        if isinstance(result, WorkflowResult):
            return TaskRunResult(task.name, ok=result.ok, message=result.message, data=result.to_dict())
        if isinstance(result, dict):
            return TaskRunResult(task.name, ok=bool(result.get("ok")), message=str(result.get("message", "")))
        try:
            answer = getattr(result, "answer", str(result))
        except Exception:  # noqa: BLE001
            answer = ""
        return TaskRunResult(task.name, ok=True, message=str(answer))

    def run_workflow(self, name: str) -> WorkflowResult | None:
        if name not in self._engine.workflows:
            return None
        return self._engine.run_workflow_by_name(name)
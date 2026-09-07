from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from nova.core.config import (
    AutomationTaskSettings,
    AutomationWorkflowSettings,
)
from nova.core.logging import get_logger
from nova.tools.runner import ToolRunner

logger = get_logger("automation.workflow")


@dataclass
class StepResult:
    step: int
    kind: str  # "tool" | "agent" | "workflow"
    name: str
    ok: bool
    message: str = ""
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "kind": self.kind,
            "name": self.name,
            "ok": self.ok,
            "message": self.message,
            "data": self.data,
        }


@dataclass
class WorkflowResult:
    workflow: str
    ok: bool
    steps: list[StepResult] = field(default_factory=list)

    @property
    def message(self) -> str:
        if self.ok:
            return f"{len(self.steps)} steps ok"
        return f"workflow stopped at step {len(self.steps)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "ok": self.ok,
            "steps": [step.to_dict() for step in self.steps],
        }


class WorkflowEngine:
    """Executes multi-step workflows (PHASE 12).

    Every step goes through the same decision layer as any manual tool call: the
    `ToolRunner` (Permission System + audit). An AGENT step runs a full agent turn
    (LLM proposes, N.O.V.A. decides); a WORKFLOW step runs another configured
    workflow by name (cycle-guarded). `on_error: stop` (default) aborts on the
    first failed step; `continue` keeps going.
    """

    def __init__(
        self,
        runner: ToolRunner,
        *,
        get_agent: Callable[[str], Any | None] | None = None,
        workflows: dict[str, AutomationWorkflowSettings] | None = None,
        max_depth: int = 5,
    ) -> None:
        self._runner = runner
        self._get_agent = get_agent or (lambda _name: None)
        self._workflows = workflows or {}
        self._max_depth = max_depth
        self.workflows = self._workflows

    def run_workflow_by_name(self, name: str) -> WorkflowResult:
        workflow = self._workflows.get(name)
        if workflow is None:
            return WorkflowResult(
                name,
                ok=False,
                steps=[StepResult(0, "workflow", name, False, "unknown workflow")],
            )
        return self.run_workflow(workflow)

    def run_workflow(
        self,
        workflow: AutomationWorkflowSettings,
        *,
        _depth: int = 0,
    ) -> WorkflowResult:
        if _depth >= self._max_depth:
            return WorkflowResult(
                workflow.name,
                ok=False,
                steps=[
                    StepResult(0, "workflow", workflow.name, False, "workflow nesting too deep")
                ],
            )
        steps: list[StepResult] = []
        for index, step in enumerate(workflow.steps, start=1):
            result = self._run_step(workflow, step, index, _depth)
            steps.append(result)
            if not result.ok and step.on_error.strip().lower() != "continue":
                return WorkflowResult(workflow.name, ok=False, steps=steps)
        return WorkflowResult(workflow.name, ok=True, steps=steps)

    def run_task(self, task: AutomationTaskSettings) -> Any:
        """Execute the action of a scheduled task (tool / agent / workflow)."""
        if task.workflow:
            nested = self._workflows.get(task.workflow)
            if nested is None:
                return WorkflowResult(task.workflow, ok=False, steps=[])
            return self.run_workflow(nested)
        if task.tool:
            return self._runner.run(task.tool, task.args)
        if task.agent:
            agent = self._get_agent(task.agent)
            if agent is None:
                logger.warning("task %s: unknown agent %r", task.name, task.agent)
                return {
                    "ok": False,
                    "message": f"unknown agent {task.agent!r}",
                }
            return agent.act(task.text or "")
        return {
            "ok": False,
            "message": "task has no tool/agent/workflow action",
        }

    def _run_step(
        self,
        workflow: AutomationWorkflowSettings,
        step: Any,
        index: int,
        depth: int,
    ) -> StepResult:
        if step.workflow:
            nested = self._workflows.get(step.workflow)
            if nested is None:
                return StepResult(index, "workflow", step.workflow, False, "unknown workflow")
            if depth + 1 >= self._max_depth:
                return StepResult(index, "workflow", step.workflow, False, "workflow nesting too deep")
            inner = self.run_workflow(nested, _depth=depth + 1)
            return StepResult(
                index,
                "workflow",
                step.workflow,
                ok=inner.ok,
                message="; ".join(s.message for s in inner.steps),
                data={"steps": [s.to_dict() for s in inner.steps]},
            )
        if step.tool:
            result = self._runner.run(step.tool, step.args)
            return StepResult(
                index,
                "tool",
                step.tool,
                ok=result.ok,
                message=result.message,
                data=result.data,
            )
        if step.agent:
            agent = self._get_agent(step.agent)
            if agent is None:
                return StepResult(
                    index, "agent", step.agent, False, f"unknown agent {step.agent!r}"
                )
            try:
                result = agent.act(step.text or "")
            except Exception as exc:  # noqa: BLE001 - provider errors are step failures
                logger.warning("workflow %s agent step failed: %s", workflow.name, exc)
                return StepResult(index, "agent", step.agent, False, f"agent error: {exc}")
            return StepResult(
                index,
                "agent",
                step.agent,
                ok=True,
                message=result.answer,
                data={"steps": [s.to_dict() for s in result.steps]},
            )
        return StepResult(index, "tool", "", False, "step has no tool/agent/workflow")
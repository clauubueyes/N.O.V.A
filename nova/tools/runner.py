from __future__ import annotations

import time
from typing import Any, Callable

from nova.core.audit import AuditLog
from nova.core.logging import get_logger
from nova.tools.base import BaseTool, ToolArgumentError, ToolError, ToolResult
from nova.tools.permissions import PermissionDecision, PermissionSystem
from nova.tools.registry import ToolRegistry

logger = get_logger("tools.runner")

_MAX_VALUE_CHARS = 300
_MASKED_VALUE = "<redacted>"
# Tools whose arguments may carry private content (file bodies, memory notes,
# credentials). The audit log records their keys but never their values.
_CONTENT_TOOLS = frozenset(
    {"write_file", "delete_file", "read_file", "remember", "system_config", "install_software"}
)


def _scrub_args(tool_name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """Redact sensitive/large values before they reach the audit log."""
    if not args:
        return {}
    mask_values = tool_name in _CONTENT_TOOLS
    scrubbed: dict[str, Any] = {}
    for key, value in args.items():
        if mask_values:
            scrubbed[key] = _MASKED_VALUE
        elif isinstance(value, str):
            scrubbed[key] = value[:_MAX_VALUE_CHARS]
            if len(value) > _MAX_VALUE_CHARS:
                scrubbed[key] += f"... ({len(value)} chars)"
        elif isinstance(value, (list, tuple, set)):
            entries = []
            total = 0
            for item in list(value)[:8]:
                text = str(item)
                entries.append(text[:_MAX_VALUE_CHARS])
                total += len(str(item))
            scrubbed[key] = entries
            if len(value) > 8 or total > _MAX_VALUE_CHARS:
                scrubbed[key].append(f"... ({len(value)} items)")
        else:
            scrubbed[key] = value
    return scrubbed


def _scrub_data(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Truncate verbose tool output (file contents, command stdout) for the audit."""
    if not data:
        return data
    scrubbed: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            scrubbed[key] = value[:_MAX_VALUE_CHARS]
            if len(value) > _MAX_VALUE_CHARS:
                scrubbed[key] += f"... ({len(value)} chars)"
        elif isinstance(value, list):
            scrubbed[key] = value[:8]
            if len(value) > 8:
                scrubbed[key] = scrubbed[key] + [f"... ({len(value)} items)"]
        else:
            scrubbed[key] = value
    return scrubbed


class ToolRunner:
    """Executes a tool through permission check -> validation -> execution, auditing every step."""

    def __init__(
        self,
        registry: ToolRegistry,
        permissions: PermissionSystem,
        audit: AuditLog | None = None,
        confirm: Callable[[str], bool] | None = None,
        confirm_action: Callable[[str, dict], bool] | None = None,
    ) -> None:
        self._registry = registry
        self._permissions = permissions
        self._audit = audit
        self._confirm = confirm or (lambda _question: False)
        self._confirm_action = confirm_action

    @property
    def tools(self) -> list[BaseTool]:
        return self._registry.all()

    def run(self, tool_name: str, args: dict[str, Any] | None = None) -> ToolResult:
        try:
            tool = self._registry.get(tool_name)
        except ValueError as exc:
            self._audit_entry("error", tool_name, args, ok=False, message=str(exc))
            return ToolResult.failure(tool_name, str(exc))

        authorization = self._permissions.authorize(tool.name)
        if authorization.decision is PermissionDecision.DENY:
            logger.warning("tool %s denied: %s", tool.name, authorization.reason)
            self._audit_entry("deny", tool.name, args, ok=False, message=authorization.reason)
            return ToolResult.failure(tool.name, f"permission denied: {authorization.reason}")

        if authorization.decision is PermissionDecision.ASK:
            prompt = self._prompt(tool, args)
            granted = (self._confirm_action(tool.name, args or {}) if self._confirm_action
                       else self._confirm(prompt))
            if not granted:
                logger.warning("tool %s denied by user", tool.name)
                self._audit_entry("ask:deny", tool.name, args, ok=False, message="denied by user")
                return ToolResult.failure(tool.name, "permission denied by user")
            decision = "ask:allow"
        else:
            decision = "allow"

        try:
            params = tool.validate(args)
        except ToolArgumentError as exc:
            logger.warning("tool %s invalid args: %s", tool.name, exc)
            self._audit_entry("error", tool.name, args, ok=False, message=str(exc))
            return ToolResult.failure(tool.name, str(exc))

        started = time.perf_counter()
        try:
            result = tool.execute(params)
        except ToolError as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.warning("tool %s failed: %s", tool.name, exc)
            self._audit_entry(decision, tool.name, args, ok=False, message=str(exc), duration_ms=duration_ms)
            return ToolResult.failure(tool.name, str(exc))
        except ValueError as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.warning("tool %s raised: %s", tool.name, exc)
            self._audit_entry(decision, tool.name, args, ok=False, message=str(exc), duration_ms=duration_ms)
            return ToolResult.failure(tool.name, str(exc))

        duration_ms = (time.perf_counter() - started) * 1000
        self._audit_entry(
            decision,
            tool.name,
            args,
            ok=result.ok,
            message=result.message,
            data=result.data,
            duration_ms=duration_ms,
        )
        logger.info("tool %s ok (%.1f ms)", tool.name, duration_ms)
        return result

    def _prompt(self, tool: BaseTool, args: dict[str, Any] | None) -> str:
        arg_summary = ", ".join(f"{key}={value}" for key, value in (args or {}).items())
        description = f" ({tool.description})" if tool.description else ""
        question = f"Allow tool '{tool.name}'{description}"
        if arg_summary:
            question += f" with [{arg_summary}]"
        question += "? [y/N] "
        return question

    def _audit_entry(
        self,
        decision: str,
        tool_name: str,
        args: dict[str, Any] | None,
        *,
        ok: bool,
        message: str = "",
        data: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> None:
        if self._audit is None:
            return
        try:
            self._audit.record(
                tool=tool_name,
                decision=decision,
                args=_scrub_args(tool_name, args or {}),
                ok=ok,
                message=message,
                data=_scrub_data(data),
                duration_ms=duration_ms,
            )
        except Exception as exc:  # audit must never break execution
            logger.warning("audit failed for %s: %s", tool_name, exc)

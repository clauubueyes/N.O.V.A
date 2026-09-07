from __future__ import annotations

import shutil
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from nova.tools.base import BaseTool, ToolError, ToolResult
from nova.tools.host.paths import PathBounds

# Commands that are NEVER allowed, regardless of the allowlist configuration:
# privilege elevation, escape hatches to a shell (which bypass the allowlist) and
# unrecoverable/destructive system operations.
BLOCKED_COMMANDS = {
    "runas",
    "sudo",
    "gsudo",
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "bash",
    "sh",
    "wsl",
    "format",
    "diskpart",
    "bcdedit",
    "shutdown",
    "restart",
    "reg",
}

_MAX_CAPTURE = 100_000  # per-stream cap for stdout/stderr kept in the result


class RunArgs(BaseModel):
    command: str = Field(description="Base command name, must be in the host.commands allowlist")
    args: list[str] = Field(default_factory=list, description="Arguments for the command")
    cwd: str | None = None
    timeout_s: float | None = None


class RunTool(BaseTool):
    """Execute an allowlisted base command, capturing stdout/stderr with a timeout.

    Even under `autonomy: full` the process is only allowed when the base command is
    explicitly listed in `host.commands` (config). Elevation commands are always blocked.
    Commands run without a shell (no shell injection).
    """

    name = "run"
    description = "Execute an allowlisted command on this computer, capturing output with a timeout."

    input_schema = RunArgs

    def __init__(
        self,
        allowlist: list[str],
        default_timeout_s: float = 30.0,
        bounds: PathBounds | None = None,
        default_cwd: str | None = None,
    ) -> None:
        self._allow = {_norm(cmd) for cmd in allowlist}
        self._default_timeout = default_timeout_s
        self._bounds = bounds or PathBounds([])
        self._default_cwd = default_cwd

    def execute(self, params: BaseModel) -> ToolResult:
        command = _norm(params.command)
        if command in BLOCKED_COMMANDS:
            return ToolResult.failure(
                self.name,
                f"command {params.command!r} is blocked for safety (elevation/destructive).",
            )
        if command not in self._allow:
            return ToolResult.failure(
                self.name,
                f"command {params.command!r} is not allowed. Add it to host.commands in config/config.yaml.",
            )
        executable = params.command
        resolved = shutil.which(executable)
        if resolved is None:
            raise ToolError(f"command not found: {params.command}")

        cwd = self._resolve_cwd(params.cwd)

        timeout = params.timeout_s or self._default_timeout
        try:
            completed = subprocess.run(
                [resolved, *params.args],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ToolError(f"command not found: {params.command}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolError(
                f"command {params.command!r} timed out after {timeout:g}s"
            ) from exc
        except OSError as exc:
            raise ToolError(f"could not run {params.command!r}: {exc}") from exc

        data: dict[str, Any] = {
            "command": params.command,
            "args": list(params.args),
            "returncode": completed.returncode,
            "cwd": cwd,
            "stdout": completed.stdout[:_MAX_CAPTURE],
            "stderr": completed.stderr[:_MAX_CAPTURE],
        }
        if len(completed.stdout) > _MAX_CAPTURE:
            data["stdout_truncated"] = True
        if len(completed.stderr) > _MAX_CAPTURE:
            data["stderr_truncated"] = True
        return ToolResult.success(
            self.name,
            message=f"exit code {completed.returncode}",
            data=data,
        )

    def _resolve_cwd(self, requested_cwd: str | None) -> str | None:
        cwd = requested_cwd or self._default_cwd
        if cwd is None:
            return None
        resolved = self._bounds.resolve_within(cwd, "working directory")
        if not resolved.is_dir():
            raise ToolError(f"working directory does not exist: {resolved}")
        return str(resolved)


def _norm(command: str) -> str:
    return command.strip().lower().rstrip(".exe")


def all_terminal_tools(
    allowlist: list[str],
    default_timeout_s: float = 30.0,
    bounds: PathBounds | None = None,
    default_cwd: str | None = None,
) -> list[BaseTool]:
    return [RunTool(allowlist, default_timeout_s, bounds, default_cwd)]
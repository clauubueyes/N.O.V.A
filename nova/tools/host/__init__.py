from __future__ import annotations

from nova.core.config import HostSettings
from nova.tools.base import BaseTool
from nova.tools.host.applications import OpenAppTool, OpenUrlTool, all_application_tools
from nova.tools.host.files import ListFilesTool, ReadFileTool, WriteFileTool, all_file_tools
from nova.tools.host.paths import PathBounds
from nova.tools.host.terminal import BLOCKED_COMMANDS, RunTool, all_terminal_tools

__all__ = [
    "BLOCKED_COMMANDS",
    "ListFilesTool",
    "OpenAppTool",
    "OpenUrlTool",
    "PathBounds",
    "ReadFileTool",
    "RunTool",
    "WriteFileTool",
    "all_application_tools",
    "all_file_tools",
    "all_host_tools",
    "all_terminal_tools",
]


def all_host_tools(settings: HostSettings) -> list[BaseTool]:
    bounds = PathBounds(settings.roots)
    return (
        all_application_tools(settings.apps)
        + all_terminal_tools(settings.commands, settings.timeout_s, bounds, settings.working_dir)
        + all_file_tools(bounds)
    )
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from nova.tools.base import BaseTool


class Plugin(ABC):
    """A self-contained extension that provides tools to N.O.V.A.

    A plugin is a named factory of `BaseTool` instances. N.O.V.A. never calls
    arbitrary code from the LLM: it only instantiates the plugin's tools and
    registers them in a `ToolRegistry`, so every capability still goes through
    the `PermissionSystem` + audit of the `ToolRunner` (the plugin does not
    bypass any safety layer).
    """

    name: str = ""
    description: str = ""

    @abstractmethod
    def tools(self) -> list[BaseTool]:
        """Return the tools this plugin provides (empty = plugin with no tools)."""

    def close(self) -> None:
        """Release any resource the plugin holds (optional override)."""


@dataclass
class PluginInfo:
    name: str
    description: str
    tools: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "tools": self.tools,
        }

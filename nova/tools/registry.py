from __future__ import annotations

from nova.tools.base import BaseTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError:
            raise ValueError(
                f"Unknown tool: {name!r}. Available: {sorted(self._tools)}"
            ) from None

    def all(self) -> list[BaseTool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return sorted(self._tools)


registry = ToolRegistry()


def create_registry(
    tools: list[BaseTool],
    *,
    base: ToolRegistry | None = None,
) -> ToolRegistry:
    """Build a registry (optionally seeded from an existing one for tests)."""
    new_registry = ToolRegistry()
    if base is not None:
        for tool in base.all():
            new_registry.register(tool)
    for tool in tools:
        new_registry.register(tool)
    return new_registry
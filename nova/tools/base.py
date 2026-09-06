from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError


class ToolError(Exception):
    """Base error raised by tools during execution."""


class ToolArgumentError(ToolError):
    """Raised when a tool receives arguments that do not match its schema."""


@dataclass
class ToolResult:
    tool: str
    ok: bool
    message: str = ""
    data: dict[str, Any] | None = None

    @classmethod
    def success(cls, tool: str, message: str = "", data: dict[str, Any] | None = None) -> "ToolResult":
        return cls(tool=tool, ok=True, message=message, data=data)

    @classmethod
    def failure(cls, tool: str, message: str, data: dict[str, Any] | None = None) -> "ToolResult":
        return cls(tool=tool, ok=False, message=message, data=data)

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "ok": self.ok, "message": self.message, "data": self.data}


class BaseTool(ABC):
    """A capability: schema-based argument validation + typed execution."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    input_schema: ClassVar[type[BaseModel]]

    def validate(self, args: dict[str, Any] | None) -> BaseModel:
        try:
            return self.input_schema(**(args or {}))
        except ValidationError as exc:
            details = [f"{error['loc'][0]}: {error['msg']}" for error in exc.errors()]
            raise ToolArgumentError(f"invalid arguments for {self.name}: {', '.join(details)}") from exc

    @abstractmethod
    def execute(self, params: BaseModel) -> ToolResult:
        """Execute the tool with validated parameters and return a typed result."""
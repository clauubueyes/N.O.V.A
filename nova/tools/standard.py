from __future__ import annotations

import ast
import operator
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from nova.tools.base import BaseTool, ToolArgumentError, ToolError, ToolResult

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "int": int,
    "float": float,
    "sqrt": lambda value: value**0.5,
}
_CONSTANTS = {"pi": 3.141592653589793, "e": 2.718281828459045}


def _eval_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        func_name = node.func.id
        if node.keywords:
            raise ToolError(f"keyword arguments are not supported: {func_name}(...)")
        if func_name not in _FUNCTIONS:
            raise ToolError(f"function not allowed: {func_name}")
        return _FUNCTIONS[func_name](*[_eval_node(arg) for arg in node.args])
    raise ToolError("expression not supported")


def _safe_eval(expression: str) -> int | float:
    tree = ast.parse(expression, mode="eval")
    return _eval_node(tree.body)


class CalculateArgs(BaseModel):
    expression: str


class CalculateTool(BaseTool):
    name = "calculate"
    description = "Evaluate a safe arithmetic expression (numbers, + - * / % **, pi/e, abs/round/min/max/int/float/sqrt)."
    input_schema = CalculateArgs

    def execute(self, params: CalculateArgs) -> ToolResult:
        try:
            result = _safe_eval(params.expression)
        except (SyntaxError, ToolError) as exc:
            raise ToolError(f"cannot evaluate: {exc}") from exc
        return ToolResult.success(self.name, data={"expression": params.expression, "result": result})


class DateTimeArgs(BaseModel):
    format: str = "%Y-%m-%d %H:%M:%S %z"


class DateTimeTool(BaseTool):
    name = "date_time"
    description = "Return the current local date and time using a strftime format."
    input_schema = DateTimeArgs

    def execute(self, params: DateTimeArgs) -> ToolResult:
        now = datetime.now().astimezone()
        try:
            formatted = now.strftime(params.format)
        except (TypeError, ValueError) as exc:
            raise ToolError(f"invalid format: {exc}") from exc
        return ToolResult.success(self.name, data={"formatted": formatted})


class ListDirArgs(BaseModel):
    path: str = "."
    show_hidden: bool = False


class ListDirTool(BaseTool):
    name = "list_dir"
    description = "List the entries of a directory (name, kind, size). Read-only."
    input_schema = ListDirArgs

    def execute(self, params: ListDirArgs) -> ToolResult:
        directory = Path(params.path).expanduser()
        if not directory.exists():
            raise ToolError(f"path does not exist: {directory}")
        if not directory.is_dir():
            raise ToolError(f"not a directory: {directory}")
        entries = []
        for entry in directory.iterdir():
            if entry.name.startswith(".") and not params.show_hidden:
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            entries.append(
                {
                    "name": entry.name,
                    "kind": "dir" if entry.is_dir() else "file",
                    "size": size,
                }
            )
        return ToolResult.success(
            self.name,
            message=f"{len(entries)} entries",
            data={"path": str(directory), "entries": entries},
        )


def all_standard_tools() -> list[BaseTool]:
    return [CalculateTool(), DateTimeTool(), ListDirTool()]
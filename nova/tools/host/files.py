from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from nova.tools.base import BaseTool, ToolError, ToolResult
from nova.tools.host.paths import PathBounds

_READ_CAP = 100_000
_WRITE_CAP = 1_000_000


class ListFilesArgs(BaseModel):
    path: str = "."


class ListFilesTool(BaseTool):
    """List the entries of a directory inside host.roots (read-only)."""

    name = "list_files"
    description = "List entries (name, kind, size) of a directory inside the configured host.roots. Read-only."

    input_schema = ListFilesArgs

    def __init__(self, bounds: PathBounds) -> None:
        self._bounds = bounds

    def execute(self, params: BaseModel) -> ToolResult:
        directory = self._bounds.resolve_within(params.path, "path")
        if not directory.is_dir():
            raise ToolError(f"not a directory: {directory}")
        entries = []
        for entry in directory.iterdir():
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


class ReadFileArgs(BaseModel):
    path: str


class ReadFileTool(BaseTool):
    """Read a text file inside host.roots (read-only)."""

    name = "read_file"
    description = "Read a text file inside the configured host.roots (capped size). Read-only."

    input_schema = ReadFileArgs

    def __init__(self, bounds: PathBounds) -> None:
        self._bounds = bounds

    def execute(self, params: BaseModel) -> ToolResult:
        target = self._bounds.resolve_within(params.path, "path")
        if not target.is_file():
            raise ToolError(f"file does not exist: {target}")
        try:
            size = target.stat().st_size
            if size > _READ_CAP:
                raise ToolError(
                    f"file too large to read: {size} bytes (max {_READ_CAP})"
                )
            content = target.read_text(encoding="utf-8")
        except ToolError:
            raise
        except OSError as exc:
            raise ToolError(f"could not read {target}: {exc}") from exc
        return ToolResult.success(
            self.name,
            message=f"read {size} bytes",
            data={"path": str(target), "size": size, "content": content},
        )


class WriteFileArgs(BaseModel):
    path: str
    content: str


class WriteFileTool(BaseTool):
    """Write a text file strictly inside host.roots (no directory creation)."""

    name = "write_file"
    description = "Write a text file inside the configured host.roots; the parent directory must already exist."

    input_schema = WriteFileArgs

    def __init__(self, bounds: PathBounds) -> None:
        self._bounds = bounds

    def execute(self, params: BaseModel) -> ToolResult:
        target = self._bounds.resolve_within(params.path, "path")
        if len(params.content) > _WRITE_CAP:
            raise ToolError(f"content too large: {len(params.content)} chars (max {_WRITE_CAP})")
        parent = target.parent
        if not parent.is_dir():
            raise ToolError(f"parent directory does not exist: {parent}")
        try:
            target.write_text(params.content, encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"could not write {target}: {exc}") from exc
        return ToolResult.success(
            self.name,
            message=f"wrote {target}",
            data={"path": str(target), "bytes": len(params.content.encode("utf-8"))},
        )


def all_file_tools(bounds: PathBounds) -> list[BaseTool]:
    return [ListFilesTool(bounds), ReadFileTool(bounds), WriteFileTool(bounds)]
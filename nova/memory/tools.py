from __future__ import annotations

from pydantic import BaseModel, Field

from nova.memory.service import MemoryService
from nova.tools.base import BaseTool, ToolResult


class RememberArgs(BaseModel):
    content: str
    kind: str = Field(default="fact", description="Memory kind, e.g. fact, preference, task")
    source: str = Field(default="user", description="Where the memory comes from")


class RememberTool(BaseTool):
    name = "remember"
    description = "Store a fact or detail in persistent memory so it can be retrieved later."

    def __init__(self, service: MemoryService) -> None:
        self._service = service
        self.input_schema = RememberArgs

    def execute(self, params: RememberArgs) -> ToolResult:
        memory_id = self._service.remember(params.content, kind=params.kind, source=params.source)
        return ToolResult.success(
            self.name,
            message=f"stored as memory #{memory_id}",
            data={"id": memory_id, "kind": params.kind},
        )


class MemorySearchArgs(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=20)


class MemorySearchTool(BaseTool):
    name = "memory_search"
    description = "Search persistent memories and past conversation relevant to a query."

    def __init__(self, service: MemoryService) -> None:
        self._service = service
        self.input_schema = MemorySearchArgs

    def execute(self, params: MemorySearchArgs) -> ToolResult:
        hits = self._service.search(params.query, limit=params.limit)
        return ToolResult.success(
            self.name,
            message=f"{len(hits)} results",
            data={"hits": [hit.to_dict() for hit in hits]},
        )


def all_memory_tools(service: MemoryService) -> list[BaseTool]:
    return [RememberTool(service), MemorySearchTool(service)]
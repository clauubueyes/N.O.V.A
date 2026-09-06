from __future__ import annotations

from typing import Callable

from nova.core.logging import get_logger
from nova.memory.retriever import EmbedCallable, MemoryHit, MemoryRetriever
from nova.memory.store import MemoryRecord, MemoryStore

logger = get_logger("memory.service")

_CONTEXT_HEADER = "Relevant memory context:\n"


class MemoryService:
    """High-level memory facade used by the CLI (and later Agents).

    Persists every exchange and explicit facts, and produces retrievable context.
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        embed: EmbedCallable | None = None,
        session_id: str = "default",
        max_context: int = 3,
        similarity_threshold: float = 0.3,
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._max_context = max_context
        self._embed: Callable[[str | list[str]], list[list[float]]] | None = embed
        self._retriever = MemoryRetriever(
            store,
            embed=embed,
            session_id=session_id,
            similarity_threshold=similarity_threshold,
        )

    @property
    def session_id(self) -> str:
        return self._session_id

    def remember(self, content: str, *, kind: str = "fact", source: str = "manual") -> int:
        embedding = self._try_embed(content)
        return self._store.add_memory(
            content,
            kind=kind,
            source=source,
            session_id=self._session_id,
            embedding=embedding,
        )

    def record(self, role: str, content: str) -> int:
        embedding = self._try_embed(content)
        return self._store.add_transcript(
            role,
            content,
            session_id=self._session_id,
            embedding=embedding,
        )

    def search(self, query: str, *, limit: int = 5) -> list[MemoryHit]:
        return self._retriever.search(query, limit=limit)

    def context(self, query: str, *, limit: int | None = None) -> str:
        """Format the top relevant memories/transcripts for a query as a context block."""
        hits = self.search(query, limit=limit or self._max_context)
        if not hits:
            return ""
        lines = [_CONTEXT_HEADER.rstrip()]
        for hit in hits:
            tag = "memory" if hit.source == "memory" else "past"
            lines.append(f"- [{tag}] ({hit.created_at}): {hit.content}")
        return "\n".join(lines)

    def recent_memories(self, *, limit: int = 10) -> list[MemoryRecord]:
        return self._store.list_memories(session_id=self._session_id, limit=limit)

    def delete_memory(self, memory_id: int) -> bool:
        return self._store.delete_memory(memory_id)

    def close(self) -> None:
        self._store.close()

    def _try_embed(self, text: str) -> list[float] | None:
        from nova.llm.base import NOVAProviderError  # local import to keep memory decoupled

        if self._embed is None:
            return None
        try:
            vectors = self._embed(text)
        except NOVAProviderError:
            logger.warning("embedding failed, storing without vector")
            return None
        return vectors[0] if vectors else None
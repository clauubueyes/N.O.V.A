from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from nova.core.logging import get_logger
from nova.llm.base import NOVAProviderError
from nova.memory.store import MemoryRecord, MemoryStore

logger = get_logger("memory.retriever")


@dataclass
class MemoryHit:
    content: str
    source: str
    kind: str
    created_at: str
    similarity: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "source": self.source,
            "kind": self.kind,
            "created_at": self.created_at,
            "similarity": round(self.similarity, 4),
        }


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


EmbedCallable = Callable[[str | list[str]], list[list[float]]]


class MemoryRetriever:
    """Retrieves relevant memories and past conversation for a query.

    Uses cosine similarity over stored embeddings when a provider is available;
    otherwise falls back to keyword matching so retrieval keeps working offline.
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        embed: EmbedCallable | None = None,
        session_id: str = "default",
        similarity_threshold: float = 0.3,
    ) -> None:
        self._store = store
        self._embed = embed
        self._session_id = session_id
        self._threshold = similarity_threshold

    def search(self, query: str, *, limit: int = 5) -> list[MemoryHit]:
        query_vec = self._try_embed(query)
        memories = self._store.list_memories(session_id=None)
        transcripts = self._store.list_transcripts(session_id=self._session_id)

        hits: list[MemoryHit] = []
        if query_vec is not None:
            hits = self._rank(memories, transcripts, query_vec)
        if not hits:
            hits = self._keyword_matches(memories, transcripts, query)
        return hits[:limit]

    def _rank(
        self,
        memories: list[MemoryRecord],
        transcripts: list[MemoryRecord],
        query_vec: list[float],
    ) -> list[MemoryHit]:
        scored: list[MemoryHit] = []
        for record in memories:
            if record.embedding:
                scored.append(self._hit(record, "memory", cosine_similarity(query_vec, record.embedding)))
        for record in transcripts:
            if record.embedding:
                scored.append(self._hit(record, "transcript", cosine_similarity(query_vec, record.embedding)))
        scored.sort(key=lambda hit: hit.similarity, reverse=True)
        return [hit for hit in scored if hit.similarity >= self._threshold]

    def _keyword_matches(
        self,
        memories: list[MemoryRecord],
        transcripts: list[MemoryRecord],
        query: str,
    ) -> list[MemoryHit]:
        lower = query.lower()
        pieces = [p for p in lower.split() if len(p) > 2]

        def matches(content: str) -> bool:
            content_lower = content.lower()
            if not pieces:
                return bool(lower) and lower in content_lower
            return all(piece in content_lower for piece in pieces)

        hits: list[MemoryHit] = []
        for record in memories:
            if not matches(record.content):
                continue
            hits.append(self._hit(record, "memory", 0.5))
        for record in transcripts:
            if not matches(record.content):
                continue
            hits.append(self._hit(record, "transcript", 0.5))
        return hits

    def _hit(self, record: MemoryRecord, source: str, similarity: float) -> MemoryHit:
        return MemoryHit(
            content=record.content,
            source=source,
            kind=record.kind,
            created_at=record.created_at,
            similarity=similarity,
        )

    def _try_embed(self, query: str) -> list[float] | None:
        if self._embed is None:
            return None
        try:
            vectors = self._embed(query)
        except NOVAProviderError:
            logger.warning("embedding failed, falling back to keyword search")
            return None
        return vectors[0] if vectors else None
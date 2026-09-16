from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from nova.core.logging import get_logger
from nova.memory.retriever import EmbedCallable, cosine_similarity
from nova.rag.splitter import split_text
from nova.rag.store import RagStore

logger = get_logger("rag.service")

_CONTEXT_HEADER = "Relevant document context:\n"
_COMPACT_K = 60  # RRF: higher K = keywords dominate; lower K = embeddings matter more


@dataclass
class RagHit:
    text: str
    document_name: str
    score: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {"text": self.text, "document": self.document_name, "score": round(self.score, 4)}


class RagService:
    """Retrieval facade: indexes documents as chunks and answers queries with
    hybrid FTS5 (BM25) + embedding cosine fusion via Reciprocal Rank Fusion."""

    def __init__(
        self,
        store: RagStore,
        *,
        embed: EmbedCallable | None = None,
        top_k: int = 3,
        chunk_chars: int = 1500,
        overlap_chars: int = 150,
    ) -> None:
        self._store = store
        self._embed: Callable[[str | list[str]], list[list[float]]] | None = embed
        self._top_k = top_k
        self._chunk_chars = chunk_chars
        self._overlap_chars = overlap_chars

    def index_text(self, ref: str, name: str, text: str) -> bool:
        """Chunk and index a text document. Returns True when (re)indexed."""
        chunks = split_text(text, max_chars=self._chunk_chars, overlap=self._overlap_chars)
        if not chunks:
            return False
        embeddings = None
        if self._embed is not None:
            try:
                embeddings = self._try_embed_blocks(chunks)
            except Exception:  # noqa: BLE001 - indexing must degrade to FTS only
                logger.warning("embedding of document %r failed; indexing FTS only", ref)
                embeddings = None
        return self._store.index_document(ref, name, text, chunks, embeddings=embeddings)

    def hits(self, query: str, *, top_k: int | None = None) -> list[RagHit]:
        limit = top_k or self._top_k
        fts = self._store.search_fts(query, limit=max(limit * 2, 8))
        by_id: dict[int, RagHit] = {}
        if fts:
            for rank, chunk in enumerate(fts):
                score = 1.0 / (_COMPACT_K + rank)
                hit = RagHit(chunk.text, chunk.document_name, score)
                by_id.setdefault(chunk.chunk_id, hit)
                by_id[chunk.chunk_id].score = max(by_id[chunk.chunk_id].score, score)

        query_vec = self._try_embed(query)
        if query_vec is not None:
            vectorized = [c for c in self._store.all_chunks_with_embedding() if c.embedding]
            ranked = sorted(
                (
                    (cosine_similarity(query_vec, chunk.embedding or []), chunk)
                    for chunk in vectorized
                ),
                key=lambda pair: pair[0],
                reverse=True,
            )
            for rank, (similarity, chunk) in enumerate(ranked[: max(limit * 2, 8)]):
                if similarity <= 0.0:
                    continue
                score = 1.0 / (_COMPACT_K * 0.6 + rank) + similarity
                hit = RagHit(chunk.text, chunk.document_name, score)
                if chunk.chunk_id in by_id:
                    by_id[chunk.chunk_id].score += score
                else:
                    by_id[chunk.chunk_id] = hit

        results = sorted(by_id.values(), key=lambda hit: hit.score, reverse=True)
        return results[:limit]

    def context(self, query: str, *, top_k: int | None = None) -> str:
        """Format the best chunks for a query as a context block (or empty)."""
        found = self.hits(query, top_k=top_k)
        if not found:
            return ""
        lines = [_CONTEXT_HEADER.rstrip()]
        for hit in found[: top_k or self._top_k]:
            lines.append(f"- [{hit.document_name}]: {hit.text.strip()}")
        return "\n".join(lines)

    def store(self) -> RagStore:
        return self._store

    def _try_embed(self, query: str) -> list[float] | None:
        if self._embed is None:
            return None
        try:
            vectors = self._embed(query)
        except Exception:  # noqa: BLE001
            return None
        return vectors[0] if vectors else None

    def _try_embed_blocks(self, chunks: list[str]) -> list[list[float]]:
        vectors = self._embed(chunks)
        return [list(v) for v in vectors]
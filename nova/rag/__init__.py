from __future__ import annotations

"""Local RAG pipeline: parse -> chunk -> index (FTS5 + embeddings) -> hybrid retrieve.

Small texts are pasted inline (the old behaviour); big documents are chunked and
retrieved by the query instead of dumping whole files into the prompt.
"""

from nova.rag.service import RagHit, RagService
from nova.rag.splitter import split_text
from nova.rag.store import RagStore

__all__ = ["RagHit", "RagService", "RagStore", "split_text"]
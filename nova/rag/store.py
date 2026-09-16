from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from nova.core.logging import get_logger

logger = get_logger("rag.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    chunk_id UNINDEXED,
    content='chunks',
    content_rowid='id'
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RichChunk:
    chunk_id: int
    document_ref: str
    document_name: str
    text: str
    embedding: list[float] | None = None


class RagStore:
    """SQLite-backed chunk store with FTS5 full-text search (keyword fallback).

    ``ref`` uniquely identifies a document (an attachment key or file path) so
    re-indexing the same content is a no-op. Embeddings are optional JSON arrays,
    matching the MemoryStore convention.
    """

    def __init__(self, path: str, *, fts: bool = True) -> None:
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(db_path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._fts = fts
        if fts:
            try:
                self._conn.executescript(_FTS_SCHEMA)
            except sqlite3.OperationalError as exc:  # FTS5 unavailable -> keyword fallback
                logger.warning("FTS5 not available (%s); using LIKE fallback", exc)
                self._fts = False

    def close(self) -> None:
        self._conn.close()

    def has_document(self, ref: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM documents WHERE ref = ?", (ref,)
        ).fetchone()
        return row is not None

    def index_document(self, ref: str, name: str, text: str, chunks: list[str], embeddings: list[list[float]] | None = None) -> bool:
        """Replace the chunks of ``ref``. Returns True when (re)indexed."""
        existing = self._conn.execute("SELECT id FROM documents WHERE ref = ?", (ref,)).fetchone()
        if existing is not None:
            doc_id = existing[0]
            self._conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        else:
            cur = self._conn.execute(
                "INSERT INTO documents (ref, name, added_at) VALUES (?, ?, ?)",
                (ref, name, _now_iso()),
            )
            doc_id = cur.lastrowid
        for position, chunk in enumerate(chunks):
            embedding_json = None
            if embeddings is not None and position < len(embeddings):
                embedding_json = json.dumps(embeddings[position])
            cur = self._conn.execute(
                "INSERT INTO chunks (doc_id, position, text, embedding) VALUES (?, ?, ?, ?)",
                (doc_id, position, chunk, embedding_json),
            )
            if self._fts:
                self._conn.execute(
                    "INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)",
                    (cur.lastrowid, chunk),
                )
        self._conn.commit()
        return True

    def delete_document(self, ref: str) -> bool:
        cur = self._conn.execute("DELETE FROM documents WHERE ref = ?", (ref,))
        self._conn.commit()
        return cur.rowcount > 0

    def search_fts(self, query: str, *, limit: int = 5) -> list[RichChunk]:
        """BM25 keyword search over the FTS index (or LIKE fallback)."""
        words = self._terms(query)
        if not words:
            return []
        if self._fts:
            try:
                rows = self._conn.execute(
                    "SELECT c.id, d.ref, d.name, c.text, c.embedding, bm25(chunks_fts) "
                    "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.chunk_id "
                    "JOIN documents d ON d.id = c.doc_id "
                    "WHERE chunks_fts MATCH ? "
                    "ORDER BY bm25(chunks_fts) LIMIT ?",
                    (" AND ".join(words), limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if rows:
                return [
                    RichChunk(
                        chunk_id=row[0],
                        document_ref=row[1],
                        document_name=row[2],
                        text=row[3],
                        embedding=json.loads(row[4]) if row[4] else None,
                    )
                    for row in rows
                ]
        sql = "SELECT c.id, d.ref, d.name, c.text, c.embedding FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE " + " AND ".join(
            ["c.text LIKE ?"] * len(words)
        )
        params = [f"%{word}%" for word in words]
        rows = self._conn.execute(sql + " LIMIT ?", params + [limit]).fetchall()
        return [
            RichChunk(
                chunk_id=row[0],
                document_ref=row[1],
                document_name=row[2],
                text=row[3],
                embedding=json.loads(row[4]) if row[4] else None,
            )
            for row in rows
        ]

    def all_chunks_with_embedding(self) -> list[RichChunk]:
        rows = self._conn.execute(
            "SELECT c.id, d.ref, d.name, c.text, c.embedding "
            "FROM chunks c JOIN documents d ON d.id = c.doc_id "
            "WHERE c.embedding IS NOT NULL"
        ).fetchall()
        return [
            RichChunk(
                chunk_id=row[0],
                document_ref=row[1],
                document_name=row[2],
                text=row[3],
                embedding=json.loads(row[4]) if row[4] else None,
            )
            for row in rows
        ]

    def document_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    def _terms(self, query: str) -> list[str]:
        return [w for w in query.lower().split() if w.isalnum() and len(w) > 2][:8]
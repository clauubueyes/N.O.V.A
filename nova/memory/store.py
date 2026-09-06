from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nova.core.logging import get_logger

logger = get_logger("memory.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'fact',
    source TEXT NOT NULL DEFAULT 'manual',
    session_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT NOT NULL,
    embedding TEXT
);
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT NOT NULL,
    embedding TEXT
);
CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_session ON transcripts(session_id);
"""


@dataclass
class MemoryRecord:
    id: int
    content: str
    kind: str
    source: str
    session_id: str
    created_at: str
    embedding: list[float] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """Persistent memory backed by SQLite: facts (`memories`) and conversation (`transcripts`).

    Embeddings are optional and stored as JSON; retrieval code decides whether to use them.
    """

    def __init__(self, path: str) -> None:
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(db_path)
        # check_same_thread=False: FastAPI/uvicorn puede atender peticiones de la misma
        # sesión desde hilos distintos del threadpool. SQLite serializa a nivel de archivo.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)

    def add_memory(
        self,
        content: str,
        *,
        kind: str = "fact",
        source: str = "manual",
        session_id: str = "default",
        embedding: list[float] | None = None,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO memories (content, kind, source, session_id, created_at, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (content, kind, source, session_id, _now_iso(), self._encode(embedding)),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def add_transcript(
        self,
        role: str,
        content: str,
        *,
        session_id: str = "default",
        embedding: list[float] | None = None,
    ) -> int:
        if role not in ("user", "assistant", "system"):
            raise ValueError(f"invalid transcript role: {role!r}")
        cursor = self._conn.execute(
            "INSERT INTO transcripts (role, content, session_id, created_at, embedding) "
            "VALUES (?, ?, ?, ?, ?)",
            (role, content, session_id, _now_iso(), self._encode(embedding)),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def list_memories(self, *, limit: int = 50, session_id: str | None = None) -> list[MemoryRecord]:
        where, params = self._session_filter("memories", session_id)
        rows = self._conn.execute(
            f"SELECT id, content, kind, source, session_id, created_at, embedding "
            f"FROM memories {where} ORDER BY id DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [self._record(row) for row in rows]

    def list_transcripts(
        self, *, limit: int = 100, session_id: str | None = None
    ) -> list[MemoryRecord]:
        where, params = self._session_filter("transcripts", session_id)
        rows = self._conn.execute(
            f"SELECT id, content, role, 'transcript', session_id, created_at, embedding "
            f"FROM transcripts {where} ORDER BY id DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [self._record(row) for row in rows]

    def delete_memory(self, memory_id: int) -> bool:
        cursor = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def clear(self) -> None:
        self._conn.execute("DELETE FROM memories")
        self._conn.execute("DELETE FROM transcripts")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _session_filter(table: str, session_id: str | None) -> tuple[str, list[Any]]:
        if session_id is None:
            return "", []
        return f"WHERE {table}.session_id = ?", [session_id]

    @staticmethod
    def _encode(embedding: list[float] | None) -> str | None:
        if embedding is None:
            return None
        return json.dumps(embedding)

    @staticmethod
    def _record(row: tuple) -> MemoryRecord:
        embedding_raw = row[6]
        return MemoryRecord(
            id=row[0],
            content=row[1],
            kind=row[2],
            source=row[3],
            session_id=row[4],
            created_at=row[5],
            embedding=None if embedding_raw is None else json.loads(embedding_raw),
        )
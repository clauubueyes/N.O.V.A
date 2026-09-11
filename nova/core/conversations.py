from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class ConversationStore:
    """Durable conversation index, independent of the bounded model context."""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    session_id TEXT PRIMARY KEY, agent TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'Nueva conversación',
                    local_only INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES conversations(session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS conversation_messages_session ON conversation_messages(session_id);
            """)
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'transcripts' in tables:
                for (sid,) in db.execute("SELECT DISTINCT session_id FROM transcripts WHERE session_id LIKE 'api-%'").fetchall():
                    key = sid.removeprefix('api-')
                    if len(key) != 32 or any(c not in '0123456789abcdef' for c in key):
                        continue
                    if db.execute('SELECT 1 FROM conversations WHERE session_id=?', (key,)).fetchone():
                        continue
                    rows = db.execute('SELECT role,content FROM transcripts WHERE session_id=? ORDER BY id', (sid,)).fetchall()
                    title = next((text[:70] for role, text in rows if role == 'user'), 'Conversación recuperada')
                    db.execute('INSERT INTO conversations(session_id,model,title) VALUES(?,?,?)', (key, '', title))
                    db.executemany('INSERT INTO conversation_messages(session_id,message) VALUES(?,?)',
                                   [(key, json.dumps(dict(role=role, content=text, images=[]))) for role, text in rows if role != 'system'])

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, sid: str, model: str, agent: str = ''):
        with self.connect() as db:
            db.execute('INSERT INTO conversations(session_id,model,agent) VALUES(?,?,?)', (sid, model, agent))

    def get(self, sid: str) -> dict | None:
        with self.connect() as db:
            row = db.execute('SELECT * FROM conversations WHERE session_id=?', (sid,)).fetchone()
            return dict(row) if row else None

    def list(self) -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT c.*, COUNT(m.id) AS messages FROM conversations c
                LEFT JOIN conversation_messages m USING(session_id) GROUP BY c.session_id ORDER BY c.updated_at DESC''')]

    def messages(self, sid: str) -> list[dict]:
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT message FROM conversation_messages WHERE session_id=? ORDER BY id', (sid,))]

    def append(self, sid: str, messages: list[dict], model: str, local_only: bool = False):
        with self.connect() as db:
            db.executemany('INSERT INTO conversation_messages(session_id,message) VALUES(?,?)',
                           [(sid, json.dumps(m, ensure_ascii=False)) for m in messages])
            title = next((m['content'][:70] for m in messages if m['role'] == 'user'), '')
            db.execute('''UPDATE conversations SET model=?, local_only=MAX(local_only,?),
                title=CASE WHEN title='Nueva conversación' AND ?!='' THEN ? ELSE title END,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE session_id=?''',
                       (model, int(local_only), title, title, sid))

    def delete(self, sid: str) -> bool:
        with self.connect() as db:
            removed = db.execute('DELETE FROM conversations WHERE session_id=?', (sid,)).rowcount > 0
            if removed:
                db.execute('DELETE FROM transcripts WHERE session_id=?', ('api-' + sid,))
            return removed

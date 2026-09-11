from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid

from nova.tools.permissions import SENSITIVE_TOOLS


class ApprovalBroker:
    """Expiring local approvals bound to the exact action and session."""

    def __init__(self, timeout: float = 120):
        self.timeout = timeout
        self._pending: dict[str, dict] = {}
        self._grants: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def key(session: str, tool: str, args: dict) -> str:
        return hashlib.sha256(json.dumps([session, tool, args], sort_keys=True).encode()).hexdigest()

    def confirm(self, session: str, tool: str, args: dict) -> bool:
        key = self.key(session, tool, args)
        with self._lock:
            if key in self._grants and tool not in SENSITIVE_TOOLS:
                return True
            uid = uuid.uuid4().hex
            row = dict(id=uid, session_id=session, tool=tool, args=args, origin='local',
                       expires=time.monotonic() + self.timeout, event=threading.Event(),
                       allowed=False, key=key, remember_allowed=tool not in SENSITIVE_TOOLS)
            self._pending[uid] = row
        try:
            row['event'].wait(self.timeout)
            return bool(row['allowed'])
        finally:
            with self._lock:
                self._pending.pop(uid, None)

    def pending(self) -> list[dict]:
        with self._lock:
            return [{k: row[k] for k in ('id', 'session_id', 'tool', 'args', 'origin', 'remember_allowed')}
                    for row in self._pending.values() if row['expires'] > time.monotonic() and not row['event'].is_set()]

    def resolve(self, uid: str, decision: str) -> bool:
        with self._lock:
            row = self._pending.get(uid)
            if not row or row['event'].is_set() or row['expires'] <= time.monotonic():
                return False
            if decision == 'always' and not row['remember_allowed']:
                return False
            row['allowed'] = decision in ('once', 'always')
            if decision == 'always':
                self._grants.add(row['key'])
            row['event'].set()
            return True

    def cancel_all(self):
        with self._lock:
            self._grants.clear()
            for row in self._pending.values():
                row['allowed'] = False
                row['event'].set()

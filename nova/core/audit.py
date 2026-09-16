from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nova.core.logging import get_logger

logger = get_logger("core.audit")
_MAX_BYTES = 2_000_000
_BACKUP_COUNT = 3


@dataclass
class AuditEntry:
    ts: str
    tool: str
    action: str = "run"
    decision: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    message: str = ""
    data: dict[str, Any] | None = None
    duration_ms: float | None = None


class AuditLog:
    """Append-only JSON-lines audit of every tool execution decision."""

    def __init__(
        self,
        path: str,
        *,
        max_bytes: int = _MAX_BYTES,
        backup_count: int = _BACKUP_COUNT,
    ) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, **fields: Any) -> None:
        entry = AuditEntry(
            ts=datetime.now(timezone.utc).isoformat(),
            **fields,
        )
        line = json.dumps(asdict(entry), ensure_ascii=False, default=str) + "\n"
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line)
            self._rotate_if_needed()
        except OSError as exc:
            logger.warning("audit write failed (path=%s): %s", self.path, exc)

    def _rotate_if_needed(self) -> None:
        if self.path.stat().st_size <= self.max_bytes:
            return
        for index in range(self.backup_count, 0, -1):
            src = self.path if index == 1 else Path(f"{self.path}.{index - 1}")
            dst = Path(f"{self.path}.{index}")
            if src.exists():
                src.replace(dst)
        self.path.touch()

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the last `limit` audit entries, oldest first.

        Reads a bounded tail of the JSONL file so listing never loads the whole
        log, and it skips lines that are not valid JSON without failing.
        """
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError as exc:
            logger.warning("audit read failed (path=%s): %s", self.path, exc)
            return []
        for line in lines[-max(64, limit * 4):]:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
            if len(entries) > limit * 4:
                entries.pop(0)
        return entries[-limit:]
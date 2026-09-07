from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from nova.core.config import AutomationTaskSettings
from nova.core.logging import get_logger

logger = get_logger("automation.scheduler")


@dataclass
class TaskRunResult:
    task: str
    ok: bool
    message: str = ""
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"task": self.task, "ok": self.ok, "message": self.message, "data": self.data}


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid time {value!r}, expected HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour < 24) or not (0 <= minute < 60):
        raise ValueError(f"invalid time {value!r}, expected HH:MM")
    return hour, minute


def next_run(task: AutomationTaskSettings, after: datetime) -> datetime | None:
    """Next moment the task shall fire, strictly after `after`, or None (no schedule)."""
    schedule = task.schedule
    if schedule.interval_s and schedule.interval_s > 0:
        return after + timedelta(seconds=schedule.interval_s)
    if schedule.at:
        hour, minute = _parse_hhmm(schedule.at)
        candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= after:
            candidate += timedelta(days=1)
        return candidate
    return None


class Scheduler:
    """Lightweight scheduler for automation tasks (PHASE 12).

    Pure timing: it decides *when* a task is due and calls an executor callback
    with it. The executor (wired elsewhere to ToolRunner/agents/workflows) decides
    *what* runs — always under the Permission System + audit. A background daemon
    thread poll loop (`start`/`stop`) runs due tasks; `run_due` is exposed for
    tests and single-threaded callers.
    """

    def __init__(
        self,
        tasks: list[AutomationTaskSettings],
        *,
        clock: Callable[[], datetime] | None = None,
        poll_s: float = 1.0,
    ) -> None:
        self._clock = clock or datetime.now
        self._poll_s = poll_s
        self._tasks: dict[str, AutomationTaskSettings] = {}
        self._next: dict[str, datetime] = {}
        for task in tasks:
            if not task.enabled:
                continue
            self._tasks[task.name] = task
            scheduled = next_run(task, self._clock())
            if scheduled is None:
                logger.warning("automation task %r has no valid schedule; ignored", task.name)
                continue
            self._next[task.name] = scheduled

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_results: dict[str, TaskRunResult] = {}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def task_names(self) -> list[str]:
        return sorted(self._next)

    def due(self, now: datetime | None = None) -> list[AutomationTaskSettings]:
        now = now or self._clock()
        with self._lock:
            names = [name for name, when in self._next.items() if now >= when]
        return [self._tasks[name] for name in names]

    def status(self) -> list[dict[str, Any]]:
        now = self._clock()
        rows = []
        for name in sorted(self._next):
            when = self._next[name]
            last = self._last_results.get(name)
            rows.append(
                {
                    "task": name,
                    "next_run": when.isoformat(timespec="seconds"),
                    "seconds_until": max(0.0, (when - now).total_seconds()),
                    "last_ok": last.ok if last else None,
                    "last_message": last.message if last else "",
                }
            )
        return rows

    def run_due(
        self,
        executor: Callable[[AutomationTaskSettings], TaskRunResult],
        now: datetime | None = None,
    ) -> list[TaskRunResult]:
        now = now or self._clock()
        results: list[TaskRunResult] = []
        for task in self.due(now):
            try:
                result = executor(task)
            except Exception as exc:  # noqa: BLE001 - the scheduler must keep running
                logger.error("automation task %s crashed: %s", task.name, exc)
                result = TaskRunResult(task.name, ok=False, message=f"scheduler error: {exc}")
            results.append(result)
            with self._lock:
                self._next[task.name] = next_run(task, now) or (
                    now + timedelta(days=1)
                )
                self._last_results[task.name] = result
            logger.info("automation task %s ok=%s: %s", task.name, result.ok, result.message)
        return results

    def start(
        self,
        executor: Callable[[AutomationTaskSettings], TaskRunResult],
        poll_s: float | None = None,
    ) -> None:
        if self.running:
            return
        self._stop.clear()
        poll = poll_s if poll_s is not None else self._poll_s

        def loop() -> None:
            logger.info("automation scheduler started (poll=%.1fs)", poll)
            while not self._stop.is_set():
                try:
                    self.run_due(executor)
                except Exception as exc:  # noqa: BLE001
                    logger.error("automation scheduler poll failed: %s", exc)
                self._stop.wait(poll)

        self._thread = threading.Thread(target=loop, name="nova-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("automation scheduler stopped")
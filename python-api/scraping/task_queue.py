from __future__ import annotations

from collections import deque
from typing import Protocol

from scraping.task_models import (
    ScrapeTask,
    TASK_FAILED,
    TASK_PENDING,
    TASK_RETRY,
    TASK_RUNNING,
    TASK_SKIP,
    TASK_SUCCESS,
)


class TaskQueueProtocol(Protocol):
    def enqueue(self, task: ScrapeTask) -> None: ...
    def dequeue(self) -> ScrapeTask | None: ...
    def requeue(self, task: ScrapeTask) -> None: ...
    def mark_running(self, task: ScrapeTask) -> None: ...
    def mark_success(self, task: ScrapeTask) -> None: ...
    def mark_skip(self, task: ScrapeTask) -> None: ...
    def mark_retry(self, task: ScrapeTask, error: str | None = None) -> None: ...
    def mark_failed(self, task: ScrapeTask, error: str | None = None) -> None: ...
    def has_items(self) -> bool: ...
    def stats(self) -> dict[str, int]: ...


class InMemoryTaskQueue:
    def __init__(self):
        self._q: deque[ScrapeTask] = deque()
        self._tasks: dict[str, ScrapeTask] = {}

    def enqueue(self, task: ScrapeTask) -> None:
        self._tasks[task.task_id] = task
        self._q.append(task)

    def dequeue(self) -> ScrapeTask | None:
        if not self._q:
            return None
        return self._q.popleft()

    def requeue(self, task: ScrapeTask) -> None:
        self._q.append(task)

    def mark_running(self, task: ScrapeTask) -> None:
        task.status = TASK_RUNNING
        task.attempts += 1

    def mark_success(self, task: ScrapeTask) -> None:
        task.status = TASK_SUCCESS

    def mark_skip(self, task: ScrapeTask) -> None:
        task.status = TASK_SKIP

    def mark_retry(self, task: ScrapeTask, error: str | None = None) -> None:
        task.status = TASK_RETRY
        task.last_error = error

    def mark_failed(self, task: ScrapeTask, error: str | None = None) -> None:
        task.status = TASK_FAILED
        task.last_error = error

    def has_items(self) -> bool:
        return len(self._q) > 0

    def stats(self) -> dict[str, int]:
        out = {
            TASK_PENDING: 0,
            TASK_RUNNING: 0,
            TASK_SUCCESS: 0,
            TASK_FAILED: 0,
            TASK_RETRY: 0,
            TASK_SKIP: 0,
        }
        for t in self._tasks.values():
            out[t.status] = out.get(t.status, 0) + 1
        out["TOTAL"] = len(self._tasks)
        return out


# Backward compatibility alias
TaskQueue = InMemoryTaskQueue

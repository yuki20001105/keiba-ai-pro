from __future__ import annotations

import os
from pathlib import Path

from scraping.sqlite_task_queue import SQLiteTaskQueue
from scraping.task_queue import InMemoryTaskQueue, TaskQueueProtocol


class TaskQueueFactory:
    def __init__(self, backend: str | None = None):
        self.backend = (backend or os.environ.get("TASK_QUEUE_BACKEND") or "sqlite").strip().lower()

    def create(self, *, db_path: Path, job_id: str, queue_name: str) -> TaskQueueProtocol:
        if self.backend == "memory":
            return InMemoryTaskQueue()
        if self.backend == "sqlite":
            return SQLiteTaskQueue(db_path=db_path, job_id=job_id, queue_name=queue_name)
        if self.backend == "redis":
            raise NotImplementedError("redis backend is not implemented yet")
        raise ValueError(f"unsupported task queue backend: {self.backend}")

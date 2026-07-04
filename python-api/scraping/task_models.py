from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


TASK_PENDING = "PENDING"
TASK_RUNNING = "RUNNING"
TASK_SUCCESS = "SUCCESS"
TASK_FAILED = "FAILED"
TASK_RETRY = "RETRY"
TASK_SKIP = "SKIP"


@dataclass
class ScrapeTask:
    task_id: str
    task_type: str
    payload: dict[str, Any]
    status: str = TASK_PENDING
    attempts: int = 0
    max_attempts: int = 2
    last_error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

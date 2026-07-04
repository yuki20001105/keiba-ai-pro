from __future__ import annotations

from typing import Any

from scraping.task_executor import TaskExecutor
from scraping.task_executors.race_task_executor import RaceTaskExecutor


class TaskFactory:
    def __init__(self, *, race_pipeline: Any):
        self._executors: dict[str, TaskExecutor] = {
            "race": RaceTaskExecutor(race_pipeline),
        }

    def get_executor(self, task_type: str) -> TaskExecutor:
        key = str(task_type).strip().lower()
        if key not in self._executors:
            raise ValueError(f"unsupported task type: {task_type}")
        return self._executors[key]

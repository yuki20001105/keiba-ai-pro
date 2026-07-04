from __future__ import annotations

from typing import Any, Protocol

from scraping.task_models import ScrapeTask


class TaskExecutor(Protocol):
    async def execute(self, task: ScrapeTask, context: dict[str, Any]) -> dict[str, Any]:
        ...

from __future__ import annotations

from typing import Any

from scraping.task_models import ScrapeTask


class RaceTaskExecutor:
    def __init__(self, race_pipeline: Any):
        self._race_pipeline = race_pipeline

    async def execute(self, task: ScrapeTask, context: dict[str, Any]) -> dict[str, Any]:
        payload = task.payload or {}
        race_id = str(payload.get("race_id", ""))
        date = str(payload.get("date", ""))

        return await self._race_pipeline.process_task(
            race_id=race_id,
            date=date,
            session=context["session"],
            db_path=context["db_path"],
            oikiri_enabled=bool(context["oikiri_enabled"]),
            force_rescrape=bool(context["force_rescrape"]),
            errors=context["errors"],
            scrape_and_save_race=context["scrape_and_save_race"],
            pedigree_cache=context.get("pedigree_cache"),
        )

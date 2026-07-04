from __future__ import annotations

import asyncio
import gc
from typing import Any, Awaitable, Callable

from scraping.quality_codes import E201_TASK_EMPTY_OR_SAVE_FAILED

from .base import BaseDomainPipeline


class RacePipeline(BaseDomainPipeline):
    name = "race"

    async def process_task(
        self,
        *,
        race_id: str,
        date: str,
        session: Any,
        db_path: Any,
        oikiri_enabled: bool,
        force_rescrape: bool,
        errors: list[str],
        scrape_and_save_race: Callable[..., Awaitable[int]],
        pedigree_cache: dict | None = None,
    ) -> dict[str, Any]:
        n = await scrape_and_save_race(
            session,
            race_id,
            date,
            db_path,
            oikiri_enabled,
            errors,
            pedigree_cache=pedigree_cache,
            force_rescrape=force_rescrape,
        )
        if n == -1:
            return {"ok": True, "skip": True, "saved_horses": 0}
        if n <= 0:
            return {
                "ok": False,
                "error": f"save_failed_or_empty:{race_id}",
                "error_code": E201_TASK_EMPTY_OR_SAVE_FAILED,
                "saved_horses": 0,
            }
        return {"ok": True, "saved_horses": int(n)}

    async def process_day(
        self,
        *,
        race_ids: list[str],
        date: str,
        day_index: int,
        total_days: int,
        session: Any,
        db_path: Any,
        oikiri_enabled: bool,
        force_rescrape: bool,
        job: dict,
        job_params: dict,
        counter: dict,
        counter_lock: asyncio.Lock,
        errors: list[str],
        scrape_and_save_race: Callable[..., Awaitable[int]],
        inter_race_sleep: float,
        pedigree_cache: dict | None = None,
    ) -> dict[str, int]:
        async def _fetch_and_save(race_id: str) -> None:
            n = await scrape_and_save_race(
                session,
                race_id,
                date,
                db_path,
                oikiri_enabled,
                errors,
                pedigree_cache=pedigree_cache,
                force_rescrape=force_rescrape,
            )
            if n > 0:
                async with counter_lock:
                    counter["races"] += 1
                    counter["horses"] += n
                    job["progress"] = {
                        "done": day_index,
                        "total": total_days,
                        "message": f"{day_index}/{total_days}日処理中 | {counter['races']}レース・{counter['horses']}頭保存済み",
                        "saved_races": counter["races"],
                        "saved_horses": counter["horses"],
                        "params": job_params,
                    }

        for ci in range(0, len(race_ids), 1):
            chunk = race_ids[ci : ci + 1]
            await asyncio.gather(*[_fetch_and_save(rid) for rid in chunk])
            if ci + 1 < len(race_ids):
                await asyncio.sleep(inter_race_sleep)
            gc.collect()

        return {"processed": len(race_ids)}

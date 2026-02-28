"""
スクレイピングジョブ管理: バックグラウンドジョブの開始・進捗管理。
"""

import asyncio
import gc
import re
from pathlib import Path

import aiohttp

from scraping.constants import SCRAPE_HEADERS
from scraping.race import scrape_race_full
from scraping.storage import _init_sqlite_db, _save_race_sqlite_only

try:
    from app_config import SUPABASE_ENABLED, logger  # type: ignore
except ImportError:
    import logging
    SUPABASE_ENABLED = False
    logger = logging.getLogger(__name__)

try:
    from supabase_client import (  # type: ignore
        get_client as get_supabase_client,
        save_race_to_supabase,
    )
except ImportError:
    def get_supabase_client():  # type: ignore
        return None

    def save_race_to_supabase(data):  # type: ignore
        pass


# ============================================================
# ジョブストア（メモリ上、Render 再起動でリセット）
# ============================================================
_scrape_jobs: dict = {}
_MAX_JOBS = 50


def _purge_old_jobs(store: dict, max_keep: int = _MAX_JOBS) -> None:
    """completed/error ジョブを古い順に削除してメモリリークを防ぐ"""
    if len(store) <= max_keep:
        return
    finished = [k for k, v in store.items() if v.get("status") in ("completed", "error")]
    for key in finished[: len(store) - max_keep]:
        del store[key]


# ============================================================
# バックグラウンドスクレイピングジョブ
# ============================================================

async def _run_scrape_job(
    job_id: str, start_date: str, end_date: str, force_rescrape: bool = False
):
    """バックグラウンドでスクレイピングを実行しジョブストアを更新する"""
    try:
        import time as _time
        from datetime import datetime as _dt, timedelta as _td

        job = _scrape_jobs[job_id]
        job["status"] = "running"

        ULTIMATE_DB = Path(__file__).parent.parent.parent / "keiba" / "data" / "keiba_ultimate.db"
        _init_sqlite_db(ULTIMATE_DB)

        start_time = _time.time()

        def _parse(s):
            for fmt in ("%Y%m%d", "%Y/%m/%d", "%Y-%m-%d"):
                try:
                    return _dt.strptime(s, fmt)
                except ValueError:
                    pass
            raise ValueError(f"日付フォーマット不正: {s}")

        s_dt = _parse(start_date)
        e_dt = _parse(end_date)
        dates = []
        cur = s_dt
        while cur <= e_dt:
            dates.append(cur.strftime("%Y%m%d"))
            cur += _td(days=1)

        total = len(dates)
        job["progress"] = {"done": 0, "total": total, "message": f"0/{total}日処理済み"}

        # --- 取得済み日付を事前チェック（レジューム対応）---
        scraped_dates: set = set()
        _MIN_RACES_PER_DAY = 6
        if SUPABASE_ENABLED and not force_rescrape:
            try:
                def _fetch_scraped_dates():
                    _sb2 = get_supabase_client()
                    if not _sb2:
                        return {}
                    _r = (
                        _sb2.table("races_ultimate")
                        .select("race_id")
                        .gte("race_id", start_date)
                        .lte("race_id", end_date + "99")
                        .execute()
                    )
                    _dc: dict = {}
                    for _row in _r.data or []:
                        _d = str(_row["race_id"])[:8]
                        _dc[_d] = _dc.get(_d, 0) + 1
                    return _dc

                _date_count = await asyncio.to_thread(_fetch_scraped_dates)
                for _d, _cnt in _date_count.items():
                    if _cnt >= _MIN_RACES_PER_DAY:
                        scraped_dates.add(_d)
                if scraped_dates:
                    logger.info(f"取得済み日付: {len(scraped_dates)}日分をスキップ（各{_MIN_RACES_PER_DAY}件以上確認）")
                    job["progress"]["message"] = f"{len(scraped_dates)}日分は取得済み、スキップします"
                _partial = {d: c for d, c in _date_count.items() if c < _MIN_RACES_PER_DAY}
                if _partial:
                    logger.info(f"部分取得日（再スクレイピング対象）: {list(_partial.items())[:5]}")
            except Exception as _e:
                logger.warning(f"取得済み日付確認失敗（全日付を処理）: {_e}")

        timeout = aiohttp.ClientTimeout(total=25, connect=8)
        connector = aiohttp.TCPConnector(limit=20, limit_per_host=10)
        counter = {"races": 0, "horses": 0}
        counter_lock = asyncio.Lock()

        async with aiohttp.ClientSession(
            headers=SCRAPE_HEADERS, timeout=timeout, connector=connector
        ) as session:
            for i, date in enumerate(dates):
                list_url = f"https://db.netkeiba.com/race/list/{date}/"
                errors: list = []

                if date in scraped_dates:
                    logger.info(f"{date}: Supabase取得済み → スキップ")
                    job["progress"] = {
                        "done": i + 1,
                        "total": total,
                        "message": f"{i+1}/{total}日処理済み / {counter['races']}レース保存 (スキップ含む)",
                        "saved_races": counter["races"],
                        "saved_horses": counter["horses"],
                    }
                    continue

                try:
                    async with session.get(list_url) as resp:
                        if resp.status != 200:
                            logger.warning(f"{date}: レース一覧 HTTP {resp.status} → スキップ")
                            job["progress"] = {
                                "done": i + 1,
                                "total": total,
                                "message": f"{i+1}/{total}日処理済み / {counter['races']}レース保存 (HTTP {resp.status}スキップ)",
                                "saved_races": counter["races"],
                                "saved_horses": counter["horses"],
                            }
                            continue
                        content = await resp.read()
                        html = content.decode("euc-jp", errors="ignore")
                        del content

                    race_ids = list(dict.fromkeys(re.findall(r"/race/(\d{12})/", html)))
                    del html
                    logger.info(f"{date}: {len(race_ids)}レースID検出")

                    async def _fetch_and_save(race_id, _date=date, _day_idx=i):
                        try:
                            race_data = await scrape_race_full(
                                session, race_id, date_hint=_date, quick_mode=True
                            )
                            if race_data and race_data.get("horses"):
                                n_horses = len(race_data["horses"])
                                _save_tasks = [
                                    asyncio.to_thread(_save_race_sqlite_only, race_data, ULTIMATE_DB)
                                ]
                                if SUPABASE_ENABLED:
                                    _save_tasks.append(
                                        asyncio.to_thread(save_race_to_supabase, race_data)
                                    )
                                _save_results = await asyncio.gather(*_save_tasks, return_exceptions=True)
                                saved = (
                                    _save_results[0]
                                    if not isinstance(_save_results[0], Exception)
                                    else False
                                )
                                del race_data
                                if saved:
                                    async with counter_lock:
                                        counter["races"] += 1
                                        counter["horses"] += n_horses
                                        job["progress"] = {
                                            "done": _day_idx,
                                            "total": total,
                                            "message": (
                                                f"{_day_idx}/{total}日処理中 | "
                                                f"{counter['races']}レース・{counter['horses']}頭保存済み"
                                            ),
                                            "saved_races": counter["races"],
                                            "saved_horses": counter["horses"],
                                        }
                                    logger.info(f"保存完了: {race_id} ({n_horses}頭)")
                                else:
                                    logger.warning(f"SQLite保存失敗: {race_id}")
                            else:
                                logger.warning(f"レースデータなし/出走馬なし: {race_id}")
                        except Exception as exc:
                            err_msg = f"{race_id}: {exc}"
                            errors.append(err_msg)
                            logger.error(f"_fetch_and_save 失敗 {err_msg}")

                    for ci in range(0, len(race_ids), 2):
                        chunk = race_ids[ci : ci + 2]
                        await asyncio.gather(*[_fetch_and_save(r) for r in chunk])
                        gc.collect()
                    if errors:
                        logger.warning(f"エラー一覧: {errors[:5]}")

                except Exception as e:
                    logger.error(f"ジョブ {job_id} {date} エラー: {e}")

                job["progress"] = {
                    "done": i + 1,
                    "total": total,
                    "message": f"{i+1}/{total}日処理済み / {counter['races']}レース保存 (errors:{len(errors)})",
                    "saved_races": counter["races"],
                    "saved_horses": counter["horses"],
                    "last_errors": errors[-3:] if errors else [],
                }

        saved_races = counter["races"]
        saved_horses = counter["horses"]
        elapsed = _time.time() - start_time
        job["status"] = "completed"
        job["result"] = {
            "success": True,
            "races_collected": saved_races,
            "saved_horses": saved_horses,
            "elapsed_time": elapsed,
            "message": f"{saved_races}レース・{saved_horses}頭のデータを収集しました",
        }
    except Exception as e:
        logger.error(f"スクレイピングジョブ失敗 {job_id}: {e}")
        if job_id in _scrape_jobs:
            _scrape_jobs[job_id]["status"] = "error"
            _scrape_jobs[job_id]["error"] = str(e)

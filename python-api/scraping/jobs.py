"""
スクレイピングジョブ管理: バックグラウンドジョブの開始・進捗管理。
"""

import asyncio
import gc
import json
import re
import sqlite3
from pathlib import Path

import aiohttp

from scraping.constants import SCRAPE_HEADERS, SCRAPE_PROXY_URL
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
# ジョブ永続化（SQLite）
# ============================================================
_JOBS_DB_PATH: Path = Path(__file__).parent.parent.parent / "keiba" / "data" / "scrape_jobs.db"


def _init_jobs_db() -> None:
    try:
        _JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_JOBS_DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scrape_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'queued',
                progress TEXT DEFAULT '{}',
                result TEXT DEFAULT 'null',
                error TEXT DEFAULT 'null',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"ジョブDB初期化失敗: {e}")


def _persist_job(job_id: str, job: dict) -> None:
    """ジョブ状態を SQLite に永続化する（失敗は握り潰す）"""
    try:
        conn = sqlite3.connect(str(_JOBS_DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            INSERT OR REPLACE INTO scrape_jobs (job_id, status, progress, result, error, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            job_id,
            job.get("status", "unknown"),
            json.dumps(job.get("progress", {}), ensure_ascii=False),
            json.dumps(job.get("result"), ensure_ascii=False),
            json.dumps(job.get("error"), ensure_ascii=False),
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass  # ログスパム防止


def _load_job_from_db(job_id: str) -> dict | None:
    """SQLite からジョブ状態を復元する"""
    try:
        conn = sqlite3.connect(str(_JOBS_DB_PATH))
        row = conn.execute(
            "SELECT status, progress, result, error FROM scrape_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        conn.close()
        if row:
            return {
                "status": row[0],
                "progress": json.loads(row[1] or "{}"),
                "result": json.loads(row[2] or "null"),
                "error": json.loads(row[3] or "null"),
            }
    except Exception:
        pass
    return None


_init_jobs_db()


# ============================================================
# ジョブストア（メモリ上 + SQLite二重管理）
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


def get_job(job_id: str) -> dict | None:
    """メモリ → SQLite の順でジョブを取得する"""
    if job_id in _scrape_jobs:
        return _scrape_jobs[job_id]
    return _load_job_from_db(job_id)


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
                    # data JSON の date フィールドから正確な日付を取得する
                    # race_id[:8] = YYYY+venue+kai であり日付ではないため使用禁止
                    _r = (
                        _sb2.table("races_ultimate")
                        .select("race_id,data")
                        .execute()
                    )
                    _dc: dict = {}
                    for _row in _r.data or []:
                        try:
                            _data = json.loads(_row.get("data") or "{}")
                            _d = str(_data.get("date") or "")
                            if len(_d) == 8 and _d.isdigit():
                                _dc[_d] = _dc.get(_d, 0) + 1
                        except Exception:
                            pass
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

        # プロキシ設定（環境変数 SCRAPE_PROXY_URL で指定）
        _session_kwargs: dict = {}
        if SCRAPE_PROXY_URL:
            _session_kwargs["trust_env"] = False
            logger.info(f"プロキシ使用: {SCRAPE_PROXY_URL}")

        async with aiohttp.ClientSession(
            headers=SCRAPE_HEADERS, timeout=timeout, connector=connector, **_session_kwargs
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
                        if ci + 2 < len(race_ids):
                            await asyncio.sleep(1.0)  # レース間インターバル（IP ブロック抑制）
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
                # 進捗を SQLite に永続化（Render スピンダウン対策）
                _persist_job(job_id, job)
                # 日付間インターバル（最終日以外）
                if i < total - 1:
                    await asyncio.sleep(2.0)

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
        _persist_job(job_id, job)
    except Exception as e:
        logger.error(f"スクレイピングジョブ失敗 {job_id}: {e}")
        if job_id in _scrape_jobs:
            _scrape_jobs[job_id]["status"] = "error"
            _scrape_jobs[job_id]["error"] = str(e)
            _persist_job(job_id, _scrape_jobs[job_id])

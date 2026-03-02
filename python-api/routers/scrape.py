"""
スクレイピングエンドポイント
POST /api/scrape/start  → 非同期ジョブ開始
GET  /api/scrape/status/{job_id}
POST /api/scrape         → レガシー同期スクレイプ
POST /api/rescrape_incomplete
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
from fastapi import APIRouter, Depends, HTTPException

from app_config import ULTIMATE_DB, logger  # type: ignore
from deps.auth import require_admin  # type: ignore
from models import ScrapeRequest, ScrapeResponse, RescrapeResponse  # type: ignore
from scraping.constants import SCRAPE_HEADERS  # type: ignore
from scraping.jobs import _scrape_jobs, _purge_old_jobs, _run_scrape_job, get_job  # type: ignore
from scraping.race import scrape_race_full  # type: ignore
from scraping.storage import _save_race_to_ultimate_db  # type: ignore

router = APIRouter()


@router.post("/api/scrape/start")
async def scrape_start(request: ScrapeRequest, _: dict = Depends(require_admin)):
    """スクレイピングをバックグラウンドで開始し、即座に job_id を返す（Admin専用）"""
    _purge_old_jobs(_scrape_jobs)
    job_id = str(uuid.uuid4())[:8]
    _scrape_jobs[job_id] = {
        "status": "queued",
        "progress": {"done": 0, "total": 0, "message": "開始待ち"},
        "result": None,
        "error": None,
    }
    try:
        import threading
        def _bg() -> None:
            # Windows の ProactorEventLoop(IOCP) が main loop と干渉しないよう
            # スレッド内では SelectorEventLoop を明示的に使用する
            import asyncio
            import sys
            if sys.platform == "win32":
                loop = asyncio.SelectorEventLoop()
            else:
                loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    _run_scrape_job(job_id, request.start_date, request.end_date, request.force_rescrape)
                )
            finally:
                loop.close()
        threading.Thread(target=_bg, daemon=True, name=f"scrape-{job_id}").start()
        logger.info(f"ジョブ {job_id} をスレッドでスケジュール済み")
    except Exception as e:
        logger.error(f"スレッド起動失敗: {e}")
        _scrape_jobs[job_id]["status"] = "error"
        _scrape_jobs[job_id]["error"] = f"タスク起動失敗: {e}"
    return {"job_id": job_id, "status": _scrape_jobs[job_id]["status"]}


@router.get("/api/scrape/status/{job_id}")
async def scrape_status(job_id: str):
    """スクレイピングジョブの進捗・結果を返す（メモリ → SQLite の順で検索）"""
    job = get_job(job_id)
    if not job:
        return {
            "job_id": job_id,
            "status": "not_found",
            "progress": {},
            "result": None,
            "error": f"ジョブ {job_id} が見つかりません（サーバー再起動の可能性）",
        }
    return {
        "job_id": job_id,
        "status": job["status"],
        "progress": job["progress"],
        "result": job.get("result"),
        "error": job.get("error"),
    }


@router.post("/api/scrape", response_model=ScrapeResponse)
async def scrape_data(request: ScrapeRequest):
    """
    期間指定でnetkeiba.comから完全データを自動収集しkeiba_ultimate.dbに保存。
    NOTE: 土日のみ対象。全日程は /api/scrape/start を使用すること。
    """
    logger.info(f"完全スクレイピング開始: {request.start_date} ～ {request.end_date}")
    start_time = time.time()

    def _parse_date(s: str) -> datetime:
        for fmt in ("%Y%m%d", "%Y/%m/%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise ValueError(f"日付フォーマット不正: {s}")

    start = _parse_date(request.start_date)
    end = _parse_date(request.end_date)
    dates = []
    cur = start
    while cur <= end:
        # 土日 + 月曜祝日（成人の日等）を対象とする
        # NOTE: /api/scrape/start を使えば全日処理可能（こちらは短期間向け）
        if cur.weekday() in [5, 6]:
            dates.append(cur.strftime("%Y%m%d"))
        elif cur.weekday() == 0:  # 月曜日 — 祝日の可能性があるので含める
            dates.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)

    logger.info(f"対象日数: {len(dates)}日")

    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(limit=5, limit_per_host=3)

    saved_races = 0
    saved_horses = 0

    async with aiohttp.ClientSession(headers=SCRAPE_HEADERS, timeout=timeout, connector=connector) as session:
        for date in dates:
            list_url = f"https://db.netkeiba.com/race/list/{date}/"
            logger.info(f"レース一覧取得: {date}")
            try:
                await asyncio.sleep(0.5)
                async with session.get(list_url) as resp:
                    if resp.status != 200:
                        continue
                    content = await resp.read()
                    html = content.decode("euc-jp", errors="ignore")

                race_ids = list(dict.fromkeys(re.findall(r"/race/(\d{12})/", html)))
                logger.info(f"  {len(race_ids)}レース発見")

                for race_id in race_ids:
                    race_data = await scrape_race_full(session, race_id, date_hint=date)
                    if race_data and race_data["horses"]:
                        _save_race_to_ultimate_db(race_data, ULTIMATE_DB, overwrite=True)
                        saved_races += 1
                        saved_horses += len(race_data["horses"])
                        logger.info(f"  保存: {race_id} {race_data['race_info']['race_name']} ({len(race_data['horses'])}頭)")
            except Exception as e:
                logger.error(f"{date} エラー: {e}")

    elapsed = time.time() - start_time
    logger.info(f"完全スクレイピング完了: {saved_races}レース/{saved_horses}頭, {elapsed:.1f}秒")

    return ScrapeResponse(
        success=True,
        message=f"{saved_races}レース・{saved_horses}頭のデータを収集しました（完全版）",
        races_collected=saved_races,
        db_path=str(ULTIMATE_DB),
        elapsed_time=elapsed,
    )


@router.post("/api/rescrape_incomplete")
async def rescrape_incomplete(limit: int = 50) -> RescrapeResponse:
    """
    keiba_ultimate.db 内の不完全レコード（trainer_name=NULL 等）を再スクレイプして上書き保存する。
    """
    import sqlite3 as _sq3

    start_time = time.time()

    conn = _sq3.connect(str(ULTIMATE_DB))
    rows = conn.execute("SELECT DISTINCT race_id FROM race_results_ultimate").fetchall()
    all_race_ids = [r[0] for r in rows]

    # races_ultimate から既存の date を取得（date_hint として再利用）
    date_map: dict[str, str] = {}
    for row in conn.execute("SELECT race_id, data FROM races_ultimate").fetchall():
        try:
            import json as _json
            _d = _json.loads(row[1] or "{}").get("date", "")
            if _d:
                date_map[row[0]] = _d
        except Exception:
            pass

    incomplete_ids = []
    for rid in all_race_ids:
        sample = conn.execute(
            "SELECT data FROM race_results_ultimate WHERE race_id = ? LIMIT 1", (rid,)
        ).fetchone()
        if sample:
            d = json.loads(sample[0])
            if d.get("trainer_name") is None and d.get("horse_weight") is None:
                incomplete_ids.append(rid)
    conn.close()

    to_process = incomplete_ids[:limit]
    logger.info(f"不完全レース: {len(incomplete_ids)}件中 {len(to_process)}件を再取得")

    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(limit=5, limit_per_host=3)

    updated_races = 0
    updated_horses = 0

    async with aiohttp.ClientSession(headers=SCRAPE_HEADERS, timeout=timeout, connector=connector) as session:
        for race_id in to_process:
            _date_hint = date_map.get(race_id, "")
            race_data = await scrape_race_full(session, race_id, date_hint=_date_hint)
            if race_data and race_data["horses"]:
                _save_race_to_ultimate_db(race_data, ULTIMATE_DB, overwrite=True)
                updated_races += 1
                updated_horses += len(race_data["horses"])
                logger.info(f"  更新: {race_id} ({len(race_data['horses'])}頭)")
            else:
                logger.warning(f"  スキップ: {race_id} (取得失敗)")

    elapsed = time.time() - start_time
    remaining = len(incomplete_ids) - updated_races

    return RescrapeResponse(
        success=True,
        message=f"{updated_races}レース/{updated_horses}頭を更新。残り不完全: {remaining}件",
        updated_races=updated_races,
        updated_horses=updated_horses,
        elapsed_time=elapsed,
    )

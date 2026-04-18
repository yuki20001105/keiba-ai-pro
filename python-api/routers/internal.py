"""
内部エンドポイント（緊急手動スクレイプ用・レガシー）

⚠️  通常運用では **使用しない**。
    GitHub Actions が python-api/run_scrape.py を直接実行する方式に移行済み。
    （INTERNAL_SECRET / 公開エンドポイント 不要の設計）

残している理由:
  - 手動で即座にスクレイプしたい緊急時のバックドア
  - INTERNAL_SECRET 環境変数を設定しない限り、403 を返すだけで無害

POST /api/internal/enqueue_scrape
  Header: X-Internal-Secret: <INTERNAL_SECRET 環境変数>
  → BackgroundTasks でスクレイプを非同期実行してすぐ 202 を返す
"""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from app_config import logger  # type: ignore
router = APIRouter()

_INTERNAL_SECRET = os.environ.get("INTERNAL_SECRET", "")

# ジョブ記録（インメモリ）
_enqueue_jobs: dict = {}


def _verify_secret(x_internal_secret: Optional[str]) -> None:
    if not _INTERNAL_SECRET:
        logger.warning("INTERNAL_SECRET 未設定。本番環境では必ず設定してください。")
        return
    if x_internal_secret != _INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Invalid internal secret")


async def _scrape_date(date_str: str) -> int:
    """1 日分のレースを取得して SQLite + Supabase に保存。保存件数を返す。"""
    from scraping.race import scrape_race_full  # type: ignore
    from scraping.storage import _save_race_to_ultimate_db  # type: ignore
    from app_config import ULTIMATE_DB, SUPABASE_ENABLED, get_supabase_client  # type: ignore

    import httpx
    from bs4 import BeautifulSoup

    url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date_str}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        race_links = list({
            "https://race.netkeiba.com" + a["href"]
            for a in soup.select("a[href*='/race/result.html']")
        })
    except Exception as e:
        logger.warning(f"レース一覧取得失敗 {date_str}: {e}")
        return 0

    count = 0
    for race_url in race_links:
        try:
            race_data = await scrape_race_full(race_url)
            if race_data:
                _save_race_to_ultimate_db(race_data, ULTIMATE_DB, overwrite=True)
                if SUPABASE_ENABLED and get_supabase_client():
                    from app_config import save_race_to_supabase  # type: ignore
                    save_race_to_supabase(race_data)
                count += 1
        except Exception as e:
            logger.warning(f"cron scrape {race_url}: {e}")
    return count


def _run_scrape_job(job_id: str, days_back: int) -> None:
    """スレッド内で非同期スクレイプを実行"""
    import asyncio

    async def _main() -> None:
        total = 0
        for d in range(days_back):
            date_str = (datetime.now() - timedelta(days=d)).strftime("%Y%m%d")
            _enqueue_jobs[job_id]["current_date"] = date_str
            cnt = await _scrape_date(date_str)
            total += cnt
            logger.info(f"[cron job {job_id}] {date_str}: {cnt} races")
        _enqueue_jobs[job_id]["status"] = "completed"
        _enqueue_jobs[job_id]["total"] = total
        logger.info(f"[cron job {job_id}] 完了 total={total}")

    try:
        asyncio.run(_main())
    except Exception as e:
        _enqueue_jobs[job_id]["status"] = "error"
        _enqueue_jobs[job_id]["error"] = str(e)
        logger.error(f"[cron job {job_id}] エラー: {e}")


@router.post("/api/internal/enqueue_scrape", status_code=202)
async def enqueue_scrape(
    background_tasks: BackgroundTasks,
    x_internal_secret: Optional[str] = Header(None, alias="X-Internal-Secret"),
    days_back: int = 3,
):
    """
    GitHub Actions から呼ばれる。直近 days_back 日分をバックグラウンドでスクレイプ。
    すぐに 202 Accepted を返す。
    """
    _verify_secret(x_internal_secret)

    job_id = str(uuid.uuid4())[:8]
    _enqueue_jobs[job_id] = {
        "status": "queued",
        "started_at": datetime.now().isoformat(),
        "days_back": days_back,
    }

    # BackgroundTasks は同プロセス内スレッドで実行（Redis 不要）
    background_tasks.add_task(
        lambda: threading.Thread(
            target=_run_scrape_job, args=(job_id, days_back), daemon=True
        ).start()
    )

    logger.info(f"Cron scrape enqueued: job_id={job_id} days_back={days_back}")
    return {
        "status": "accepted",
        "job_id": job_id,
        "days_back": days_back,
        "message": f"直近 {days_back} 日分のスクレイプをバックグラウンドで開始しました",
    }


@router.get("/api/internal/scrape_status/{job_id}")
async def scrape_status(
    job_id: str,
    x_internal_secret: Optional[str] = Header(None, alias="X-Internal-Secret"),
):
    """ジョブ進捗確認"""
    _verify_secret(x_internal_secret)
    job = _enqueue_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return {"job_id": job_id, **job}

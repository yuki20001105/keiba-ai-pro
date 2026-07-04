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
    """1 日分のレースを取得して SQLite に保存。保存件数を返す。"""
    import asyncio
    import re as _re
    from scraping.race import scrape_race_full  # type: ignore
    from scraping.storage import _save_race_to_ultimate_db  # type: ignore
    from scraping.constants import get_random_headers  # type: ignore
    from app_config import ULTIMATE_DB, SUPABASE_DATA_ENABLED, get_supabase_client  # type: ignore

    import aiohttp
    import httpx
    from bs4 import BeautifulSoup

    race_ids: list[str] = []

    # ① db.netkeiba.com/race/list/{date}/ → 完了済みレース一覧（結果ページ）
    db_url = f"https://db.netkeiba.com/race/list/{date_str}/"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}) as client:
            resp = await client.get(db_url)
        if resp.status_code == 200:
            html = resp.content.decode("euc-jp", errors="replace")
            # race_id は YYYY[場コード][回][日][レース] 形式で日付は含まれない
            race_ids = list(dict.fromkeys(_re.findall(r"/race/(\d{12})/", html)))
            if race_ids:
                logger.info(f"[scheduler] {date_str}: db.netkeiba.com から {len(race_ids)} レースID検出")
    except Exception as e:
        logger.warning(f"[scheduler] db.netkeiba.com 取得失敗 {date_str}: {e}")

    # ② フォールバック: race.netkeiba.com/top/race_list_sub.html（直近30日）
    if not race_ids:
        sub_url = f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={date_str}"
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True,
                                         headers={"User-Agent": "Mozilla/5.0"}) as client:
                resp2 = await client.get(sub_url)
            if resp2.status_code == 200:
                html2 = resp2.content.decode("euc-jp", errors="replace")
                soup2 = BeautifulSoup(html2, "lxml")
                seen: set[str] = set()
                for a in soup2.find_all("a", href=True):
                    m = _re.search(r"race_id=(\d{12})", a["href"])
                    if m and m.group(1) not in seen:
                        seen.add(m.group(1))
                        race_ids.append(m.group(1))
                if race_ids:
                    logger.info(f"[scheduler] {date_str}: race_list_sub から {len(race_ids)} レースID検出")
                else:
                    logger.info(f"[scheduler] {date_str}: race_list_sub 0件")
        except Exception as e:
            logger.warning(f"[scheduler] race_list_sub 取得失敗 {date_str}: {e}")

    if not race_ids:
        logger.info(f"[scheduler] {date_str}: レースIDなし → スキップ（結果未掲載またはIPブロック）")
        return 0

    count = 0
    _timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(headers=get_random_headers(), timeout=_timeout) as session:
        for race_id in race_ids:
            try:
                await asyncio.sleep(1.0)  # INV-07: 1秒以上のインターバル
                race_data = await scrape_race_full(session, race_id, date_hint=date_str)
                if race_data and race_data.get("horses"):
                    _save_race_to_ultimate_db(race_data, ULTIMATE_DB)
                    if SUPABASE_DATA_ENABLED and get_supabase_client():
                        from app_config import save_race_to_supabase  # type: ignore
                        save_race_to_supabase(race_data)
                    count += 1
            except Exception as e:
                logger.warning(f"cron scrape {race_id}: {e}")
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

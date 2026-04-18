"""
定時スクレイピングスケジューラー（APScheduler）

レース開催日（主に土日）に当日分のレースデータを自動取得する。

スケジュール:
  - 毎朝 6:00 JST   : 前日分（昨日）の結果を取り込む
  - 9:00 〜 22:00 JST, 2時間おき: 当日分のレースをスクレイプ
    （発走前は shutuba フォールバックで出走表、レース後は結果を上書き）

環境変数:
  SCHEDULER_ENABLED  : "false" を設定するとスケジューラを無効化（デフォルト: 有効）
  SCHEDULER_TZ       : タイムゾーン（デフォルト: "Asia/Tokyo"）
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").lower() not in ("false", "0", "no")
_TZ = os.environ.get("SCHEDULER_TZ", "Asia/Tokyo")

# APScheduler はオプション依存
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
    from apscheduler.triggers.cron import CronTrigger  # type: ignore
    _APSCHEDULER_AVAILABLE = True
except ImportError:
    _APSCHEDULER_AVAILABLE = False

_scheduler: "AsyncIOScheduler | None" = None


# ---------------------------------------------------------------------------
# スクレイプ実行ヘルパー
# ---------------------------------------------------------------------------

async def _run_scrape_for_date(date_str: str) -> None:
    """1日分のレースを取得して SQLite (+ Supabase) に保存する。"""
    try:
        from routers.internal import _scrape_date  # type: ignore
        count = await _scrape_date(date_str)
        logger.info(f"[scheduler] {date_str}: {count} races scraped")
    except Exception as e:
        logger.error(f"[scheduler] scrape_date {date_str} failed: {e}")


async def _job_scrape_yesterday() -> None:
    """毎朝6時ジョブ: 前日分の結果を確定取得する。"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    logger.info(f"[scheduler] 前日結果取り込み: {yesterday}")
    await _run_scrape_for_date(yesterday)


async def _job_scrape_today() -> None:
    """日中ジョブ: 当日分を取得（出走表→結果で上書き）。"""
    today = datetime.now().strftime("%Y%m%d")
    logger.info(f"[scheduler] 当日スクレイプ: {today}")
    await _run_scrape_for_date(today)


# ---------------------------------------------------------------------------
# スケジューラ起動 / 停止
# ---------------------------------------------------------------------------

def start_scheduler() -> None:
    """FastAPI startup フックから呼び出す。"""
    global _scheduler

    if not _ENABLED:
        logger.info("[scheduler] SCHEDULER_ENABLED=false のためスキップ")
        return

    if not _APSCHEDULER_AVAILABLE:
        logger.warning(
            "[scheduler] APScheduler が未インストールです。"
            " pip install 'APScheduler>=3.10.0' を実行してください。"
        )
        return

    _scheduler = AsyncIOScheduler(timezone=_TZ)

    # 毎朝 6:00 — 前日結果を確定取得
    _scheduler.add_job(
        _job_scrape_yesterday,
        CronTrigger(hour=6, minute=0, timezone=_TZ),
        id="scrape_yesterday",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # 9:00 〜 21:00 の偶数時 (2時間おき) — 当日をリアルタイム更新
    _scheduler.add_job(
        _job_scrape_today,
        CronTrigger(hour="9,11,13,15,17,19,21", minute=0, timezone=_TZ),
        id="scrape_today",
        replace_existing=True,
        misfire_grace_time=600,
    )

    _scheduler.start()
    logger.info(f"[scheduler] 起動完了 (tz={_TZ})")


def stop_scheduler() -> None:
    """FastAPI shutdown フックから呼び出す。"""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[scheduler] 停止")
    _scheduler = None

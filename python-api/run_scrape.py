"""
GitHub Actions から直接実行されるスクレイプスクリプト。

FastAPI サーバーを経由しない設計:
  - 内部エンドポイント（/api/internal/enqueue_scrape）不要
  - INTERNAL_SECRET 不要
  - 漏洩リスクのある秘密情報が減る

必要な環境変数:
  SUPABASE_URL          例: https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  service_role キー（anon は不可）

GitHub Actions から呼ばれる: .github/workflows/daily-scrape.yml

使い方:
  cd python-api
  python run_scrape.py --days-back 3
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# ── ログ設定 ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("run_scrape")

# ── Supabase 接続 ─────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _get_supabase():
    """service_role キーで Supabase クライアントを生成（RLS バイパス）"""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.warning("SUPABASE_URL / SUPABASE_SERVICE_KEY 未設定。Supabase への保存をスキップ。")
        return None
    try:
        from supabase import create_client  # type: ignore
        return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception as e:
        logger.error(f"Supabase クライアント生成失敗: {e}")
        return None


# ── Supabase へレースデータを保存 ────────────────────────────────

def _save_to_supabase(client, race_data: dict) -> bool:
    """
    races_ultimate / race_results_ultimate テーブルへ upsert。
    service_role キーなので RLS をバイパス。
    """
    try:
        race_id = race_data.get("race_id")
        if not race_id:
            return False

        # races_ultimate upsert
        race_row = {
            "race_id": race_id,
            "data": race_data,
        }
        client.table("races_ultimate").upsert(race_row, on_conflict="race_id").execute()

        # race_results_ultimate upsert（results リストがあれば）
        results = race_data.get("results") or race_data.get("horses") or []
        for r in results:
            horse_num = r.get("horse_number") or r.get("horse_num") or r.get("馬番")
            if horse_num is None:
                continue
            result_row = {
                "race_id": race_id,
                "horse_number": str(horse_num),
                "data": r,
            }
            client.table("race_results_ultimate").upsert(
                result_row,
                on_conflict="race_id,horse_number",
            ).execute()

        logger.info(f"Supabase 保存完了: race_id={race_id}")
        return True
    except Exception as e:
        logger.warning(f"Supabase 保存エラー: {e}")
        return False


# ── netkeiba からレース一覧を取得 ────────────────────────────────

async def _get_race_urls(date_str: str) -> list[str]:
    """netkeiba レース一覧ページからレース URL を取得"""
    try:
        import httpx
        from bs4 import BeautifulSoup

        url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date_str}"
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        urls = list({
            "https://race.netkeiba.com" + a["href"]
            for a in soup.select("a[href*='/race/result.html']")
        })
        logger.info(f"{date_str}: {len(urls)} レース発見")
        return urls
    except Exception as e:
        logger.warning(f"レース一覧取得失敗 {date_str}: {e}")
        return []


# ── 1 日分のスクレイプ ───────────────────────────────────────────

async def scrape_date(date_str: str, supabase_client) -> int:
    """1 日分のレースをスクレイプして Supabase に保存。保存件数を返す。"""
    # python-api/ を sys.path に追加（scraping モジュールの import 用）
    here = Path(__file__).parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))

    try:
        from scraping.race import scrape_race_full  # type: ignore
    except ImportError as e:
        logger.error(f"scraping.race import 失敗: {e}")
        return 0

    race_urls = await _get_race_urls(date_str)
    if not race_urls:
        return 0

    count = 0
    for race_url in race_urls:
        try:
            race_data = await scrape_race_full(race_url)
            if not race_data:
                continue
            if supabase_client:
                ok = _save_to_supabase(supabase_client, race_data)
                if ok:
                    count += 1
        except Exception as e:
            logger.warning(f"スクレイプ失敗 {race_url}: {e}")

    logger.info(f"{date_str}: {count}/{len(race_urls)} 件保存完了")
    return count


# ── メイン ──────────────────────────────────────────────────────

async def main(days_back: int) -> None:
    supabase = _get_supabase()

    today = date.today()
    total = 0
    for i in range(days_back):
        target = today - timedelta(days=i)
        date_str = target.strftime("%Y%m%d")
        logger.info(f"=== スクレイプ開始: {date_str} ===")
        n = await scrape_date(date_str, supabase)
        total += n

    logger.info(f"=== 完了: 合計 {total} 件保存 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="競馬レース自動スクレイプ")
    parser.add_argument(
        "--days-back",
        type=int,
        default=3,
        help="今日を含む何日前まで取得するか（デフォルト: 3）",
    )
    args = parser.parse_args()

    logger.info(f"run_scrape 開始 days_back={args.days_back}")
    asyncio.run(main(args.days_back))

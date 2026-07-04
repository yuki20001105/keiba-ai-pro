#!/usr/bin/env python3
"""
scrape_worker.py — 並列データ取得 Worker スクリプト

FastAPI 不要で keiba_ultimate.db に直接書き込む。
複数プロセスを異なる IP で起動することで 10 年分のデータを 3 日以内に取得できる。

━━━ 使い方 ━━━

  # Worker 1 (2016-2018年 担当) — ターミナル1
  python-api\\.venv\\Scripts\\python.exe python-api\\scripts\\scrape_worker.py ^
      --start 20160101 --end 20181231 --proxy http://vpn1:8080 --worker-id 1

  # Worker 2 (2019-2021年 担当) — ターミナル2
  python-api\\.venv\\Scripts\\python.exe python-api\\scripts\\scrape_worker.py ^
      --start 20190101 --end 20211231 --proxy http://vpn2:8080 --worker-id 2

  # Worker 3 (2022-2025年 担当) — ターミナル3
  python-api\\.venv\\Scripts\\python.exe python-api\\scripts\\scrape_worker.py ^
      --start 20220101 --end 20251231 --proxy http://vpn3:8080 --worker-id 3

  # プロキシなし (シングル IP、通常速度)
  python-api\\.venv\\Scripts\\python.exe python-api\\scripts\\scrape_worker.py ^
      --start 20250101 --end 20251231

  # dry-run: 処理日数のみ表示して終了
  python-api\\.venv\\Scripts\\python.exe python-api\\scripts\\scrape_worker.py ^
      --start 20160101 --end 20181231 --dry-run

  # 強制再取得 (取得済み日付も再スクレイプ)
  python-api\\.venv\\Scripts\\python.exe python-api\\scripts\\scrape_worker.py ^
      --start 20241201 --end 20241231 --force

━━━ 年代範囲の目安 (1年あたり約 2,700 レース) ━━━

  2016-2018: ~8,100 レース
  2019-2021: ~8,100 レース
  2022-2025: ~10,800 レース

━━━ 注意 ━━━

  - INV-07: 各リクエスト間 1.0 秒以上必須
  - 各 Worker は独立したネットワーク接続 (異なる IP) が必要
  - 同一 DB への並列書き込みは WAL mode で対応済み
  - Ctrl+C で安全に中断 (再開可能)
  - ログ: python-api/logs/worker_<id>.log に出力
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import logging
import os
import random
import re
import signal
import sys
import time
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path

# ── sys.path を python-api/ に設定（app_config / scraping モジュール解決） ──
_SCRIPT_DIR = Path(__file__).resolve().parent
_API_DIR = _SCRIPT_DIR.parent  # python-api/
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

# ── --proxy を os.environ に早期設定（constants.py が import 前に読む） ──
_early_proxy = None
for _i, _a in enumerate(sys.argv):
    if _a == "--proxy" and _i + 1 < len(sys.argv):
        _early_proxy = sys.argv[_i + 1]
        break
if _early_proxy:
    os.environ["SCRAPE_PROXY_URL"] = _early_proxy

# ── ここから scraping モジュールを import ──
import aiohttp
import httpx
from bs4 import BeautifulSoup

from scraping.constants import (
    NETKEIBA_EMAIL,
    NETKEIBA_PASSWORD,
    SCRAPE_PROXY_URL,
    get_random_headers,
    login_netkeiba,
)
from scraping.jobs import (
    _build_race_dates_from_calendar,
    _jitter,
    _new_session,
    _parse_date,
    _PRE_SLEEP_OLD,
    _PRE_SLEEP_RECENT,
    _INTER_RACE_SLEEP,
    _POST_SLEEP_OLD,
    _POST_SLEEP_RECENT,
    _scrape_and_save_race,
    _SESSION_ROTATE_DAYS,
    _COOLDOWN_EVERY_DAYS,
)
from scraping.oikiri import scrape_oikiri
from scraping.race import scrape_race_full
from scraping.speed_figure import scrape_speed_figure
from scraping.storage import (
    _get_scraped_dates_sqlite,
    _init_sqlite_db,
    _save_race_sqlite_only,
    _save_scraped_date_sqlite,
    _save_training_data,
    _save_speed_figures,
)

# ────────────────────────────────────────────────────────────────
# ロガー設定
# ────────────────────────────────────────────────────────────────

def _setup_logger(worker_id: int) -> logging.Logger:
    log_dir = _API_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"worker_{worker_id}.log"

    fmt = f"%(asctime)s [W{worker_id}] %(levelname)s %(message)s"
    logger = logging.getLogger(f"scrape_worker_{worker_id}")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(fmt))

        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter(fmt))

        logger.addHandler(fh)
        logger.addHandler(ch)

    return logger


# ────────────────────────────────────────────────────────────────
# グローバルキャンセルフラグ
# ────────────────────────────────────────────────────────────────
_cancel_requested = False


def _request_cancel(signum, frame):
    global _cancel_requested
    _cancel_requested = True
    print("\n[Ctrl+C] キャンセル要求を受け付けました。現在のレース完了後に中断します...")


signal.signal(signal.SIGINT, _request_cancel)
try:
    signal.signal(signal.SIGTERM, _request_cancel)
except (OSError, ValueError):
    pass


# ────────────────────────────────────────────────────────────────
# メイン非同期関数
# ────────────────────────────────────────────────────────────────

async def run_worker(
    start_date: str,
    end_date: str,
    worker_id: int,
    force_rescrape: bool,
    dry_run: bool,
    logger: logging.Logger,
) -> None:
    global _cancel_requested

    ULTIMATE_DB = _API_DIR.parent / "keiba" / "data" / "keiba_ultimate.db"
    _init_sqlite_db(ULTIMATE_DB)

    proxy_info = SCRAPE_PROXY_URL or "なし (シングルIP)"
    logger.info(
        f"━━━ Worker {worker_id} 開始 ━━━  期間={start_date}～{end_date}"
        f"  proxy={proxy_info}  force={force_rescrape}"
    )

    # ── 処理対象日リスト（全日付展開）──
    s_dt = _parse_date(start_date)
    e_dt = _parse_date(end_date)
    all_dates: list[str] = []
    cur = s_dt
    while cur <= e_dt:
        all_dates.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)

    # ── カレンダーで開催日のみに絞り込み ──
    logger.info(f"カレンダー取得中: {start_date}～{end_date} ({len(all_dates)}日 → 開催日のみに絞り込み)")
    _calendar_dates, _calendar_was_blocked = await _build_race_dates_from_calendar(
        start_date, end_date
    )
    if _calendar_dates is not None:
        orig = len(all_dates)
        dates = sorted(set(all_dates) & set(_calendar_dates))
        logger.info(f"カレンダー絞り込み完了: {orig}日 → {len(dates)}日（開催日のみ）")
    else:
        dates = all_dates
        if _calendar_was_blocked:
            logger.warning("カレンダー HTTP 400 → IPブロック疑い。全日付で処理します（フォールバック）")
        else:
            logger.warning("カレンダー取得失敗 → 全日付で処理（フォールバック）")

    total = len(dates)
    logger.info(
        f"処理対象: {total}日"
        f" ({dates[0] if dates else '-'}～{dates[-1] if dates else '-'})"
    )

    if dry_run:
        _years = (e_dt - s_dt).days / 365.25
        _est_races = _years * 2700
        _est_days_30s = _est_races * 30 / 86400
        logger.info(
            f"[dry-run] 処理日数={total}  推定レース数={int(_est_races)}"
            f"  推定所要時間={_est_days_30s:.1f}日 (30s/race・シングルIP試算)"
            f" / {_est_days_30s/4:.1f}日 (4Worker並列試算)"
        )
        return

    # ── 取得済み日付をスキップ（レジューム対応）──
    scraped_dates: set[str] = set()
    if not force_rescrape:
        try:
            local_scraped = await asyncio.to_thread(
                _get_scraped_dates_sqlite, ULTIMATE_DB, 6
            )
            scraped_dates.update(local_scraped)
            skip_count = sum(1 for d in dates if d in scraped_dates)
            if skip_count:
                logger.info(f"取得済みスキップ: {skip_count}日 → 残り {total - skip_count}日")
        except Exception as e:
            logger.warning(f"取得済み日付確認失敗: {e}")

    # ── スクレイピング開始 ──
    start_time = time.time()
    counter = {"races": 0, "horses": 0}
    counter_lock = asyncio.Lock()
    _consecutive_block_count = 0
    _BLOCK_THRESHOLD = 15

    session = _new_session()
    try:
        _oikiri_enabled = await login_netkeiba(session)
        if _oikiri_enabled:
            logger.info("調教タイム取得: 有効（プレミアム会員ログイン済み）")
        else:
            logger.info("調教タイム取得: スキップ（NETKEIBA_EMAIL/PASSWORD 未設定またはログイン失敗）")
        await asyncio.sleep(_jitter(1.5))

        for i, date in enumerate(dates):
            # ── キャンセルチェック ──
            if _cancel_requested:
                logger.info(f"キャンセル要求を検知 → 中断 ({i}/{total}日目 処理済み)")
                break

            # ── セッションローテーション (50日ごと) ──
            if i > 0 and i % _SESSION_ROTATE_DAYS == 0:
                logger.info(f"セッションローテーション ({i}/{total}日目)")
                await session.close()
                await asyncio.sleep(_jitter(30.0, ratio=0.2))
                session = _new_session()
                _oikiri_enabled = await login_netkeiba(session)
                await asyncio.sleep(_jitter(2.0))
                _consecutive_block_count = 0

            # ── クールダウン (100日ごとに5分休憩) ──
            if i > 0 and i % _COOLDOWN_EVERY_DAYS == 0:
                logger.info(f"クールダウン ({i}/{total}日目) → 5分間休憩")
                await asyncio.sleep(300.0)

            # ── インターバル計算 ──
            _days_ago = (
                date_cls.today()
                - date_cls(int(date[:4]), int(date[4:6]), int(date[6:8]))
            ).days
            _is_recent = _days_ago <= 30
            _pre_sleep = _jitter(_PRE_SLEEP_RECENT if _is_recent else _PRE_SLEEP_OLD)
            _inter_race_sleep = _jitter(_INTER_RACE_SLEEP)
            _post_sleep = _jitter(_POST_SLEEP_RECENT if _is_recent else _POST_SLEEP_OLD)

            # ── 取得済みスキップ ──
            if date in scraped_dates:
                if (i + 1) % 50 == 0:
                    logger.info(
                        f"[{i+1}/{total}] 取得済みスキップ中... "
                        f"累計={counter['races']}レース"
                    )
                continue

            _day_races_before = counter["races"]
            race_ids: list[str] = []
            errors: list[str] = []
            list_url = f"https://db.netkeiba.com/race/list/{date}/"
            _ip_blocked = False

            try:
                await asyncio.sleep(_pre_sleep)

                # ── ① レース一覧取得 (db.netkeiba.com) ──
                async with session.get(list_url) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        html = content.decode("euc-jp", errors="ignore")
                        del content
                        race_ids = list(dict.fromkeys(re.findall(r"/race/(\d{12})/", html)))
                        del html
                        _consecutive_block_count = 0
                    elif resp.status == 400:
                        body = await resp.read()
                        if len(body) == 0:
                            _consecutive_block_count += 1
                            logger.info(
                                f"{date} [{i+1}/{total}]: HTTP 400 空 → "
                                f"非開催日/IPブロック（連続{_consecutive_block_count}日）"
                            )
                            if _consecutive_block_count >= _BLOCK_THRESHOLD:
                                _ip_blocked = True
                                logger.error(
                                    f"連続 {_consecutive_block_count} 日 HTTP400 → IPブロック判定。"
                                    f" VPNのIP変更後に再実行してください。最終日付: {date}"
                                )
                                raise RuntimeError(
                                    f"HTTP 400 連続 {_consecutive_block_count} 日 → IPブロック。"
                                    f" 最終処理日付: {date}"
                                )
                        else:
                            _consecutive_block_count = 0
                            logger.info(f"{date}: HTTP 400 → 未開催日 ({len(body)}B)")
                    elif resp.status in (403, 429, 503):
                        _ip_blocked = True
                        raise RuntimeError(
                            f"HTTP {resp.status} IPブロック検知 → 即停止。最終処理日付: {date}"
                        )
                    else:
                        logger.warning(f"{date}: HTTP {resp.status}")

                # ── ② 直近レースは race.netkeiba.com フォールバック ──
                if not race_ids and _is_recent:
                    sub_url = (
                        f"https://race.netkeiba.com/top/race_list_sub.html"
                        f"?kaisai_date={date}"
                    )
                    try:
                        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as hx:
                            r2 = await hx.get(sub_url)
                        if r2.status_code == 200:
                            html2 = r2.content.decode("euc-jp", errors="replace")
                            soup2 = BeautifulSoup(html2, "lxml")
                            seen: set[str] = set()
                            for a in soup2.find_all("a", href=True):
                                m = re.search(r"race_id=(\d{12})", a["href"])
                                if m and m.group(1) not in seen:
                                    seen.add(m.group(1))
                                    race_ids.append(m.group(1))
                            logger.info(f"{date}: race_list_sub → {len(race_ids)}件")
                    except Exception as fe:
                        logger.warning(f"{date}: race_list_sub 失敗: {fe}")

                logger.info(
                    f"{date} [{i+1}/{total}]: {len(race_ids)}レース  "
                    f"累計={counter['races']}レース/{counter['horses']}頭  "
                    f"経過={_elapsed_str(start_time)}"
                )

                # ── ③ 各レースをスクレイプ＆保存 ──
                async def _fetch_and_save(race_id: str, _date: str = date) -> None:
                    n = await _scrape_and_save_race(
                        session, race_id, _date, ULTIMATE_DB, _oikiri_enabled, errors
                    )
                    if n > 0:
                        async with counter_lock:
                            counter["races"] += 1
                            counter["horses"] += n

                for ci in range(len(race_ids)):
                    await _fetch_and_save(race_ids[ci])
                    if ci + 1 < len(race_ids):
                        await asyncio.sleep(_inter_race_sleep)
                    gc.collect()

                if errors:
                    logger.warning(f"{date}: エラー {len(errors)}件: {errors[:3]}")

            except RuntimeError:
                raise  # IPブロック → 外側で処理
            except Exception as e:
                logger.error(f"{date} 処理エラー: {e}")

            _day_saved = counter["races"] - _day_races_before
            logger.info(
                f"{date} [{i+1}/{total}] 完了: 検出={len(race_ids)}  保存={_day_saved}"
                f"  エラー={len(errors)}"
            )

            # ── 取得済みとして記録（次回レジューム用）──
            try:
                _age = (
                    date_cls.today()
                    - date_cls(int(date[:4]), int(date[4:6]), int(date[6:8]))
                ).days
                if not _ip_blocked and not (_calendar_was_blocked and _day_saved == 0) and not (_day_saved == 0 and _age < 7):
                    await asyncio.to_thread(
                        _save_scraped_date_sqlite, ULTIMATE_DB, date, _day_saved
                    )
            except Exception:
                pass

            if i < total - 1:
                await asyncio.sleep(_post_sleep)

    except RuntimeError as e:
        logger.error(f"IPブロック検知で停止: {e}")
    finally:
        await session.close()

    # ── 完了サマリー ──
    elapsed = time.time() - start_time
    logger.info(
        f"━━━ Worker {worker_id} 完了 ━━━"
        f"  保存={counter['races']}レース/{counter['horses']}頭"
        f"  処理日数={total}日"
        f"  所要時間={elapsed/3600:.2f}時間 ({elapsed/60:.1f}分)"
    )
    if total > 0 and counter["races"] > 0:
        per_race = elapsed / counter["races"]
        logger.info(f"  平均速度: {per_race:.1f}秒/レース")


def _elapsed_str(start: float) -> str:
    s = int(time.time() - start)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}h{m:02d}m{s:02d}s"


# ────────────────────────────────────────────────────────────────
# CLI エントリーポイント
# ────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="並列スクレイプ Worker — 年代範囲を指定して直接 DB に書き込む",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
年代分割の例 (4 Worker / 異なるIP):
  Worker 1: --start 20160101 --end 20181231 --proxy http://vpn1:8080 --worker-id 1
  Worker 2: --start 20190101 --end 20211231 --proxy http://vpn2:8080 --worker-id 2
  Worker 3: --start 20220101 --end 20241231 --proxy http://vpn3:8080 --worker-id 3
  Worker 4: --start 20250101 --end 20251231 --proxy http://vpn4:8080 --worker-id 4
""",
    )
    p.add_argument("--start", required=True, help="開始日 (YYYYMMDD 形式)")
    p.add_argument("--end",   required=True, help="終了日 (YYYYMMDD 形式)")
    p.add_argument("--proxy",     default=None, help="プロキシ URL (例: http://user:pass@host:port)")
    p.add_argument("--worker-id", type=int, default=1, help="Worker 識別番号 (ログ区別用, デフォルト=1)")
    p.add_argument("--force",     action="store_true", help="取得済み日付も強制再スクレイプ")
    p.add_argument("--dry-run",   action="store_true", help="処理日数の確認のみ実行 (スクレイプなし)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    logger = _setup_logger(args.worker_id)

    # --proxy が指定されていれば env に再設定（early_proxy は import 前に処理済みだが
    # ここで logger 経由に記録する）
    if args.proxy:
        os.environ["SCRAPE_PROXY_URL"] = args.proxy
        logger.info(f"プロキシ設定: {args.proxy}")

    logger.info(
        f"scrape_worker.py 起動"
        f"  start={args.start}  end={args.end}"
        f"  worker_id={args.worker_id}  force={args.force}  dry_run={args.dry_run}"
    )

    asyncio.run(
        run_worker(
            start_date=args.start,
            end_date=args.end,
            worker_id=args.worker_id,
            force_rescrape=args.force,
            dry_run=args.dry_run,
            logger=logger,
        )
    )


if __name__ == "__main__":
    main()

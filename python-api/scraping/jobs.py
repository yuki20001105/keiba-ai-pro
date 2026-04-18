"""
スクレイピングジョブ管理: バックグラウンドジョブの開始・進捗管理。
"""
from __future__ import annotations

import asyncio
import gc
import json
import re
import sqlite3
import threading
from pathlib import Path

import aiohttp
import httpx
from bs4 import BeautifulSoup

from scraping.constants import SCRAPE_HEADERS, SCRAPE_PROXY_URL, get_random_headers
from scraping.race import scrape_race_full
from scraping.storage import (
    _init_sqlite_db,
    _save_race_sqlite_only,
    _get_scraped_dates_sqlite,
    _save_scraped_date_sqlite,
)

try:
    from app_config import logger  # type: ignore
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


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
# カレンダーから開催日を取得するヘルパー
# ============================================================

async def _fetch_race_days_for_month(year: int, month: int, timeout_sec: float = 15.0) -> list[str]:
    """race.netkeiba.com のカレンダーから指定年月の開催日一覧を取得する。

    返り値: ['YYYYMMDD', ...] のリスト（開催日のみ）。取得失敗時は空リスト。
    参照実装に倣い kaisai_date リンクからレース開催日を抽出する。
    """
    url = f"https://race.netkeiba.com/top/calendar.html?year={year}&month={month:02d}"
    try:
        async with httpx.AsyncClient(
            timeout=timeout_sec,
            follow_redirects=True,
            headers=get_random_headers(),
        ) as hx:
            resp = await hx.get(url)
        if resp.status_code != 200:
            logger.debug(f"カレンダー HTTP {resp.status_code}: {year}/{month:02d}")
            return []
        html = resp.content.decode("euc-jp", errors="replace")
        # href="/top/race_list.html?kaisai_date=20240105" 形式のリンクから日付を抽出
        dates = list(dict.fromkeys(re.findall(r"kaisai_date=(\d{8})", html)))
        logger.info(f"カレンダー取得: {year}/{month:02d} → {len(dates)}日 {dates[:3]}")
        return dates
    except Exception as e:
        logger.debug(f"カレンダー取得失敗 {year}/{month:02d}: {e}")
        return []


async def _build_race_dates_from_calendar(
    start_date: str, end_date: str
) -> list[str] | None:
    """開始〜終了日の範囲内でカレンダーから実際の開催日だけを収集する。

    カレンダー取得に失敗した月がある場合は None を返し、呼び出し元が全日付フォールバックを行う。
    取得成功の場合は開催日のみのリストを返す（大幅な無駄リクエスト削減）。
    """
    from datetime import datetime as _dt, timedelta as _td

    def _parse(s):
        for fmt in ("%Y%m%d", "%Y/%m/%d", "%Y-%m-%d"):
            try:
                return _dt.strptime(s, fmt)
            except ValueError:
                pass
        raise ValueError(f"日付フォーマット不正: {s}")

    s_dt = _parse(start_date)
    e_dt = _parse(end_date)

    # 対象年月の一覧（重複なし）
    months: list[tuple[int, int]] = []
    cur = s_dt.replace(day=1)
    while cur <= e_dt:
        months.append((cur.year, cur.month))
        # 翌月へ
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)

    all_dates: list[str] = []
    for year, month in months:
        days = await _fetch_race_days_for_month(year, month)
        if days is None:
            # 取得失敗 → 全日フォールバック
            return None
        all_dates.extend(days)
        await asyncio.sleep(1.0)  # カレンダーリクエスト間インターバル

    # 指定範囲でフィルタ
    s_str = s_dt.strftime("%Y%m%d")
    e_str = e_dt.strftime("%Y%m%d")
    filtered = [d for d in all_dates if s_str <= d <= e_str]
    return sorted(set(filtered))


# ============================================================
# ジョブストア（メモリ上 + SQLite二重管理）
# ============================================================
_scrape_jobs: dict = {}
_MAX_JOBS = 50
# スレッドセーフな _scrape_jobs アクセスのためのロック
# （FastAPI メインスレッドと scrape バックグラウンドスレッドが同時にアクセスするため）
# RLock（再入可能ロック）を使用: scrape_start が _JOBS_LOCK を保持したまま
# _purge_old_jobs を呼び出すとデッドロックする問題を防ぐ
_JOBS_LOCK = threading.RLock()


def _purge_old_jobs(store: dict, max_keep: int = _MAX_JOBS) -> None:
    """completed/error ジョブを古い順に削除してメモリリークを防ぐ"""
    with _JOBS_LOCK:
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

        _MIN_RACES_PER_DAY = 6

        # ── ① 前処理A: カレンダーから実際の開催日のみに絞り込み（歴史データ高速化）──
        # 30日以上前のデータが含まれる場合はカレンダーAPIで開催日を事前取得し
        # 開催なし日のリクエストをゼロにする（最大70%以上の削減効果）
        from datetime import date as _date_cls
        _oldest = min(dates)
        _oldest_days_ago = (_date_cls.today() - _date_cls(int(_oldest[:4]), int(_oldest[4:6]), int(_oldest[6:8]))).days
        if _oldest_days_ago > 30 and not force_rescrape:
            job["progress"] = {"done": 0, "total": len(dates), "message": "カレンダー取得中..."}
            logger.info(f"カレンダー取得開始: {start_date}〜{end_date} ({len(dates)}日 → 開催日のみに絞り込み)")
            _calendar_dates = await _build_race_dates_from_calendar(start_date, end_date)
            if _calendar_dates is not None:
                _original_count = len(dates)
                dates = sorted(set(dates) & set(_calendar_dates))
                logger.info(f"カレンダー絞り込み完了: {_original_count}日 → {len(dates)}日（開催日のみ）")
            else:
                logger.warning("カレンダー取得失敗 → 全日付で処理（フォールバック）")

        total = len(dates)
        job["progress"] = {"done": 0, "total": total, "message": f"0/{total}日処理済み"}

        # ── ② 前処理B: 取得済み日付を SQLite から読み込み（レジューム）──
        scraped_dates: set = set()
        if not force_rescrape:
            try:
                _local_scraped = await asyncio.to_thread(
                    _get_scraped_dates_sqlite, ULTIMATE_DB, _MIN_RACES_PER_DAY
                )
                scraped_dates.update(_local_scraped)
                if _local_scraped:
                    logger.info(f"SQLite取得済み日付: {len(_local_scraped)}日分をスキップ")
                    job["progress"]["message"] = f"{len(_local_scraped)}日分は取得済み、スキップします"
            except Exception as _e:
                logger.warning(f"SQLite取得済み確認失敗: {_e}")

        timeout = aiohttp.ClientTimeout(total=25, connect=8)
        connector = aiohttp.TCPConnector(limit=5, limit_per_host=3)
        counter = {"races": 0, "horses": 0}
        counter_lock = asyncio.Lock()

        # プロキシ設定（環境変数 SCRAPE_PROXY_URL で指定）
        _session_kwargs: dict = {}
        if SCRAPE_PROXY_URL:
            _session_kwargs["trust_env"] = False
            logger.info(f"プロキシ使用: {SCRAPE_PROXY_URL}")

        # 2024/11以降 netkeiba はランダムUAが必要 → 各ジョブで新規ランダムUA
        _session_headers = get_random_headers()
        logger.info(f"セッションUA: {_session_headers['User-Agent'][:60]}...")

        async with aiohttp.ClientSession(
            headers=_session_headers, timeout=timeout, connector=connector, **_session_kwargs
        ) as session:
            for i, date in enumerate(dates):
                list_url = f"https://db.netkeiba.com/race/list/{date}/"
                errors: list = []

                # 過去30日以内か判定（インターバル・フォールバック制御用）
                _days_ago = (_date_cls.today() - _date_cls(int(date[:4]), int(date[4:6]), int(date[6:8]))).days
                _is_recent = _days_ago <= 30
                # 過去データは db.netkeiba.com のみ → 短いインターバルで高速化
                _pre_sleep = 2.0 if _is_recent else 1.0
                _inter_race_sleep = 2.0 if _is_recent else 1.0
                _post_sleep = 8.0 if _is_recent else 2.0

                if date in scraped_dates:
                    logger.info(f"{date}: 取得済み（SQLite/Supabase）→ スキップ")
                    job["progress"] = {
                        "done": i + 1,
                        "total": total,
                        "message": f"{i+1}/{total}日処理済み / {counter['races']}レース保存 (スキップ含む)",
                        "saved_races": counter["races"],
                        "saved_horses": counter["horses"],
                    }
                    continue

                _day_races_before = counter["races"]  # この日の保存開始前レース数を記録
                race_ids: list[str] = []  # try ブロック外で初期化（except後にも参照可能）
                try:
                    await asyncio.sleep(_pre_sleep)  # レース一覧リクエスト間のインターバル

                    # ① db.netkeiba.com（過去レース結果ページ）から race ID を取得
                    race_ids = []
                    async with session.get(list_url) as resp:
                        if resp.status == 200:
                            content = await resp.read()
                            html = content.decode("euc-jp", errors="ignore")
                            del content
                            race_ids = list(dict.fromkeys(re.findall(r"/race/(\d{12})/", html)))
                            del html
                        elif resp.status == 400:
                            logger.info(f"{date}: db.netkeiba.com HTTP 400 → 未開催または削除済み日付")
                        else:
                            logger.warning(f"{date}: db.netkeiba.com HTTP {resp.status}")

                    # ② 0件のとき → race.netkeiba.com（race_list_sub）へフォールバック
                    #    当日・直近未来レースはこちらにしか載っていない
                    #    ※ 過去日付（30日超）には 400 を返すため使用しない
                    if not race_ids and _is_recent:
                        shutuba_url = (
                            f"https://race.netkeiba.com/top/race_list_sub.html"
                            f"?kaisai_date={date}"
                        )
                        try:
                            async with httpx.AsyncClient(
                                timeout=20.0, follow_redirects=True
                            ) as hx:
                                resp2 = await hx.get(shutuba_url)
                            if resp2.status_code == 200:
                                # Content-Type の charset が空なので EUC-JP で明示的にデコード
                                html2 = resp2.content.decode("euc-jp", errors="replace")
                                soup2 = BeautifulSoup(html2, "lxml")
                                found_ids: list[str] = []
                                for a in soup2.find_all("a", href=True):
                                    m = re.search(r"race_id=(\d{12})", a["href"])
                                    if m:
                                        found_ids.append(m.group(1))
                                race_ids = list(dict.fromkeys(found_ids))
                                logger.info(
                                    f"{date}: race.netkeiba.com から {len(race_ids)} レースID検出"
                                    f" (race_list_sub, HTML {len(html2)}chars)"
                                )
                                if not race_ids:
                                    logger.warning(
                                        f"{date}: race_list_sub.html HTMLサンプル(EUC-JP): "
                                        f"{html2[:300]!r}"
                                    )
                            else:
                                logger.warning(
                                    f"{date}: レース一覧 HTTP {resp2.status_code} (db/race 両方失敗) → スキップ"
                                )
                                job["progress"] = {
                                    "done": i + 1,
                                    "total": total,
                                    "message": f"{i+1}/{total}日処理済み / {counter['races']}レース保存 (HTTP {resp2.status_code}スキップ)",
                                    "saved_races": counter["races"],
                                    "saved_horses": counter["horses"],
                                }
                                if resp2.status_code in (403, 429, 503):
                                    logger.warning(f"{date}: HTTP {resp2.status_code} → 60秒待機（IPブロック回避）")
                                    await asyncio.sleep(60.0)
                                continue
                        except Exception as _fe:
                            logger.warning(f"{date}: race.netkeiba.com 取得失敗: {_fe}")

                    logger.info(f"{date}: {len(race_ids)}レースID検出")

                    async def _fetch_and_save(race_id, _date=date, _day_idx=i):
                        try:
                            race_data = await scrape_race_full(
                                session, race_id, date_hint=_date, quick_mode=True
                            )
                            if race_data and race_data.get("horses"):
                                n_horses = len(race_data["horses"])
                                saved = await asyncio.to_thread(
                                    _save_race_sqlite_only, race_data, ULTIMATE_DB
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

                    for ci in range(0, len(race_ids), 1):
                        chunk = race_ids[ci : ci + 1]
                        await asyncio.gather(*[_fetch_and_save(r) for r in chunk])
                        if ci + 1 < len(race_ids):
                            await asyncio.sleep(_inter_race_sleep)  # レース間インターバル
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
                # この日の取得結果をSQLiteに記録（次回再開時にスキップ可能にする）
                # ⚠️ race_ids 数（URL候補）ではなく実際に DB 保存したレース数を使う
                #    Cloudflare ブロック等で保存 0 件の場合に誤スキップを防ぐ
                try:
                    _day_saved = counter["races"] - _day_races_before
                    await asyncio.to_thread(
                        _save_scraped_date_sqlite, ULTIMATE_DB, date, _day_saved
                    )
                except Exception:
                    pass
                # 進捗を SQLite に永続化（Render スピンダウン対策）
                _persist_job(job_id, job)
                # 日付間インターバル（最終日以外）
                if i < total - 1:
                    await asyncio.sleep(_post_sleep)

        saved_races = counter["races"]
        saved_horses = counter["horses"]
        elapsed = _time.time() - start_time
        with _JOBS_LOCK:
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
        with _JOBS_LOCK:
            if job_id in _scrape_jobs:
                _scrape_jobs[job_id]["status"] = "error"
                _scrape_jobs[job_id]["error"] = str(e)
        _persist_job(job_id, _scrape_jobs.get(job_id, {}))

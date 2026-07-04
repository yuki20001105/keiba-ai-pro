"""
スクレイピングジョブ管理: バックグラウンドジョブの開始・進捗管理。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import sqlite3
import threading
from pathlib import Path

import time
from datetime import datetime, timedelta, date as date_cls

import aiohttp

from scraping.constants import SCRAPE_PROXY_URL, get_random_headers, login_netkeiba
from scraping.job_queue import (
    filter_dates_for_run,
    get_queue_counts,
    init_date_queue,
    seed_dates,
)
from scraping.monitor import write_scraping_report
from scraping.pipeline import run_scraping_pipeline
from scraping.race import scrape_race_full
from scraping.oikiri import scrape_oikiri
from scraping.speed_figure import scrape_speed_figure
from scraping.storage import (
    _init_sqlite_db,
    _save_training_data,
    _save_speed_figures,
    _get_scraped_dates_sqlite,
)
from scraping.repository import ScrapingRepository

try:
    from app_config import logger  # type: ignore
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


async def _scrape_and_save_race(
    session,
    race_id: str,
    date: str,
    db_path: Path,
    oikiri_enabled: bool,
    errors: list,
    pedigree_cache: dict | None = None,
    force_rescrape: bool = False,
) -> int:
    """1レースのスクレイプ・DB保存・調教タイム・タイム指数を実行する共通ヘルパー。

    Returns:
        保存頭数 (失敗・データなし時は 0、スキップ時は -1)
    jobs.py の _run_scrape_job および scrape_worker.py の run_worker で共用する。
    pedigree_cache: ジョブ内共有の血統キャッシュディクト（複数レースで同一馬のDB読み出しを省略）。
    force_rescrape: True の場合はDB既存チェックをスキップ。
    """
    repo = ScrapingRepository(db_path)
    # レース単位スキップ: 既にDBに存在するレースは引き当てない（force_rescrape=False 時）
    if not force_rescrape and repo.race_exists(race_id):
        logger.debug(f"{race_id}: DB既存 → スキップ")
        return -1
    try:
        race_data = await scrape_race_full(
            session, race_id, date_hint=date, quick_mode=True,
            pedigree_cache=pedigree_cache,
        )
        if not race_data or not race_data.get("horses"):
            logger.warning(f"レースデータなし/出走馬なし: {race_id}")
            return 0
        n_horses = len(race_data["horses"])
        saved = await asyncio.to_thread(repo.save_race, race_data)
        del race_data
        if not saved:
            logger.warning(f"SQLite保存失敗: {race_id}")
            return 0
        logger.debug(f"保存: {race_id} ({n_horses}頭)")
        if oikiri_enabled:
            await asyncio.sleep(1.0)  # INV-07
            training_recs = await scrape_oikiri(session, race_id, is_logged_in=True)
            if training_recs:
                n_train = await asyncio.to_thread(
                    _save_training_data, race_id, training_recs, db_path
                )
                logger.info(f"調教タイム保存: {race_id} {n_train}件")
            await asyncio.sleep(1.0)  # INV-07
            speed_recs = await scrape_speed_figure(session, race_id, is_logged_in=True)
            if speed_recs:
                n_speed = await asyncio.to_thread(
                    _save_speed_figures, race_id, speed_recs, db_path
                )
                logger.info(f"タイム指数保存: {race_id} {n_speed}件")
        return n_horses
    except Exception as exc:
        errors.append(f"{race_id}: {exc}")
        logger.error(f"スクレイプ失敗 {race_id}: {exc}")
        return 0


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
        # スクレイプパラメータ履歴テーブル（HTTP400時の設定を記録して最適化に利用）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scrape_param_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                event TEXT NOT NULL,
                date_processing TEXT,
                pre_sleep_recent REAL,
                pre_sleep_old REAL,
                inter_race_sleep REAL,
                post_sleep_recent REAL,
                post_sleep_old REAL,
                stop_on_first_400 INTEGER,
                pre_sleep_actual REAL,
                inter_race_sleep_actual REAL,
                post_sleep_actual REAL,
                is_recent INTEGER,
                consecutive_400_count INTEGER,
                races_scraped INTEGER,
                days_scraped INTEGER,
                note TEXT
            )
        """)
        # 起動時: 前プロセスで中断されたジョブを cancelled にリセット（二重起動防止）
        # ※ blocked は意図的なIPブロック停止なので保持する
        conn.execute("""
            UPDATE scrape_jobs SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('running', 'queued')
        """)
        conn.commit()
        conn.close()
        # 日次ジョブキュー（PENDING/RUNNING/SUCCESS/FAILED/SKIP）
        init_date_queue(_JOBS_DB_PATH)
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


def _log_scrape_event(
    job_id: str,
    event: str,
    *,
    date_processing: str | None = None,
    pre_sleep_recent: float = 3.0,
    pre_sleep_old: float = 4.0,
    inter_race_sleep: float = 2.0,
    post_sleep_recent: float = 10.0,
    post_sleep_old: float = 12.0,
    stop_on_first_400: bool = False,
    pre_sleep_actual: float | None = None,
    inter_race_sleep_actual: float | None = None,
    post_sleep_actual: float | None = None,
    is_recent: bool | None = None,
    consecutive_400_count: int | None = None,
    races_scraped: int = 0,
    days_scraped: int = 0,
    note: str | None = None,
) -> None:
    """スクレイプパラメータと HTTP 400 イベントを scrape_param_log テーブルに記録する。"""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(str(_JOBS_DB_PATH))
        conn.execute(
            """
            INSERT INTO scrape_param_log (
                job_id, timestamp, event, date_processing,
                pre_sleep_recent, pre_sleep_old, inter_race_sleep,
                post_sleep_recent, post_sleep_old, stop_on_first_400,
                pre_sleep_actual, inter_race_sleep_actual, post_sleep_actual,
                is_recent, consecutive_400_count, races_scraped, days_scraped, note
            ) VALUES (?,?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?,?,?)
            """,
            (
                job_id, ts, event, date_processing,
                pre_sleep_recent, pre_sleep_old, inter_race_sleep,
                post_sleep_recent, post_sleep_old, 1 if stop_on_first_400 else 0,
                pre_sleep_actual, inter_race_sleep_actual, post_sleep_actual,
                (1 if is_recent else 0) if is_recent is not None else None,
                consecutive_400_count, races_scraped, days_scraped, note,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as _e:
        logger.debug(f"scrape_param_log 書き込み失敗: {_e}")


_init_jobs_db()


# ============================================================
# カレンダーキャッシュ（7日 TTL）
# ============================================================
_CAL_CACHE_INIT = False


def _ensure_cal_cache() -> None:
    global _CAL_CACHE_INIT
    if _CAL_CACHE_INIT:
        return
    try:
        conn = sqlite3.connect(str(_JOBS_DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cal_cache (
                year_month TEXT PRIMARY KEY,
                dates      TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
        _CAL_CACHE_INIT = True
    except Exception as e:
        logger.debug(f"_ensure_cal_cache error: {e}")


def _get_calendar_cache(year: int, month: int) -> list[str] | None:
    """7日以内のキャッシュがあれば返す。なければ None。"""
    _ensure_cal_cache()
    key = f"{year}{month:02d}"
    try:
        conn = sqlite3.connect(str(_JOBS_DB_PATH))
        row = conn.execute(
            "SELECT dates, updated_at FROM cal_cache WHERE year_month = ?", (key,)
        ).fetchone()
        conn.close()
        if row:
            updated = datetime.fromisoformat(row[1])
            if datetime.now() - updated < timedelta(days=7):
                return json.loads(row[0])
    except Exception:
        pass
    return None


def _save_calendar_cache(year: int, month: int, dates: list[str]) -> None:
    _ensure_cal_cache()
    key = f"{year}{month:02d}"
    try:
        conn = sqlite3.connect(str(_JOBS_DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT OR REPLACE INTO cal_cache (year_month, dates, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(dates), datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"_save_calendar_cache error {key}: {e}")


# ============================================================
# カレンダーから開催日を取得するヘルパー
# ============================================================

async def _fetch_race_days_for_month(
    year: int, month: int, timeout_sec: float = 15.0
) -> tuple[list[str] | None, bool]:
    """race.netkeiba.com のカレンダーから指定年月の開催日一覧を取得する。

    返り値: (dates_list, was_http_400_blocked)
      - dates_list: ['YYYYMMDD', ...] のリスト（開催日のみ）。取得失敗時は None。
      - was_http_400_blocked: HTTP 400 でブロックされていた場合 True（タイムアウト等は False）。
    過去月（今月より前）は SQLite に 7 日間キャッシュする。
    """
    today = datetime.now()
    is_past_month = (year, month) < (today.year, today.month)

    # 過去月はキャッシュから返す
    if is_past_month:
        cached = _get_calendar_cache(year, month)
        if cached is not None:
            logger.debug(f"カレンダーキャッシュヒット: {year}/{month:02d} → {len(cached)}日")
            return (cached, False)

    url = f"https://race.netkeiba.com/top/calendar.html?year={year}&month={month:02d}"
    try:
        _timeout = aiohttp.ClientTimeout(total=timeout_sec)
        _connector = aiohttp.TCPConnector(limit=1, limit_per_host=1, force_close=True)
        async with aiohttp.ClientSession(
            headers=get_random_headers(),
            timeout=_timeout,
            connector=_connector,
        ) as client:
            async with client.get(url) as resp:
                if resp.status != 200:
                    _is_blocked = resp.status == 400
                    logger.warning(
                        f"カレンダー HTTP {resp.status}: {year}/{month:02d} → フォールバック"
                        + (" [IPブロック疑い]" if _is_blocked else "")
                    )
                    return (None, _is_blocked)
                content = await resp.read()
        html = content.decode("euc-jp", errors="replace")
        # href="/top/race_list.html?kaisai_date=20240105" 形式のリンクから日付を抽出
        dates = list(dict.fromkeys(re.findall(r"kaisai_date=(\d{8})", html)))
        logger.info(f"カレンダー取得: {year}/{month:02d} → {len(dates)}日 {dates[:3]}")
        # 過去月はキャッシュに保存
        if is_past_month:
            _save_calendar_cache(year, month, dates)
        return (dates, False)
    except Exception as e:
        logger.warning(f"カレンダー取得失敗 {year}/{month:02d}: {e} → フォールバック")
        return (None, False)


def _parse_date(s: str) -> datetime:
    """日付文字列 (YYYYMMDD / YYYY/MM/DD / YYYY-MM-DD) を datetime に変換する"""
    for fmt in ("%Y%m%d", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"日付フォーマット不正: {s}")


async def _build_race_dates_from_calendar(
    start_date: str, end_date: str
) -> tuple[list[str] | None, bool]:
    """開始〜終了日の範囲内でカレンダーから実際の開催日だけを収集する。

    返り値: (dates, was_blocked)
      - dates: 開催日リスト。取得失敗時は None。
      - was_blocked: カレンダーが HTTP 400 でブロックされていた場合 True。
    カレンダー取得に失敗した月がある場合は (None, ...) を返し、
    呼び出し元が全日付フォールバックを行う。
    """
    s_dt = _parse_date(start_date)
    e_dt = _parse_date(end_date)

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
        days, was_blocked = await _fetch_race_days_for_month(year, month)
        if days is None:
            # 取得失敗 → 全日フォールバック
            return (None, was_blocked)
        all_dates.extend(days)
        await asyncio.sleep(1.0)  # カレンダーリクエスト間インターバル

    # 指定範囲でフィルタ
    s_str = s_dt.strftime("%Y%m%d")
    e_str = e_dt.strftime("%Y%m%d")
    filtered = [d for d in all_dates if s_str <= d <= e_str]
    return (sorted(set(filtered)), False)


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

# ============================================================
# ジョブキャンセル管理
# ============================================================
_CANCEL_FLAGS: dict[str, bool] = {}  # job_id → キャンセルフラグ（同一イベントループ内で参照）


def request_cancel_job(job_id: str) -> bool:
    """ジョブのキャンセルフラグを立てる。ジョブが存在する場合 True を返す。"""
    with _JOBS_LOCK:
        if job_id not in _scrape_jobs:
            return False
    _CANCEL_FLAGS[job_id] = True
    return True


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


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _compute_quality_score(
    *,
    parser_survival: float,
    validator_survival: float,
    repository_survival: float,
    retry_rate: float,
    quality_error_rate: float,
) -> float:
    # Composite score (0-100): lower data loss/retry/error produces higher confidence.
    score = 100.0
    score -= (1.0 - max(0.0, min(1.0, parser_survival))) * 35.0
    score -= (1.0 - max(0.0, min(1.0, validator_survival))) * 20.0
    score -= (1.0 - max(0.0, min(1.0, repository_survival))) * 30.0
    score -= max(0.0, retry_rate) * 10.0
    score -= max(0.0, quality_error_rate) * 20.0
    score = max(0.0, min(100.0, score))
    return round(score, 2)


def _build_quality_row(job_id: str, updated_at: str, result: dict) -> dict:
    lineage = result.get("lineage_records") or []
    quality_counts = result.get("quality_counts") or {}
    task_totals = result.get("task_totals") or {}
    policy_totals = result.get("policy_totals") or {}
    severity_totals = result.get("severity_totals") or {}

    downloader_ids = sum(int(r.get("downloader_ids", 0)) for r in lineage)
    parser_ids = sum(int(r.get("parser_ids", 0)) for r in lineage)
    validator_in = sum(int(r.get("validator_in", 0)) for r in lineage)
    validator_out = sum(int(r.get("validator_out", 0)) for r in lineage)
    repository_saved = sum(int(r.get("repository_saved", 0)) for r in lineage)
    cache_hits = sum(int(r.get("cache_hit", 0)) for r in lineage)
    lineage_days = len(lineage)
    total_quality_errors = sum(int(v) for v in quality_counts.values()) if isinstance(quality_counts, dict) else 0
    task_total = int(task_totals.get("TOTAL", 0))
    task_success = int(task_totals.get("SUCCESS", 0))
    task_failed = int(task_totals.get("FAILED", 0))
    task_skip = int(task_totals.get("SKIP", 0))

    parser_survival = _safe_ratio(parser_ids, downloader_ids)
    validator_survival = _safe_ratio(validator_out, validator_in)
    repository_survival = _safe_ratio(repository_saved, validator_out)
    retry_rate = _safe_ratio(int(policy_totals.get("RETRY", 0)), task_total)
    success_rate = _safe_ratio(task_success, task_total)
    cache_hit_rate = _safe_ratio(cache_hits, lineage_days)
    quality_error_rate = _safe_ratio(total_quality_errors, task_total)
    quality_score = _compute_quality_score(
        parser_survival=parser_survival,
        validator_survival=validator_survival,
        repository_survival=repository_survival,
        retry_rate=retry_rate,
        quality_error_rate=quality_error_rate,
    )

    top_error_codes: list[dict[str, int]] = []
    if isinstance(quality_counts, dict):
        top_error_codes = [
            {"code": str(k), "count": int(v)}
            for k, v in sorted(quality_counts.items(), key=lambda x: int(x[1]), reverse=True)[:5]
        ]

    return {
        "job_id": job_id,
        "updated_at": updated_at,
        "range": {
            "start_date": str((result.get("params") or {}).get("start_date") or ""),
            "end_date": str((result.get("params") or {}).get("end_date") or ""),
        },
        "metrics": {
            "processed_dates": int(result.get("processed_dates", 0)),
            "failed_dates": int(result.get("failed_dates", 0)),
            "saved_races": int(result.get("races_collected", 0)),
            "saved_horses": int(result.get("saved_horses", 0)),
            "elapsed_min": float(result.get("elapsed_min", 0.0)),
            "task_total": task_total,
            "task_success": task_success,
            "task_failed": task_failed,
            "task_skip": task_skip,
            "quality_errors": int(total_quality_errors),
            "policy_retry": int(policy_totals.get("RETRY", 0)),
            "policy_skip": int(policy_totals.get("SKIP", 0)),
            "policy_abort": int(policy_totals.get("ABORT", 0)),
            "policy_continue": int(policy_totals.get("CONTINUE", 0)),
            "severity_error": int(severity_totals.get("ERROR", 0)),
            "severity_fatal": int(severity_totals.get("FATAL", 0)),
            "quality_score": float(quality_score),
        },
        "funnel": {
            "downloader": int(downloader_ids),
            "parser": int(parser_ids),
            "validator_in": int(validator_in),
            "validator_out": int(validator_out),
            "repository": int(repository_saved),
        },
        "rates": {
            "parser_survival": parser_survival,
            "validator_survival": validator_survival,
            "repository_survival": repository_survival,
            "retry_rate": retry_rate,
            "success_rate": success_rate,
            "cache_hit_rate": cache_hit_rate,
        },
        "versions": {
            "parser_version": str(lineage[-1].get("parser_version", "")) if lineage else "",
            "rule_version": str(lineage[-1].get("rule_version", "")) if lineage else "",
        },
        "top_error_codes": top_error_codes,
    }


def get_quality_history(limit: int = 30) -> list[dict]:
    """完了済みスクレイプジョブから品質メトリクス履歴を返す。"""
    n = max(1, min(int(limit), 200))
    rows: list[dict] = []
    try:
        conn = sqlite3.connect(str(_JOBS_DB_PATH))
        cur = conn.execute(
            """
            SELECT job_id, result, updated_at
            FROM scrape_jobs
            WHERE status = 'completed' AND result IS NOT NULL AND result != 'null'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (n,),
        )
        data = cur.fetchall()
        conn.close()
    except Exception:
        return rows

    for job_id, result_raw, updated_at in data:
        try:
            result = json.loads(result_raw or "{}")
            if not isinstance(result, dict):
                continue
            rows.append(_build_quality_row(str(job_id), str(updated_at), result))
        except Exception:
            continue
    return rows


def get_quality_summary(limit: int = 30) -> dict:
    """品質メトリクスのサマリー（直近Nジョブ）を返す。"""
    history = get_quality_history(limit=limit)
    if not history:
        return {
            "window": {"jobs": 0, "limit": int(limit)},
            "funnel": {},
            "rates": {},
            "totals": {},
            "top_error_codes": [],
        }

    totals = {
        "processed_dates": 0,
        "saved_races": 0,
        "saved_horses": 0,
        "task_total": 0,
        "task_success": 0,
        "task_failed": 0,
        "task_skip": 0,
        "quality_errors": 0,
        "policy_retry": 0,
        "severity_error": 0,
        "severity_fatal": 0,
        "downloader": 0,
        "parser": 0,
        "validator_in": 0,
        "validator_out": 0,
        "repository": 0,
        "quality_score_sum": 0.0,
    }
    code_counter: dict[str, int] = {}

    for row in history:
        m = row.get("metrics") or {}
        f = row.get("funnel") or {}
        totals["processed_dates"] += int(m.get("processed_dates", 0))
        totals["saved_races"] += int(m.get("saved_races", 0))
        totals["saved_horses"] += int(m.get("saved_horses", 0))
        totals["task_total"] += int(m.get("task_total", 0))
        totals["task_success"] += int(m.get("task_success", 0))
        totals["task_failed"] += int(m.get("task_failed", 0))
        totals["task_skip"] += int(m.get("task_skip", 0))
        totals["quality_errors"] += int(m.get("quality_errors", 0))
        totals["policy_retry"] += int(m.get("policy_retry", 0))
        totals["severity_error"] += int(m.get("severity_error", 0))
        totals["severity_fatal"] += int(m.get("severity_fatal", 0))
        totals["downloader"] += int(f.get("downloader", 0))
        totals["parser"] += int(f.get("parser", 0))
        totals["validator_in"] += int(f.get("validator_in", 0))
        totals["validator_out"] += int(f.get("validator_out", 0))
        totals["repository"] += int(f.get("repository", 0))
        totals["quality_score_sum"] += float(m.get("quality_score", 0.0))
        for item in row.get("top_error_codes") or []:
            code = str(item.get("code") or "")
            if not code:
                continue
            code_counter[code] = int(code_counter.get(code, 0)) + int(item.get("count", 0))

    return {
        "window": {"jobs": len(history), "limit": int(limit)},
        "totals": totals,
        "funnel": {
            "downloader": totals["downloader"],
            "parser": totals["parser"],
            "validator_in": totals["validator_in"],
            "validator_out": totals["validator_out"],
            "repository": totals["repository"],
        },
        "rates": {
            "parser_survival": _safe_ratio(totals["parser"], totals["downloader"]),
            "validator_survival": _safe_ratio(totals["validator_out"], totals["validator_in"]),
            "repository_survival": _safe_ratio(totals["repository"], totals["validator_out"]),
            "retry_rate": _safe_ratio(totals["policy_retry"], totals["task_total"]),
            "success_rate": _safe_ratio(totals["task_success"], totals["task_total"]),
            "quality_score_avg": (float(totals["quality_score_sum"]) / float(len(history))),
        },
        "top_error_codes": [
            {"code": k, "count": int(v)}
            for k, v in sorted(code_counter.items(), key=lambda x: int(x[1]), reverse=True)[:10]
        ],
    }


# ============================================================
# HTTPセッション・インターバルヘルパー（Bot検知回避）
# ============================================================

# セッションローテーション間隔（日数）
_SESSION_ROTATE_DAYS = 80  # 旧50: 頻繁なローテーションは不要（大量スクレイプ時の無駄な30s休憩を削減）
# クールダウン間隔（日数ごとに5分休憩）
_COOLDOWN_EVERY_DAYS = 150  # 旧100

# スクレイピングインターバル基準値（INV-07: 最低1.0秒）
_PRE_SLEEP_RECENT  = 1.5   # 直近30日: 一覧取得前 (秒) — 旧2.0
_PRE_SLEEP_OLD     = 2.0   # 30日超: 一覧取得前 (秒) — 旧3.0
_INTER_RACE_SLEEP  = 1.0   # レース間 (秒) — 旧1.5 (INV-07最小値)
_POST_SLEEP_RECENT = 5.0   # 直近30日: 日付間 (秒) — 旧7.0
_POST_SLEEP_OLD    = 3.0   # 30日超: 日付間 (秒) — 旧4.0


def _jitter(base: float, ratio: float = 0.3) -> float:
    """インターバルに±ratio のランダムなゆらぎを加える（等間隔パターンによるBot判定回避）"""
    return base * (1.0 + random.uniform(-ratio, ratio))


def _new_session() -> aiohttp.ClientSession:
    """UA・Cookie・コネクションを一新したセッションを生成する（Bot検知回避）"""
    _timeout = aiohttp.ClientTimeout(total=25, connect=8)
    _connector = aiohttp.TCPConnector(limit=2, limit_per_host=1, force_close=True)
    _cookie_jar = aiohttp.CookieJar(unsafe=True)
    _hdrs = get_random_headers()
    logger.info(f"セッション生成: UA={_hdrs['User-Agent'][:55]}...")
    _kwargs: dict = {}
    if SCRAPE_PROXY_URL:
        _kwargs["trust_env"] = False
    return aiohttp.ClientSession(
        headers=_hdrs, timeout=_timeout, connector=_connector,
        cookie_jar=_cookie_jar, **_kwargs
    )


# ============================================================
# バックグラウンドスクレイピングジョブ
# ============================================================

async def _run_scrape_job(
    job_id: str, start_date: str, end_date: str, force_rescrape: bool = False,
):
    """バックグラウンドでスクレイピングを実行しジョブストアを更新する"""
    try:
        job = _scrape_jobs[job_id]
        job["status"] = "running"

        # ── ジョブ開始ログ ──
        logger.info(
            f"━━━ スクレイプジョブ開始 ━━━"
            f" job_id={job_id}"
            f" 期間={start_date}～{end_date}"
            f" force_rescrape={force_rescrape}"
        )
        _log_scrape_event(
            job_id, "job_start",
            note=f"start={start_date} end={end_date} force={force_rescrape}",
        )

        ULTIMATE_DB = Path(__file__).parent.parent.parent / "keiba" / "data" / "keiba_ultimate.db"
        _init_sqlite_db(ULTIMATE_DB)

        start_time = time.time()

        s_dt = _parse_date(start_date)
        e_dt = _parse_date(end_date)
        dates = []
        cur = s_dt
        while cur <= e_dt:
            dates.append(cur.strftime("%Y%m%d"))
            cur += timedelta(days=1)

        _MIN_RACES_PER_DAY = 6

        # ── ① 前処理A: カレンダーから実際の開催日のみに絞り込み（常に実行）──
        # カレンダーAPIで開催日を事前取得し、開催なし日へのリクエストをゼロにする。
        # force_rescrape でも必ず実行（非開催日リクエストは根本的に不要）。
        # ※ 旧: 30日超のみ適用 → 改善: 全期間に適用（直近も非開催日をスキップ）
        _calendar_was_blocked = False  # カレンダーが HTTP 400 でブロックされていた場合 True
        job["progress"] = {"done": 0, "total": len(dates), "message": "カレンダー取得中..."}
        logger.info(f"カレンダー取得開始: {start_date}～{end_date} ({len(dates)}日 → 開催日のみに絞り込み)")
        _calendar_dates, _calendar_was_blocked = await _build_race_dates_from_calendar(start_date, end_date)
        if _calendar_dates is not None:
            _original_count = len(dates)
            dates = sorted(set(dates) & set(_calendar_dates))
            logger.info(f"カレンダー絞り込み完了: {_original_count}日 → {len(dates)}日（開催日のみ）")
        elif _calendar_was_blocked:
            logger.warning(
                "カレンダー HTTP 400 → IPブロック検知。"
                " scraped_dates への no_race=1 書き込みを無効化します。"
            )
        else:
            logger.warning("カレンダー取得失敗 → 全日付で処理（フォールバック）")

        total = len(dates)
        failed_days = 0
        total_errors = 0

        # 日次Queueを初期化し、今回実行対象を絞り込む
        seed_dates(_JOBS_DB_PATH, dates, force_rescrape=force_rescrape)
        dates = filter_dates_for_run(_JOBS_DB_PATH, dates, force_rescrape=force_rescrape)
        total = len(dates)

        _job_params = {"start_date": start_date, "end_date": end_date, "force_rescrape": force_rescrape}
        job["progress"] = {
            "done": 0, "total": total, "message": f"0/{total}日処理済み",
            "params": _job_params,
        }
        logger.info(
            f"処理対象: {total}日 ({dates[0] if dates else '-'}～{dates[-1] if dates else '-'})"
            + (" ※カレンダー絞り込み済み" if _calendar_dates is not None else "")
        )

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

        pipeline_result = await run_scraping_pipeline(
            job_id=job_id,
            job=job,
            dates=dates,
            force_rescrape=force_rescrape,
            scraped_dates=scraped_dates,
            ultimate_db=ULTIMATE_DB,
            jobs_db_path=_JOBS_DB_PATH,
            start_time=start_time,
            calendar_was_blocked=_calendar_was_blocked,
            job_params=_job_params,
            cancel_flags=_CANCEL_FLAGS,
            persist_job=_persist_job,
            log_scrape_event=_log_scrape_event,
            scrape_and_save_race=_scrape_and_save_race,
            jitter=_jitter,
            new_session=_new_session,
            login_netkeiba=login_netkeiba,
            logger=logger,
            session_rotate_days=_SESSION_ROTATE_DAYS,
            cooldown_every_days=_COOLDOWN_EVERY_DAYS,
            pre_sleep_recent=_PRE_SLEEP_RECENT,
            pre_sleep_old=_PRE_SLEEP_OLD,
            inter_race_sleep_base=_INTER_RACE_SLEEP,
            post_sleep_recent=_POST_SLEEP_RECENT,
            post_sleep_old=_POST_SLEEP_OLD,
        )
        if pipeline_result.get("cancelled"):
            return

        saved_races = int(pipeline_result.get("saved_races", 0))
        saved_horses = int(pipeline_result.get("saved_horses", 0))
        failed_days = int(pipeline_result.get("failed_days", 0))
        total_errors = int(pipeline_result.get("total_errors", 0))
        task_totals = pipeline_result.get("task_totals", {})
        quality_counts = pipeline_result.get("quality_counts", {})
        severity_totals = pipeline_result.get("severity_totals", {})
        policy_totals = pipeline_result.get("policy_totals", {})
        lineage_records = pipeline_result.get("lineage_records", [])
        event_counts = pipeline_result.get("event_counts", {})
        elapsed = float(pipeline_result.get("elapsed", time.time() - start_time))
        elapsed_min = elapsed / 60
        logger.info(
            f"━━━ スクレイプジョブ完了 ━━━"
            f" job_id={job_id}"
            f" 期間={start_date}～{end_date}"
            f" 保存={saved_races}レース/{saved_horses}頭"
            f" 処理日数={total}日"
            f" 所要時間={elapsed_min:.1f}分"
        )
        _log_scrape_event(
            job_id, "job_completed",
            races_scraped=saved_races,
            days_scraped=total,
            note=f"elapsed_min={elapsed_min:.1f}",
        )

        try:
            _queue_counts = get_queue_counts(_JOBS_DB_PATH)
            _report_path = write_scraping_report(
                start_date=start_date,
                end_date=end_date,
                processed_dates=total,
                saved_races=saved_races,
                saved_horses=saved_horses,
                elapsed_min=elapsed_min,
                queue_counts=_queue_counts,
                errors=total_errors,
                task_totals=task_totals,
                quality_counts=quality_counts,
                severity_totals=severity_totals,
                policy_totals=policy_totals,
                lineage_records=lineage_records,
            )
            logger.info(f"スクレイピングサマリ保存: {_report_path}")
        except Exception as _report_err:
            logger.warning(f"スクレイピングサマリ保存失敗: {_report_err}")

        with _JOBS_LOCK:
            job["status"] = "completed"
            job["result"] = {
                "success": True,
                "races_collected": saved_races,
                "saved_horses": saved_horses,
                "elapsed_time": elapsed,
                "elapsed_min": round(elapsed_min, 1),
                "processed_dates": total,
                "failed_dates": failed_days,
                "task_totals": task_totals,
                "quality_counts": quality_counts,
                "severity_totals": severity_totals,
                "policy_totals": policy_totals,
                "lineage_records": lineage_records,
                "event_counts": event_counts,
                "params": _job_params,
                "message": (
                    f"{saved_races}レース・{saved_horses}頭のデータを収集しました"
                    f" ({elapsed_min:.1f}分 / {total}日処理)"
                ),
            }
        _persist_job(job_id, job)
    except RuntimeError as e:
        _err_msg = str(e)
        if "IPブロック検知" in _err_msg or "IPブロック継続" in _err_msg:
            logger.warning(f"スクレイピングジョブ停止（IPブロック）{job_id}: {_err_msg}")
            with _JOBS_LOCK:
                if job_id in _scrape_jobs:
                    _scrape_jobs[job_id]["status"] = "blocked"
                    _scrape_jobs[job_id]["error"] = _err_msg
            _persist_job(job_id, _scrape_jobs.get(job_id, {}))
        else:
            logger.error(f"スクレイピングジョブ失敗 {job_id}: {_err_msg}")
            with _JOBS_LOCK:
                if job_id in _scrape_jobs:
                    _scrape_jobs[job_id]["status"] = "error"
                    _scrape_jobs[job_id]["error"] = _err_msg
            _persist_job(job_id, _scrape_jobs.get(job_id, {}))
    except Exception as e:
        logger.error(f"スクレイピングジョブ失敗 {job_id}: {e}")
        with _JOBS_LOCK:
            if job_id in _scrape_jobs:
                _scrape_jobs[job_id]["status"] = "error"
                _scrape_jobs[job_id]["error"] = str(e)
        _persist_job(job_id, _scrape_jobs.get(job_id, {}))

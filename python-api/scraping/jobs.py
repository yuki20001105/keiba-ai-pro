"""
スクレイピングジョブ管理: バックグラウンドジョブの開始・進捗管理。
"""
from __future__ import annotations

import asyncio
import gc
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

import aiohttp
import httpx

from scraping.constants import SCRAPE_HEADERS, SCRAPE_PROXY_URL, get_random_headers
from scraping.fetch_pipeline import (
    estimate_fetch_plan,
    fetch_text,
    get_fetch_metrics,
    write_fetch_summary,
)
from scraping.race import scrape_race_full
from scraping.race_list import fetch_race_ids
from scraping.quality import (
    classify_race_quality,
    completed_quality_dates,
    excluded_standard_dates,
    excluded_standard_race_ids,
    hydrate_quality_from_existing_data,
    init_acquisition_quality_db,
    record_date_expectation,
    record_date_failure,
    record_race_failure,
    record_race_quality,
    run_date_repair_audit,
    summarize_acquisition_quality,
)
from scraping.scrape_request_contract import MAX_SCRAPE_TARGETS, build_bounded_scrape_dates
from scraping.storage import (
    _get_scraped_dates_sqlite,
    _init_sqlite_db,
    _save_race_sqlite_only,
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
_PEDIGREE_DB_PATH: Path = Path(__file__).parent.parent.parent / "keiba" / "data" / "pedigree_cache.db"

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - requirements-lock includes psutil
    psutil = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class JobStoreUnavailable(RuntimeError):
    """Raised when durable scrape-job state cannot be read or written safely."""


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
                request_json TEXT DEFAULT '{}',
                heartbeat_at TEXT,
                resume_count INTEGER NOT NULL DEFAULT 0,
                worker_pid INTEGER,
                last_checkpoint TEXT,
                owner_user_id TEXT NOT NULL DEFAULT '',
                request_hash TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(scrape_jobs)").fetchall()}
        migrations = {
            "request_json": "TEXT DEFAULT '{}'",
            "heartbeat_at": "TEXT",
            "resume_count": "INTEGER NOT NULL DEFAULT 0",
            "worker_pid": "INTEGER",
            "last_checkpoint": "TEXT",
            "owner_user_id": "TEXT NOT NULL DEFAULT ''",
            "request_hash": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in migrations.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE scrape_jobs ADD COLUMN {column} {definition}")
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"ジョブDB初期化失敗: {e}")


def _persist_job(job_id: str, job: dict) -> bool:
    """Persist job state and report whether the durable write succeeded."""
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(_JOBS_DB_PATH), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.execute("""
            INSERT INTO scrape_jobs (
                job_id, status, progress, result, error,
                request_json, heartbeat_at, resume_count, worker_pid, last_checkpoint,
                owner_user_id, request_hash, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                progress = excluded.progress,
                result = excluded.result,
                error = excluded.error,
                request_json = excluded.request_json,
                heartbeat_at = excluded.heartbeat_at,
                resume_count = excluded.resume_count,
                worker_pid = excluded.worker_pid,
                last_checkpoint = excluded.last_checkpoint,
                owner_user_id = scrape_jobs.owner_user_id,
                request_hash = scrape_jobs.request_hash,
                updated_at = CURRENT_TIMESTAMP
            WHERE scrape_jobs.owner_user_id = excluded.owner_user_id
              AND scrape_jobs.request_hash = excluded.request_hash
        """, (
            job_id,
            job.get("status", "unknown"),
            json.dumps(job.get("progress", {}), ensure_ascii=False),
            json.dumps(job.get("result"), ensure_ascii=False),
            json.dumps(job.get("error"), ensure_ascii=False),
            json.dumps(job.get("request", {}), ensure_ascii=False),
            job.get("heartbeat_at"),
            int(job.get("resume_count", 0) or 0),
            job.get("worker_pid"),
            (job.get("progress") or {}).get("last_completed_date"),
            str(job.get("owner_user_id") or ""),
            str(job.get("request_hash") or ""),
        ))
        if cursor.rowcount != 1:
            conn.rollback()
            logger.error("scrape job binding mismatch for %s", job_id)
            return False
        conn.commit()
        return True
    except Exception as exc:
        logger.error("scrape job persistence failed for %s: %s", job_id, exc)
        return False
    finally:
        if conn is not None:
            conn.close()


def _persist_job_or_raise(job_id: str, job: dict) -> None:
    """Persist a lifecycle transition or fail closed for status readers."""
    if not _persist_job(job_id, job):
        with _JOBS_LOCK:
            current = _scrape_jobs.get(job_id)
            if current is not None:
                current["_store_unavailable"] = True
        raise JobStoreUnavailable(f"scrape job state could not be persisted: {job_id}")

    with _JOBS_LOCK:
        current = _scrape_jobs.get(job_id)
        if current is not None:
            current.pop("_store_unavailable", None)


def create_job_if_owner_idle(job_id: str, job: dict) -> Literal["created", "active", "unavailable"]:
    """Atomically reject an active owner job or durably create a queued job."""
    owner_user_id = str(job.get("owner_user_id") or "")
    request_hash = str(job.get("request_hash") or "")
    if not owner_user_id or not request_hash:
        return "unavailable"

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(_JOBS_DB_PATH), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            """
            SELECT 1
            FROM scrape_jobs
            WHERE owner_user_id = ? AND status IN ('queued', 'running')
            LIMIT 1
            """,
            (owner_user_id,),
        ).fetchone()
        if active is not None:
            conn.rollback()
            return "active"
        conn.execute(
            """
            INSERT INTO scrape_jobs (
                job_id, status, progress, result, error,
                request_json, heartbeat_at, resume_count, worker_pid, last_checkpoint,
                owner_user_id, request_hash, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                job_id,
                job.get("status", "queued"),
                json.dumps(job.get("progress", {}), ensure_ascii=False),
                json.dumps(job.get("result"), ensure_ascii=False),
                json.dumps(job.get("error"), ensure_ascii=False),
                json.dumps(job.get("request", {}), ensure_ascii=False),
                job.get("heartbeat_at"),
                int(job.get("resume_count", 0) or 0),
                job.get("worker_pid"),
                (job.get("progress") or {}).get("last_completed_date"),
                owner_user_id,
                request_hash,
            ),
        )
        conn.commit()
        return "created"
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        logger.error("atomic scrape job creation failed for %s: %s", job_id, exc)
        return "unavailable"
    finally:
        if conn is not None:
            conn.close()


def _load_job_from_db(job_id: str) -> dict | None:
    """SQLite からジョブ状態を復元する"""
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(_JOBS_DB_PATH))
        row = conn.execute(
            """
            SELECT status, progress, result, error, request_json,
                   heartbeat_at, resume_count, worker_pid, owner_user_id, request_hash
            FROM scrape_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row:
            loaded = {
                "status": row[0],
                "progress": json.loads(row[1] or "{}"),
                "result": json.loads(row[2] or "null"),
                "error": json.loads(row[3] or "null"),
                "owner_user_id": str(row[8] or ""),
                "request_hash": str(row[9] or ""),
            }
            request_payload = json.loads(row[4] or "{}")
            if request_payload:
                loaded["request"] = request_payload
            if row[5] is not None:
                loaded["heartbeat_at"] = row[5]
            if int(row[6] or 0):
                loaded["resume_count"] = int(row[6])
            if row[7] is not None:
                loaded["worker_pid"] = row[7]
            return loaded
    except Exception as exc:
        logger.error("scrape job load failed for %s: %s", job_id, exc)
        raise JobStoreUnavailable(f"scrape job state could not be loaded: {job_id}") from exc
    finally:
        if conn is not None:
            conn.close()
    return None


def mark_interrupted_scrape_jobs(db_path: Path = _JOBS_DB_PATH) -> int:
    """Fail closed for jobs whose worker disappeared during a backend restart."""
    message = "Backend restarted before the scrape job completed. Start a new job to resume safely."
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            """
            UPDATE scrape_jobs
            SET status = 'error', error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('queued', 'running')
            """,
            (json.dumps(message),),
        )
        conn.commit()
        changed = int(cursor.rowcount or 0)
        conn.close()
        return changed
    except Exception as exc:
        logger.warning(f"中断スクレイピングジョブの復旧処理に失敗: {exc}")
        return 0


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
    start_date: str,
    end_date: str,
    heartbeat: Callable[[], None] | None = None,
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
        if heartbeat is not None:
            heartbeat()
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
_CANCEL_FLAGS: dict[str, bool] = {}
_MAX_JOBS = 50
# スレッドセーフな _scrape_jobs アクセスのためのロック
# （FastAPI メインスレッドと scrape バックグラウンドスレッドが同時にアクセスするため）
# RLock（再入可能ロック）を使用: scrape_start が _JOBS_LOCK を保持したまま
# _purge_old_jobs を呼び出すとデッドロックする問題を防ぐ
_JOBS_LOCK = threading.RLock()
_WORKERS_LOCK = threading.RLock()
_ACTIVE_WORKERS: set[str] = set()
_SUPERVISOR_STOP = threading.Event()
_SUPERVISOR_THREAD: threading.Thread | None = None


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def get_scrape_resource_snapshot() -> dict:
    """Return low-cost process/system metrics used by the long-running guard."""
    snapshot = {
        "process_rss_mb": None,
        "system_available_mb": None,
        "process_rss_limit_mb": _env_int("SCRAPE_MAX_PROCESS_RSS_MB", 8192),
        "system_available_min_mb": _env_int("SCRAPE_MIN_AVAILABLE_MB", 1024),
    }
    if psutil is None:
        snapshot["guard_available"] = False
        return snapshot
    try:
        snapshot["process_rss_mb"] = round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024, 1)
        snapshot["system_available_mb"] = round(psutil.virtual_memory().available / 1024 / 1024, 1)
        snapshot["guard_available"] = True
    except Exception as exc:  # pragma: no cover - platform failure
        snapshot["guard_available"] = False
        snapshot["guard_error"] = str(exc)
    return snapshot


def _resource_capacity_available(snapshot: dict | None = None) -> bool:
    current = snapshot or get_scrape_resource_snapshot()
    if not current.get("guard_available"):
        return True
    rss = current.get("process_rss_mb")
    available = current.get("system_available_mb")
    rss_limit = current.get("process_rss_limit_mb")
    available_min = current.get("system_available_min_mb")
    return not (
        (rss_limit and rss is not None and rss >= rss_limit)
        or (available_min and available is not None and available <= available_min)
    )


def _checkpoint_job(job_id: str, job: dict, *, force: bool = False) -> None:
    """Persist a heartbeat without writing SQLite on every small progress mutation."""
    now = time.monotonic()
    last = float(job.get("_last_checkpoint_monotonic", 0.0) or 0.0)
    if not force and now - last < _env_int("SCRAPE_HEARTBEAT_INTERVAL_SEC", 10):
        return
    job["heartbeat_at"] = _utc_now()
    job["worker_pid"] = os.getpid()
    job["resource"] = get_scrape_resource_snapshot()
    job["_last_checkpoint_monotonic"] = now
    _persist_job(job_id, job)


async def _wait_for_resource_capacity(job_id: str, job: dict) -> None:
    """Pause safely under memory pressure and continue when capacity returns."""
    wait_started = time.monotonic()
    max_wait = _env_int("SCRAPE_RESOURCE_MAX_WAIT_SEC", 0)
    poll_sec = max(5, _env_int("SCRAPE_RESOURCE_POLL_SEC", 30))
    while True:
        snapshot = get_scrape_resource_snapshot()
        if _resource_capacity_available(snapshot):
            if job.get("status") == "waiting_resources":
                job["status"] = "running"
                job["error"] = None
                _checkpoint_job(job_id, job, force=True)
            return
        job["status"] = "waiting_resources"
        job["error"] = None
        progress = job.setdefault("progress", {})
        progress["message"] = (
            "Resource guard paused scraping; it will resume automatically "
            f"(rss={snapshot.get('process_rss_mb')} MB, "
            f"available={snapshot.get('system_available_mb')} MB)."
        )
        _checkpoint_job(job_id, job, force=True)
        if max_wait and time.monotonic() - wait_started >= max_wait:
            raise RuntimeError("Resource pressure did not recover before SCRAPE_RESOURCE_MAX_WAIT_SEC")
        await asyncio.sleep(poll_sec)


def start_scrape_job_worker(job_id: str, request: dict | None = None) -> bool:
    """Start exactly one daemon worker for a durable scrape job."""
    job = _scrape_jobs.get(job_id) or _load_job_from_db(job_id)
    if not job:
        return False
    request_data = dict(request or job.get("request") or {})
    if not request_data.get("start_date") or not request_data.get("end_date"):
        job["status"] = "error"
        job["error"] = "This legacy job has no persisted date range and cannot be resumed safely."
        _scrape_jobs[job_id] = job
        _checkpoint_job(job_id, job, force=True)
        return False

    with _WORKERS_LOCK:
        if job_id in _ACTIVE_WORKERS:
            return False
        _ACTIVE_WORKERS.add(job_id)

    job["request"] = request_data
    _scrape_jobs[job_id] = job

    def _worker() -> None:
        if os.name == "nt":
            loop = asyncio.SelectorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                _run_scrape_job(
                    job_id,
                    str(request_data["start_date"]),
                    str(request_data["end_date"]),
                    bool(request_data.get("force_rescrape", False)),
                    bool(request_data.get("dry_run", False)),
                )
            )
        finally:
            loop.close()
            with _WORKERS_LOCK:
                _ACTIVE_WORKERS.discard(job_id)

    threading.Thread(target=_worker, daemon=True, name=f"scrape-{job_id}").start()
    return True


def resume_interrupted_scrape_jobs() -> int:
    """Recover durable queued/running jobs after a FastAPI restart."""
    _init_jobs_db()
    statuses = ("queued", "running", "recovering", "waiting_resources", "paused_resource")
    placeholders = ",".join("?" for _ in statuses)
    try:
        with sqlite3.connect(str(_JOBS_DB_PATH), timeout=30) as conn:
            rows = conn.execute(
                f"SELECT job_id FROM scrape_jobs WHERE status IN ({placeholders}) ORDER BY created_at",
                statuses,
            ).fetchall()
    except Exception as exc:
        logger.warning(f"Failed to inspect recoverable scrape jobs: {exc}")
        return 0

    resumed = 0
    for (job_id,) in rows:
        with _WORKERS_LOCK:
            if str(job_id) in _ACTIVE_WORKERS:
                continue
        job = _load_job_from_db(str(job_id))
        request_data = (job or {}).get("request") or {}
        if not job or not request_data.get("start_date") or not request_data.get("end_date"):
            if job:
                job["status"] = "error"
                job["error"] = "Backend restarted, but this legacy job has no persisted request to resume."
                _scrape_jobs[str(job_id)] = job
                _checkpoint_job(str(job_id), job, force=True)
            continue
        job["status"] = "recovering"
        job["error"] = None
        job["resume_count"] = int(job.get("resume_count", 0) or 0) + 1
        job["heartbeat_at"] = _utc_now()
        _scrape_jobs[str(job_id)] = job
        _persist_job(str(job_id), job)
        if start_scrape_job_worker(str(job_id), request_data):
            resumed += 1
    return resumed


def get_scrape_runtime_health() -> dict:
    """Expose enough evidence to tell whether long-running work is alive or stale."""
    stale_after = _env_int("SCRAPE_STALE_HEARTBEAT_SEC", 180)
    now = datetime.now(timezone.utc)
    stale_jobs: list[str] = []
    recoverable_jobs = 0
    try:
        with sqlite3.connect(str(_JOBS_DB_PATH), timeout=10) as conn:
            rows = conn.execute(
                """SELECT job_id, status, heartbeat_at FROM scrape_jobs
                   WHERE status IN ('queued','running','recovering','waiting_resources','paused_resource')"""
            ).fetchall()
        recoverable_jobs = len(rows)
        for job_id, status, heartbeat in rows:
            if status == "queued" and not heartbeat:
                continue
            try:
                observed = datetime.fromisoformat(str(heartbeat).replace("Z", "+00:00"))
                if (now - observed).total_seconds() > stale_after:
                    stale_jobs.append(str(job_id))
            except (TypeError, ValueError):
                stale_jobs.append(str(job_id))
    except Exception as exc:
        return {"database_ok": False, "database_error": str(exc)}

    with _WORKERS_LOCK:
        active_workers = sorted(_ACTIVE_WORKERS)
    resource = get_scrape_resource_snapshot()
    return {
        "database_ok": True,
        "auto_resume_enabled": True,
        "active_workers": active_workers,
        "active_worker_count": len(active_workers),
        "recoverable_job_count": recoverable_jobs,
        "stale_job_ids": stale_jobs,
        "stale_after_sec": stale_after,
        "resource_capacity_available": _resource_capacity_available(resource),
        "resource": resource,
    }


def start_scrape_supervisor() -> int:
    """Resume interrupted jobs now and keep a lightweight recovery supervisor alive."""
    global _SUPERVISOR_THREAD
    resumed = resume_interrupted_scrape_jobs()
    with _WORKERS_LOCK:
        if _SUPERVISOR_THREAD and _SUPERVISOR_THREAD.is_alive():
            return resumed
        _SUPERVISOR_STOP.clear()

        def _supervise() -> None:
            interval = max(15, _env_int("SCRAPE_SUPERVISOR_INTERVAL_SEC", 60))
            while not _SUPERVISOR_STOP.wait(interval):
                resume_interrupted_scrape_jobs()

        _SUPERVISOR_THREAD = threading.Thread(
            target=_supervise, daemon=True, name="scrape-supervisor"
        )
        _SUPERVISOR_THREAD.start()
    return resumed


def stop_scrape_supervisor() -> None:
    global _SUPERVISOR_THREAD
    _SUPERVISOR_STOP.set()
    thread = _SUPERVISOR_THREAD
    if thread and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=2)
    _SUPERVISOR_THREAD = None


def _purge_old_jobs(store: dict, max_keep: int = _MAX_JOBS) -> None:
    """completed/error ジョブを古い順に削除してメモリリークを防ぐ"""
    with _JOBS_LOCK:
        if len(store) <= max_keep:
            return
        finished = [k for k, v in store.items() if v.get("status") in ("completed", "error")]
        for key in finished[: len(store) - max_keep]:
            del store[key]


def get_job(job_id: str, *, owner_user_id: str) -> dict | None:
    """Return an owner-bound job from memory or SQLite."""
    if not owner_user_id:
        return None
    with _JOBS_LOCK:
        job = _scrape_jobs.get(job_id)
        if job is not None and job.get("_store_unavailable") is True:
            raise JobStoreUnavailable(f"scrape job state is not durable: {job_id}")
    if job is None:
        job = _load_job_from_db(job_id)
    if not job or str(job.get("owner_user_id") or "") != owner_user_id:
        return None
    return job


def has_active_job(owner_user_id: str) -> bool | None:
    """Return active-job presence, or None when durable state cannot be checked."""
    if not owner_user_id:
        return None
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(_JOBS_DB_PATH))
        row = conn.execute(
            """
            SELECT 1
            FROM scrape_jobs
            WHERE owner_user_id = ? AND status IN ('queued', 'running')
            LIMIT 1
            """,
            (owner_user_id,),
        ).fetchone()
        return row is not None
    except Exception as exc:
        logger.error("active scrape job check failed for owner %s: %s", owner_user_id, exc)
        return None
    finally:
        if conn is not None:
            conn.close()


def list_recent_jobs(*, owner_user_id: str, limit: int = 20) -> list[dict]:
    """SQLite から最近のジョブ履歴を取得する（fetch_summary 抜粋付き）。"""
    if not owner_user_id:
        return []
    safe_limit = max(1, min(int(limit), 100))
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(_JOBS_DB_PATH))
        rows = conn.execute(
            """
            SELECT job_id, status, progress, result, error, created_at, updated_at,
                   request_json, heartbeat_at, resume_count, worker_pid
            FROM scrape_jobs
            WHERE owner_user_id = ?
            ORDER BY datetime(updated_at) DESC
            LIMIT ?
            """,
            (owner_user_id, safe_limit),
        ).fetchall()
    except Exception as exc:
        logger.error("scrape job history load failed for owner %s: %s", owner_user_id, exc)
        raise JobStoreUnavailable("scrape job history could not be loaded") from exc
    finally:
        if conn is not None:
            conn.close()

    history: list[dict] = []
    for row in rows:
        try:
            job_id = str(row[0])
            status = str(row[1] or "unknown")
            progress = json.loads(row[2] or "{}")
            result = json.loads(row[3] or "null")
            error = json.loads(row[4] or "null")
            created_at = row[5]
            updated_at = row[6]
            request_data = json.loads(row[7] or "{}")
            fetch_summary = (result or {}).get("fetch_summary") if isinstance(result, dict) else None
            history.append(
                {
                    "job_id": job_id,
                    "status": status,
                    "progress": progress,
                    "result": result,
                    "error": error,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "fetch_summary": fetch_summary,
                    "request": request_data,
                    "heartbeat_at": row[8],
                    "resume_count": int(row[9] or 0),
                    "worker_pid": row[10],
                }
            )
        except Exception as exc:
            logger.error("malformed scrape job history row for owner %s: %s", owner_user_id, exc)
            raise JobStoreUnavailable("scrape job history contains invalid durable state") from exc
    return history


def _parse_yyyymmdd(value: object) -> str | None:
    txt = str(value or "").strip()
    if len(txt) == 8 and txt.isdigit():
        return txt
    if len(txt) >= 10 and txt[4] == "-" and txt[7] == "-":
        ymd = txt[:10].replace("-", "")
        if len(ymd) == 8 and ymd.isdigit():
            return ymd
    if len(txt) >= 10 and txt[4] == "/" and txt[7] == "/":
        ymd = txt[:10].replace("/", "")
        if len(ymd) == 8 and ymd.isdigit():
            return ymd
    return None


def _iter_chunks(values: list[str], chunk_size: int = 500):
    for i in range(0, len(values), chunk_size):
        yield values[i:i + chunk_size]


def _estimate_db_existing_coverage(db_path: Path, dates: list[str], min_races: int = 6) -> dict[str, int]:
    if not db_path.exists() or not dates:
        return {
            "db_existing_skip_count": 0,
            "db_existing_race_count": 0,
            "db_existing_horse_count": 0,
            "db_existing_result_count": 0,
            "db_existing_pedigree_count": 0,
        }

    date_set = set(dates)
    db_existing_dates = _get_scraped_dates_sqlite(db_path, min_races=min_races)
    target_existing_dates = sorted(date_set & db_existing_dates)
    db_existing_skip_count = len(target_existing_dates) * 2

    out = {
        "db_existing_skip_count": int(db_existing_skip_count),
        "db_existing_race_count": 0,
        "db_existing_horse_count": 0,
        "db_existing_result_count": 0,
        "db_existing_pedigree_count": 0,
    }

    if not target_existing_dates:
        return out

    race_ids: set[str] = set()
    horse_ids: set[str] = set()

    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT race_id, data FROM races_ultimate").fetchall()
        for race_id, data_txt in rows:
            try:
                payload = json.loads(data_txt or "{}")
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                continue
            race_date = (
                _parse_yyyymmdd(payload.get("race_date"))
                or _parse_yyyymmdd(payload.get("date"))
                or _parse_yyyymmdd(payload.get("kaisai_date"))
            )
            if race_date and race_date in date_set:
                race_ids.add(str(race_id))

        if race_ids:
            race_ids_list = sorted(race_ids)
            out["db_existing_race_count"] = len(race_ids_list)

            result_race_count = 0
            horse_count = 0
            for chunk in _iter_chunks(race_ids_list):
                placeholders = ",".join("?" for _ in chunk)
                row = conn.execute(
                    f"SELECT COUNT(DISTINCT race_id), COUNT(*) FROM race_results_ultimate WHERE race_id IN ({placeholders})",
                    chunk,
                ).fetchone()
                if row:
                    result_race_count += int(row[0] or 0)
                    horse_count += int(row[1] or 0)

                result_rows = conn.execute(
                    f"SELECT data FROM race_results_ultimate WHERE race_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                for (data_txt,) in result_rows:
                    try:
                        payload = json.loads(data_txt or "{}")
                    except Exception:
                        payload = {}
                    if isinstance(payload, dict):
                        horse_id = str(payload.get("horse_id") or "").strip()
                        if horse_id:
                            horse_ids.add(horse_id)

            out["db_existing_result_count"] = int(result_race_count)
            out["db_existing_horse_count"] = int(horse_count)
    except Exception:
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not horse_ids or not _PEDIGREE_DB_PATH.exists():
        return out

    try:
        ped_conn = sqlite3.connect(str(_PEDIGREE_DB_PATH))
        pedigree_count = 0
        horse_id_list = sorted(horse_ids)
        for chunk in _iter_chunks(horse_id_list):
            placeholders = ",".join("?" for _ in chunk)
            row = ped_conn.execute(
                f"SELECT COUNT(*) FROM pedigree_cache WHERE horse_id IN ({placeholders})",
                chunk,
            ).fetchone()
            if row:
                pedigree_count += int(row[0] or 0)
        out["db_existing_pedigree_count"] = int(pedigree_count)
    except Exception:
        pass
    finally:
        try:
            ped_conn.close()
        except Exception:
            pass

    return out


# ============================================================
# バックグラウンドスクレイピングジョブ
# ============================================================

async def _run_scrape_job(
    job_id: str,
    start_date: str,
    end_date: str,
    force_rescrape: bool = False,
    dry_run: bool = False,
):
    """バックグラウンドでスクレイピングを実行しジョブストアを更新する"""
    try:
        import time as _time
        job = _scrape_jobs[job_id]
        job["status"] = "running"
        job["error"] = None
        job["worker_pid"] = os.getpid()
        job["heartbeat_at"] = _utc_now()
        job["resource"] = get_scrape_resource_snapshot()
        await asyncio.to_thread(_persist_job_or_raise, job_id, job)

        ULTIMATE_DB = Path(__file__).parent.parent.parent / "keiba" / "data" / "keiba_ultimate.db"
        await asyncio.to_thread(_init_sqlite_db, ULTIMATE_DB)
        await asyncio.to_thread(init_acquisition_quality_db, ULTIMATE_DB)

        start_time = _time.time()

        dates = await asyncio.to_thread(build_bounded_scrape_dates, start_date, end_date)

        _MIN_RACES_PER_DAY = 6

        # ── ① 前処理A: カレンダーから実際の開催日のみに絞り込み（歴史データ高速化）──
        # 30日以上前のデータが含まれる場合はカレンダーAPIで開催日を事前取得し
        # 開催なし日のリクエストをゼロにする（最大70%以上の削減効果）
        from datetime import date as _date_cls
        _oldest = min(dates)
        _oldest_days_ago = (_date_cls.today() - _date_cls(int(_oldest[:4]), int(_oldest[4:6]), int(_oldest[6:8]))).days
        calendar_filter_applied = False
        # A dry-run is a zero-HTTP planning operation. Calendar HTTP filtering
        # belongs to execution and must not make the preview mutate cache/state.
        if _oldest_days_ago > 30 and not force_rescrape and not dry_run:
            job["progress"] = {"done": 0, "total": len(dates), "message": "カレンダー取得中..."}
            logger.info(f"カレンダー取得開始: {start_date}〜{end_date} ({len(dates)}日 → 開催日のみに絞り込み)")
            _calendar_dates = await _build_race_dates_from_calendar(
                start_date,
                end_date,
                heartbeat=lambda: _checkpoint_job(job_id, job, force=True),
            )
            if _calendar_dates is not None:
                calendar_filter_applied = True
                _original_count = len(dates)
                dates = sorted(set(dates) & set(_calendar_dates))
                logger.info(f"カレンダー絞り込み完了: {_original_count}日 → {len(dates)}日（開催日のみ）")
            else:
                logger.warning("カレンダー取得失敗 → 全日付で処理（フォールバック）")

        total = len(dates)
        previous_progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
        completed_dates = set(previous_progress.get("completed_dates") or [])
        job["progress"] = {
            "done": sum(1 for date in dates if date in completed_dates),
            "total": total,
            "message": f"0/{total}日処理済み",
            "completed_dates": sorted(completed_dates),
            "saved_races": int(previous_progress.get("saved_races", 0) or 0),
            "saved_horses": int(previous_progress.get("saved_horses", 0) or 0),
        }
        if not dry_run:
            _checkpoint_job(job_id, job, force=True)

        if dry_run:
            dry_urls: list[str] = []
            dry_resume_keys: list[str] = []
            _rate_limit_policy = {
                "min_interval_sec": 1.0,
                "scope": "per-host",
                "note": "INV-07 compliant; no high-concurrency acceleration",
            }
            _retry_policy = {
                "max_retries": 3,
                "retry_statuses": [429, 500, 503],
                "backoff": {
                    "type": "exponential_with_jitter",
                    "base_sec": 2.0,
                    "jitter_sec": 0.6,
                },
                "retry_after": "respected",
            }
            _circuit_breaker_policy = {
                "failure_threshold": 3,
                "cooldown_sec": 120.0,
                "scope": "per-host",
            }
            for d in dates:
                dry_urls.append(f"https://db.netkeiba.com/race/list/{d}/")
                dry_urls.append(f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={d}")
                dry_resume_keys.append(f"job:{job_id}:date:{d}:list")
                dry_resume_keys.append(f"job:{job_id}:date:{d}:sub")

            if len(dry_urls) > MAX_SCRAPE_TARGETS or len(dry_resume_keys) > MAX_SCRAPE_TARGETS:
                raise ValueError("scrape dry-run target limit exceeded")

            plan = await asyncio.to_thread(
                estimate_fetch_plan, dry_urls, resume_keys=dry_resume_keys
            )
            if force_rescrape:
                db_existing = {
                    "db_existing_skip_count": 0,
                    "db_existing_race_count": 0,
                    "db_existing_horse_count": 0,
                    "db_existing_result_count": 0,
                    "db_existing_pedigree_count": 0,
                }
            else:
                db_existing = await asyncio.to_thread(
                    _estimate_db_existing_coverage,
                    ULTIMATE_DB,
                    dates,
                    _MIN_RACES_PER_DAY,
                )
            cache_hits = int(plan.get("cache_hits", 0))
            resume_hits = int(plan.get("resume_hits", 0))
            unique_urls = int(plan.get("unique_urls", 0))
            total_target_count = int(plan.get("total_input_urls", 0))
            db_existing_skip_count = int(db_existing.get("db_existing_skip_count", 0))
            legacy_skipped_count = cache_hits + resume_hits
            already_covered_count = legacy_skipped_count + db_existing_skip_count
            new_fetch_required_count = max(0, total_target_count - already_covered_count)
            estimated_requests = int(new_fetch_required_count)
            cache_miss = max(0, unique_urls - cache_hits)
            skipped_count = legacy_skipped_count
            estimated_runtime_sec = float(max(0, estimated_requests) * _rate_limit_policy["min_interval_sec"])
            summary = {
                "job_id": job_id,
                "mode": "dry-run",
                "start_date": start_date,
                "end_date": end_date,
                "total_dates": total,
                "force_rescrape": bool(force_rescrape),
                "calendar_filter_applied": calendar_filter_applied,
                "dry_run": {
                    "total_target_count": total_target_count,
                    "unique_url_count": unique_urls,
                    "estimated_request_count": estimated_requests,
                    "cache_hit_count": cache_hits,
                    "cache_miss_count": cache_miss,
                    "resume_hit_count": resume_hits,
                    "skipped_count": skipped_count,
                    "db_existing_skip_count": db_existing_skip_count,
                    "db_existing_race_count": int(db_existing.get("db_existing_race_count", 0)),
                    "db_existing_horse_count": int(db_existing.get("db_existing_horse_count", 0)),
                    "db_existing_result_count": int(db_existing.get("db_existing_result_count", 0)),
                    "db_existing_pedigree_count": int(db_existing.get("db_existing_pedigree_count", 0)),
                    "new_fetch_required_count": int(new_fetch_required_count),
                    "already_covered_count": int(already_covered_count),
                    "estimated_runtime_sec": estimated_runtime_sec,
                },
                "rate_limit_policy": _rate_limit_policy,
                "retry_backoff_policy": _retry_policy,
                "circuit_breaker_policy": _circuit_breaker_policy,
                "plan": plan,
            }
            report_path = await asyncio.to_thread(write_fetch_summary, summary)

            with _JOBS_LOCK:
                job["status"] = "completed"
                job["result"] = {
                    "success": True,
                    "dry_run": True,
                    "message": "dry-run completed (no HTTP access)",
                    "fetch_summary": summary,
                    "fetch_summary_path": str(report_path),
                }
            await asyncio.to_thread(_persist_job_or_raise, job_id, job)
            return

        # ── ② 前処理B: 取得済み日付を SQLite から読み込み（レジューム）──
        scraped_dates: set = set()
        excluded_dates: set[str] = set()
        if not force_rescrape:
            try:
                _local_scraped = await asyncio.to_thread(
                    completed_quality_dates, ULTIMATE_DB
                )
                scraped_dates.update(_local_scraped)
                if _local_scraped:
                    logger.info(f"SQLite取得済み日付: {len(_local_scraped)}日分をスキップ")
                    job["progress"]["message"] = f"{len(_local_scraped)}日分は取得済み、スキップします"
            except Exception as _e:
                logger.warning(f"SQLite取得済み確認失敗: {_e}")

            excluded_dates = await asyncio.to_thread(excluded_standard_dates, ULTIMATE_DB)

        timeout = aiohttp.ClientTimeout(total=25, connect=8)
        connector = aiohttp.TCPConnector(limit=5, limit_per_host=3)
        counter = {
            "races": int(job["progress"].get("saved_races", 0) or 0),
            "horses": int(job["progress"].get("saved_horses", 0) or 0),
        }
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
                await _wait_for_resource_capacity(job_id, job)
                list_url = f"https://db.netkeiba.com/race/list/{date}/"
                errors: list = []

                if date in completed_dates:
                    job["progress"].update({
                        "done": i + 1,
                        "total": total,
                        "message": f"{i+1}/{total} dates processed (resumed checkpoint)",
                    })
                    _checkpoint_job(job_id, job, force=True)
                    continue

                if date in excluded_dates:
                    failed_dates = set(job["progress"].get("failed_dates") or [])
                    failed_dates.add(date)
                    job["progress"].update({
                        "done": i + 1,
                        "total": total,
                        "message": f"{i+1}/{total} dates processed ({date} quarantined)",
                        "failed_dates": sorted(failed_dates),
                    })
                    _checkpoint_job(job_id, job, force=True)
                    continue

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
                        "completed_dates": sorted(completed_dates | {date}),
                        "last_completed_date": date,
                    }
                    completed_dates.add(date)
                    _checkpoint_job(job_id, job, force=True)
                    continue

                _day_races_before = counter["races"]  # この日の保存開始前レース数を記録
                race_ids: list[str] = []  # available after the per-date try/except
                expected_race_ids: list[str] = []
                list_failure_reason: str | None = None
                list_source_status = "not_requested"
                try:
                    await asyncio.sleep(_pre_sleep)  # レース一覧リクエスト間のインターバル

                    # Resolve the authoritative date list through the shared
                    # fail-closed path.  It tries the desktop endpoints first,
                    # then scans every page of the JRA-only mobile month index;
                    # therefore an HTTP 400 can no longer be mistaken for an
                    # empty/non-racing date.
                    try:
                        race_ids, race_list_source = await fetch_race_ids(date)
                        list_source_status = f"ok:{race_list_source}"
                        if not race_ids:
                            list_failure_reason = (
                                "race_list_verified_empty"
                                if race_list_source.endswith(":verified-empty")
                                else "race_list_empty"
                            )
                    except Exception as _list_error:
                        race_ids = []
                        list_source_status = "exception"
                        list_failure_reason = "race_list_unavailable"
                        logger.warning(f"{date}: authoritative race-list failed: {_list_error}")

                    logger.info(f"{date}: {len(race_ids)}レースID検出")
                    # Freeze the authoritative race list before exclusions.
                    # Quarantined races remain visible to the date audit, so a
                    # partial day can never become a silent success.
                    if not race_ids:
                        await asyncio.to_thread(
                            record_date_failure,
                            ULTIMATE_DB,
                            race_date=date,
                            reason=list_failure_reason or "race_list_empty",
                            source_status=list_source_status,
                        )
                        errors.append(f"{date}: {list_failure_reason or 'race_list_empty'}")

                    expected_race_ids = list(race_ids)
                    await asyncio.to_thread(
                        record_date_expectation, ULTIMATE_DB, date, expected_race_ids
                    )
                    existing_audit = await asyncio.to_thread(
                        hydrate_quality_from_existing_data,
                        ULTIMATE_DB,
                        date,
                        expected_race_ids,
                    )
                    missing_or_incomplete = set(existing_audit["missing_race_ids"])
                    race_ids = [rid for rid in expected_race_ids if rid in missing_or_incomplete]
                    if expected_race_ids and not race_ids:
                        logger.info(
                            f"{date}: all {len(expected_race_ids)} expected races already "
                            "pass settled quality; detail fetch skipped"
                        )
                    excluded_ids = await asyncio.to_thread(
                        excluded_standard_race_ids, ULTIMATE_DB
                    )
                    if excluded_ids:
                        race_ids = [rid for rid in race_ids if rid not in excluded_ids]
                        excluded_today = sorted(set(expected_race_ids) & excluded_ids)
                        if excluded_today:
                            logger.warning(
                                f"{date}: {len(excluded_today)} quarantined races excluded "
                                f"from standard scrape: {excluded_today[:5]}"
                            )

                    async def _fetch_and_save(race_id, _date=date, _day_idx=i):
                        try:
                            race_data = await scrape_race_full(
                                session,
                                race_id,
                                date_hint=_date,
                                quick_mode=True,
                                force_refresh=True,
                            )
                            if race_data and race_data.get("horses"):
                                quality = classify_race_quality(race_data)
                                if not quality.valid_for_storage:
                                    reason = "quality:" + ",".join(quality.required_errors)
                                    await asyncio.to_thread(
                                        record_race_failure,
                                        ULTIMATE_DB,
                                        race_date=_date,
                                        race_id=race_id,
                                        reason=reason,
                                        source_status="http_200",
                                    )
                                    errors.append(f"{race_id}: {reason}")
                                    logger.warning(f"quality gate rejected {race_id}: {reason}")
                                    del race_data
                                    return
                                n_horses = len(race_data["horses"])
                                saved = await asyncio.to_thread(
                                    _save_race_sqlite_only, race_data, ULTIMATE_DB
                                )
                                if saved:
                                    await asyncio.to_thread(
                                        record_race_quality,
                                        ULTIMATE_DB,
                                        race_date=_date,
                                        race_id=race_id,
                                        report=quality,
                                        source_status="http_200",
                                        race_data=race_data,
                                    )
                                    async with counter_lock:
                                        counter["races"] += 1
                                        counter["horses"] += n_horses
                                        job["progress"].update({
                                            "done": _day_idx,
                                            "total": total,
                                            "message": (
                                                f"{_day_idx}/{total}日処理中 | "
                                                f"{counter['races']}レース・{counter['horses']}頭保存済み"
                                            ),
                                            "saved_races": counter["races"],
                                            "saved_horses": counter["horses"],
                                        })
                                    logger.info(f"保存完了: {race_id} ({n_horses}頭)")
                                else:
                                    await asyncio.to_thread(
                                        record_race_failure,
                                        ULTIMATE_DB,
                                        race_date=_date,
                                        race_id=race_id,
                                        reason="sqlite_save_failed",
                                        source_status="http_200",
                                    )
                                    errors.append(f"{race_id}: sqlite_save_failed")
                                    logger.warning(f"SQLite保存失敗: {race_id}")
                                del race_data
                            else:
                                await asyncio.to_thread(
                                    record_race_failure,
                                    ULTIMATE_DB,
                                    race_date=_date,
                                    race_id=race_id,
                                    reason="parse_or_source_empty",
                                    source_status="source_unclassified",
                                )
                                errors.append(f"{race_id}: parse_or_source_empty")
                                logger.warning(f"レースデータなし/出走馬なし: {race_id}")
                        except Exception as exc:
                            err_msg = f"{race_id}: {exc}"
                            errors.append(err_msg)
                            await asyncio.to_thread(
                                record_race_failure,
                                ULTIMATE_DB,
                                race_date=_date,
                                race_id=race_id,
                                reason="exception:" + str(exc)[:300],
                                source_status="exception",
                            )
                            logger.error(f"_fetch_and_save 失敗 {err_msg}")

                    for ci in range(0, len(race_ids), 1):
                        await _wait_for_resource_capacity(job_id, job)
                        chunk = race_ids[ci : ci + 1]
                        await asyncio.gather(*[_fetch_and_save(r) for r in chunk])
                        _checkpoint_job(job_id, job)
                        if ci + 1 < len(race_ids):
                            await asyncio.sleep(_inter_race_sleep)  # レース間インターバル
                        await asyncio.to_thread(gc.collect)
                    if errors:
                        logger.warning(f"エラー一覧: {errors[:5]}")

                except Exception as e:
                    errors.append(f"{date}: {e}")
                    logger.error(f"ジョブ {job_id} {date} エラー: {e}")

                job["progress"].update({
                    "done": i + 1,
                    "total": total,
                    "message": f"{i+1}/{total}日処理済み / {counter['races']}レース保存 (errors:{len(errors)})",
                    "saved_races": counter["races"],
                    "saved_horses": counter["horses"],
                    "last_errors": errors[-3:] if errors else [],
                })
                # A date is complete only when every expected race has a
                # settled, structurally valid record.  Partial and quarantined
                # races remain in the repair ledger and are never hidden by a
                # fixed minimum-race threshold.
                try:
                    date_audit = await asyncio.to_thread(
                        run_date_repair_audit,
                        ULTIMATE_DB,
                        date,
                        expected_race_ids,
                    )
                    day_complete = date_audit["status"] == "complete"
                    if day_complete:
                        await asyncio.to_thread(
                            _save_scraped_date_sqlite,
                            ULTIMATE_DB,
                            date,
                            date_audit["expected_race_count"],
                        )
                        completed_dates.add(date)
                        job["progress"]["completed_dates"] = sorted(completed_dates)
                        job["progress"]["last_completed_date"] = date
                    else:
                        failed_dates = set(job["progress"].get("failed_dates") or [])
                        failed_dates.add(date)
                        job["progress"]["failed_dates"] = sorted(failed_dates)
                        job["progress"]["last_date_audit"] = date_audit
                except Exception as audit_exc:
                    errors.append(f"{date}: date audit failed: {audit_exc}")
                    logger.error(f"{date}: date completeness audit failed: {audit_exc}")
                # 進捗を SQLite に永続化（Render スピンダウン対策）
                await asyncio.to_thread(_persist_job_or_raise, job_id, job)
                # 日付間インターバル（最終日以外）
                if i < total - 1:
                    await asyncio.sleep(_post_sleep)

        saved_races = counter["races"]
        saved_horses = counter["horses"]
        elapsed = _time.time() - start_time
        quality_audit = await asyncio.to_thread(
            summarize_acquisition_quality, ULTIMATE_DB, dates
        )
        fetch_summary = {
            "job_id": job_id,
            "mode": "execute",
            "start_date": start_date,
            "end_date": end_date,
            "saved_races": saved_races,
            "saved_horses": saved_horses,
            "elapsed_time_sec": elapsed,
            "quality_audit": quality_audit,
            "metrics": get_fetch_metrics(reset=True),
            "rate_limit_policy": {
                "min_interval_sec": 1.0,
                "scope": "per-host",
            },
            "retry_backoff_policy": {
                "max_retries": 3,
                "retry_statuses": [429, 500, 503],
                "backoff": {"type": "exponential_with_jitter", "base_sec": 2.0, "jitter_sec": 0.6},
                "retry_after": "respected",
            },
            "circuit_breaker_policy": {
                "failure_threshold": 3,
                "cooldown_sec": 120.0,
                "scope": "per-host",
            },
        }
        report_path = await asyncio.to_thread(write_fetch_summary, fetch_summary)
        with _JOBS_LOCK:
            job["status"] = "completed"
            job["result"] = {
                "success": bool(quality_audit["quality_complete"]),
                "races_collected": saved_races,
                "saved_horses": saved_horses,
                "elapsed_time": elapsed,
                "message": (
                    f"{saved_races}レース・{saved_horses}頭のデータを収集しました"
                    if quality_audit["quality_complete"]
                    else (
                        f"収集は完了しましたが、{quality_audit['incomplete_date_count']}日を"
                        "修復キューへ登録しました"
                    )
                ),
                "fetch_summary": fetch_summary,
                "fetch_summary_path": str(report_path),
            }
        await asyncio.to_thread(_persist_job_or_raise, job_id, job)
    except Exception as e:
        logger.error(f"スクレイピングジョブ失敗 {job_id}: {e}")
        with _JOBS_LOCK:
            if job_id in _scrape_jobs:
                _scrape_jobs[job_id]["status"] = "error"
                _scrape_jobs[job_id]["error"] = str(e)
                _scrape_jobs[job_id]["result"] = None
        error_job = _scrape_jobs.get(job_id, {})
        if await asyncio.to_thread(_persist_job, job_id, error_job):
            with _JOBS_LOCK:
                current = _scrape_jobs.get(job_id)
                if current is not None:
                    current.pop("_store_unavailable", None)
        else:
            with _JOBS_LOCK:
                current = _scrape_jobs.get(job_id)
                if current is not None:
                    current["_store_unavailable"] = True

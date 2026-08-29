import json
import sqlite3
from pathlib import Path

from scraping import jobs
from scraping.jobs import mark_interrupted_scrape_jobs


def test_running_scrape_job_is_marked_interrupted_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "scrape_jobs.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE scrape_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                progress TEXT DEFAULT '{}',
                result TEXT DEFAULT 'null',
                error TEXT DEFAULT 'null',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO scrape_jobs (job_id, status) VALUES (?, ?)",
            ("scrape-1", "running"),
        )

    assert mark_interrupted_scrape_jobs(db_path) == 1
    with sqlite3.connect(str(db_path)) as conn:
        status, error = conn.execute(
            "SELECT status, error FROM scrape_jobs WHERE job_id = ?",
            ("scrape-1",),
        ).fetchone()

    assert status == "error"
    assert "restarted" in json.loads(error)


def test_durable_scrape_job_is_resumed_after_restart(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "scrape_jobs.db"
    monkeypatch.setattr(jobs, "_JOBS_DB_PATH", db_path)
    jobs._scrape_jobs.clear()
    jobs._ACTIVE_WORKERS.clear()
    jobs._init_jobs_db()

    durable_job = {
        "status": "running",
        "progress": {
            "done": 2,
            "total": 10,
            "completed_dates": ["20260101", "20260102"],
        },
        "result": None,
        "error": None,
        "request": {
            "start_date": "20260101",
            "end_date": "20260110",
            "force_rescrape": False,
            "dry_run": False,
        },
        "resume_count": 0,
    }
    jobs._persist_job("durable-1", durable_job)

    started: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        jobs,
        "start_scrape_job_worker",
        lambda job_id, request: started.append((job_id, request)) or True,
    )

    assert jobs.resume_interrupted_scrape_jobs() == 1
    assert started[0][0] == "durable-1"
    recovered = jobs._load_job_from_db("durable-1")
    assert recovered is not None
    assert recovered["status"] == "recovering"
    assert recovered["resume_count"] == 1
    assert recovered["progress"]["completed_dates"] == ["20260101", "20260102"]


def test_legacy_job_without_request_fails_closed(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "scrape_jobs.db"
    monkeypatch.setattr(jobs, "_JOBS_DB_PATH", db_path)
    jobs._scrape_jobs.clear()
    jobs._ACTIVE_WORKERS.clear()
    jobs._init_jobs_db()
    jobs._persist_job("legacy-1", {"status": "running", "progress": {}})

    assert jobs.resume_interrupted_scrape_jobs() == 0
    recovered = jobs._load_job_from_db("legacy-1")
    assert recovered is not None
    assert recovered["status"] == "error"
    assert "legacy job" in recovered["error"]


def test_resource_guard_fails_closed_below_available_memory_limit() -> None:
    assert not jobs._resource_capacity_available({
        "guard_available": True,
        "process_rss_mb": 100,
        "process_rss_limit_mb": 8192,
        "system_available_mb": 512,
        "system_available_min_mb": 1024,
    })
    assert jobs._resource_capacity_available({
        "guard_available": True,
        "process_rss_mb": 100,
        "process_rss_limit_mb": 8192,
        "system_available_mb": 4096,
        "system_available_min_mb": 1024,
    })

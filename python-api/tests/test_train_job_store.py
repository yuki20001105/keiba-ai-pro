from pathlib import Path

from training.job_store import load_train_job, mark_interrupted_train_jobs, persist_train_job


def test_train_job_round_trip_and_update(tmp_path: Path) -> None:
    db_path = tmp_path / "train_jobs.db"
    persist_train_job(
        "job-1",
        {"status": "running", "progress": "training", "pct": 35, "result": None, "error": None},
        {"target": "speed_deviation"},
        db_path,
    )
    persist_train_job(
        "job-1",
        {"status": "completed", "progress": "done", "pct": 100, "result": {"model_id": "m1"}, "error": None},
        db_path=db_path,
    )

    assert load_train_job("job-1", db_path) == {
        "status": "completed",
        "progress": "done",
        "pct": 100,
        "result": {"model_id": "m1"},
        "error": None,
    }


def test_running_job_is_marked_interrupted_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "train_jobs.db"
    persist_train_job(
        "job-2",
        {"status": "running", "progress": "training", "pct": 60, "result": None, "error": None},
        db_path=db_path,
    )

    assert mark_interrupted_train_jobs(db_path) == 1
    restored = load_train_job("job-2", db_path)
    assert restored is not None
    assert restored["status"] == "error"
    assert "restarted" in restored["progress"]

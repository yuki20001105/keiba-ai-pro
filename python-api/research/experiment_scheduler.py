from __future__ import annotations

from typing import Any

from .experiment_registry import ExperimentOpsStore
from .experiment_runner import run_experiment_spec


def run_next_experiment_job(
    *,
    store: ExperimentOpsStore | None = None,
    mlops_db_path: str,
    race_db_path: str,
    ultimate_db_path: str,
    worker: str = "local-worker",
) -> dict[str, Any]:
    ops = store or ExperimentOpsStore()
    job = ops.pop_next_job(worker=worker)
    if not job:
        return {"ran": False, "message": "queue is empty"}

    job_id = int(job.get("job_id") or 0)
    spec = job.get("spec") if isinstance(job.get("spec"), dict) else {}

    try:
        result = run_experiment_spec(
            spec,
            mlops_db_path=mlops_db_path,
            race_db_path=race_db_path,
            ultimate_db_path=ultimate_db_path,
        )
        ops.finish_job(job_id=job_id, status="completed", result=result, error="")
        return {
            "ran": True,
            "status": "completed",
            "job_id": job_id,
            "name": job.get("name"),
            "result": result,
        }
    except Exception as e:
        ops.finish_job(job_id=job_id, status="failed", result={}, error=str(e))
        return {
            "ran": True,
            "status": "failed",
            "job_id": job_id,
            "name": job.get("name"),
            "error": str(e),
        }

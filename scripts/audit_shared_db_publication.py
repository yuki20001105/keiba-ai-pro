"""Inspect one completed local publication; never train, activate or purchase.

Optional --predict performs ONE normal authenticated /api/analyze_race of a saved
race, retaining only counts, diagnostics and timings, not identity/session data.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.request import Request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))
sys.path.insert(0, str(ROOT / "keiba"))


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def audit(job_id: str, predict: bool = False) -> dict:
    uuid.UUID(job_id)
    os.environ["APP_ENV"] = "development"
    ledger = ROOT / "python-api/data/local_retrain_jobs.db"
    with sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT j.state,j.snapshot_path,j.snapshot_sha256,j.created_at,j.finished_at,"
            "p.published_path,p.artifact_sha256,j.request_json "
            "FROM local_retrain_jobs j JOIN local_retrain_publications p ON p.job_id=j.job_id "
            "WHERE j.job_id=? AND p.state='published'", (job_id,)).fetchone()
    if not row or row[0] != "completed":
        raise ValueError("completed-publication-required")
    state, snapshot, snapshot_sha, created, finished, model_path, model_sha, request = row
    from services.db_shared_input import validate_published_artifact, verify_bundle_sources
    if validate_published_artifact(Path(model_path)) != model_sha:
        raise ValueError("publication-digest-mismatch")
    import joblib
    bundle = joblib.load(model_path)
    verify_bundle_sources(bundle)
    from training.db_shared_fit import _validate_prediction_bundle
    _validate_prediction_bundle(bundle)
    if digest(snapshot) != snapshot_sha:
        raise ValueError("snapshot-digest-mismatch")
    pointer = ROOT / "python-api/models/.active_model.json"
    pointer_sha = digest(pointer)
    active = json.loads(pointer.read_text(encoding="utf-8"))["model_id"]
    active_sha = digest(pointer.parent / (active + ".joblib"))
    if (pointer_sha != "152e7fff8618a2ea367846bb9e3f64972ffa85f90a2fdc01fc2fb70fa976163e"
            or active_sha != "3c19d28b2018900d2531cbc825a0ea39bff7741f31b4c69f9da47507d4800b3e"):
        raise ValueError("active-model-preservation-check-failed")
    total = (datetime.fromisoformat(finished.replace("Z", "+00:00"))
             - datetime.fromisoformat(created.replace("Z", "+00:00"))).total_seconds()
    report = {"schema": "shared-db-publication-proof-v1", "job_id": job_id, "job_state": state,
        "request": json.loads(request), "model_id": Path(model_path).stem, "artifact_sha256": model_sha,
        "snapshot_sha256": snapshot_sha, "snapshot_path": snapshot,
        "publication_verified": True, "feature_count": len(bundle["feature_columns"]),
        "feature_columns": bundle["feature_columns"], "feature_source_hashes": bundle["feature_source_hashes"],
        "created_at": bundle["created_at"], "dataset_period_start": bundle["dataset_period_start"],
        "dataset_period_end": bundle["dataset_period_end"], "training_eligibility": bundle["training_eligibility"],
        "fit_audit": bundle["shared_training_audit"], "evaluation": bundle["evaluation"],
        "probability_validation": bundle["probability_validation"], "whole_job_seconds": total,
        "training_stage_seconds": bundle["training_stage_seconds"],
        "training_time_seconds": bundle["training_time_seconds"], "active_pointer_sha256": pointer_sha,
        "active_artifact_sha256": active_sha, "active_model_changed": False,
        "forward_collection_mutation_performed": False, "real_purchase_performed": False, "roi_evaluated": False}
    if predict:
        import smoke_local_authenticated_readonly as auth
        opener = auth.secure_opener()
        status, token = auth.authenticate(opener, auth.configuration(ROOT))
        if status != 200 or not token:
            raise ValueError("ordinary-admin-authentication-unavailable")
        with sqlite3.connect(Path(snapshot).as_uri() + "?mode=ro&immutable=1", uri=True) as db:
            candidates = db.execute("SELECT race_id,data FROM races_ultimate WHERE race_id LIKE '2026%' ORDER BY race_id").fetchall()
        race_id = next(rid for rid, raw in candidates
            if json.loads(raw).get("date") == "20261010" and json.loads(raw).get("venue") == "東京"
            and str(rid).endswith("01"))
        auth.TIMEOUT_SECONDS = 180
        payload = {"race_id": race_id, "model_id": Path(model_path).stem, "bankroll": 10000,
                   "risk_mode": "balanced", "include_explanation": False}
        clock = __import__("time").perf_counter()
        status, response = auth.request_json(opener, Request("http://127.0.0.1:8000/api/analyze_race",
            data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + token,
            "Content-Type": "application/json"}, method="POST"))
        del token
        if status != 200 or not isinstance(response, dict):
            raise ValueError("saved-race-prediction-failed-status-" + str(status))
        predictions = response.get("predictions", [])
        report["saved_race_prediction"] = {"status": status, "race_id": race_id,
            "model_id": response.get("model_id"), "runner_count": len(predictions),
            "elapsed_seconds": __import__("time").perf_counter() - clock,
            "input_diagnostics": response.get("input_diagnostics"),
            "processing_timings": response.get("processing_timings"),
            "reference_bets_present": bool(response.get("reference_bets")),
            "expected_values_visible": sum(row.get("expected_value") is not None for row in predictions),
            "recommendation_reason_codes": response.get("recommendation", {}).get("reason_codes", [])}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--predict", action="store_true")
    args = parser.parse_args()
    try:
        report = audit(args.job_id, args.predict)
    except Exception as error:
        print("AUDIT_FAILED", type(error).__name__)
        return 1
    output = ROOT / "reports/generated/shared-db-publication" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True, exist_ok=False)
    with (output / "publication.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"evidence": str(output / "publication.json"), "job_id": args.job_id,
        "evaluation": report["evaluation"]["primary"], "probability": report["probability_validation"],
        "whole_job_seconds": report["whole_job_seconds"], "timings": report["training_stage_seconds"],
        "prediction": report.get("saved_race_prediction")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

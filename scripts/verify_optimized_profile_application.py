"""Create one ordinary local 46-feature candidate, never switch the active model.

Opt-in training uses the existing authenticated UI proxy, durable lease and
publisher. Credentials stay in memory. A completed full-period research model
is never promoted or renamed. No scraping, purchase, settlement or restart.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import uuid
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_normal_training_acceleration as normal


def idle_inventory():
    receipt = normal.inventory()
    counts = receipt["job_inventory"].get("training", {}).get("state_counts", {}).get("local_retrain_jobs", {})
    if any(count and state not in {"completed", "failed", "cancelled"} for state, count in counts.items()):
        raise ValueError("local-training-not-idle")
    return receipt


def authenticated_scrape_health(opener, token):
    request = Request("http://127.0.0.1:8000/api/scrape/health", headers={"Authorization": "Bearer " + token})
    status, body = normal.auth.request_json(opener, request)
    if status != 200 or not isinstance(body, dict) or body.get("metrics", {}).get("active_jobs") != 0:
        raise ValueError("local-acquisition-not-confirmed-idle")
    return {"status": body.get("status"), "active_jobs": 0}


def run(create_candidate=False):
    before = idle_inventory()
    opener = normal.auth.secure_opener()
    normal.auth.TIMEOUT_SECONDS = 40
    status, token = normal.auth.authenticate(opener, normal.auth.configuration(ROOT))
    if status != 200 or not token:
        raise ValueError("ordinary-admin-authentication-unavailable")
    scrape = authenticated_scrape_health(opener, token)
    capability = normal.call(opener, token, "/api/ml/train/capability")
    if not create_candidate:
        print(json.dumps({"training_idle": True, "scrape": scrape,
            "training_enabled": capability.get("enabled") is True,
            "forward_unchanged": before["preservation"] == normal.inventory()["preservation"]}), flush=True)
        return 0
    if capability.get("enabled") is not True:
        raise ValueError("ordinary-local-training-not-enabled")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    output = ROOT / "reports/generated/optimized-profile-application" / stamp
    output.mkdir(parents=True, exist_ok=False)
    request = {"target": "speed_deviation", "model_type": "lightgbm",
        "feature_pipeline": "local-db-shared-optimized-v1", "force_sync": False,
        "use_sqlite": True, "ultimate_mode": True, "use_optimizer": True,
        "use_optuna": False, "test_size": .2, "cv_folds": 5,
        "training_date_from": "2016-01", "training_date_to": "2026-09",
        "compute_device": "auto", "reuse_features": True}
    normal.save(output / "protocol.json", {"request": request, "feature_count": 46,
        "excluded_features": ["safe_past5_top3_rate"], "no_activation": True,
        "roi_evaluated": False, "historical_diagnostics_not_independent_future_evidence": True})
    normal.save(output / "before.json", before)
    started = time.perf_counter()
    response = normal.call(opener, token, "/api/ml/train/start", request)
    job_id = str(uuid.UUID(response["job_id"]))
    normal.save(output / "job.json", {"job_id": job_id})
    print(json.dumps({"output": str(output), "job_id": job_id, "status": "started"}), flush=True)
    previous = None
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        state = normal.call(opener, token, f"/api/ml/train/status/{job_id}")
        progress = (state.get("status"), state.get("progress"))
        if progress != previous:
            print(json.dumps({"status": progress[0], "progress": progress[1]}, ensure_ascii=False), flush=True)
            previous = progress
        if state.get("status") == "completed":
            break
        if state.get("status") in {"failed", "error"}:
            normal.save(output / "failed.json", {"job_id": job_id, "status": state.get("status"),
                "error": state.get("error"), "preservation": normal.inventory()["preservation"]})
            raise ValueError("ordinary-optimized-training-failed")
        time.sleep(2)
    else:
        raise ValueError("ordinary-optimized-timeout-job-not-stopped")
    report = normal.publication(job_id, feature_pipeline=request["feature_pipeline"])
    after = normal.inventory()
    preserved = before["preservation"] == after["preservation"]
    if (report["feature_count"] != 46 or "safe_past5_top3_rate" in report["feature_columns"]
            or report["feature_pipeline"] != request["feature_pipeline"] or not preserved):
        raise ValueError("ordinary-optimized-publication-contract-or-protection-failed")
    models = normal.call(opener, token, "/api/models")
    selectable = any(model.get("model_id") == report["model_id"] or model.get("id") == report["model_id"]
                     for model in models.get("models", []))
    normal.save(output / "results.json", {"schema": "optimized-profile-application-v1",
        "candidate": report, "preserved": preserved, "selectable_in_models": selectable,
        "request_through_completion_seconds": time.perf_counter() - started,
        "roi_evaluated": False, "model_activated": False, "forward_changed": False})
    normal.save(output / "after.json", after)
    manifest = {path.name: normal.protection.file_hash(path) for path in output.iterdir() if path.is_file()}
    normal.save(output / "artifact_manifest.json", {"sha256": manifest})
    print(json.dumps({"status": "completed", "model_id": report["model_id"],
        "feature_count": 46, "selectable": selectable, "preserved": preserved,
        "evaluation": report["evaluation"]["primary"], "probability_validated": report["probability_validation"]["validated"],
        "whole_job_seconds": report["whole_job_seconds"], "output": str(output)}), flush=True)
    return 0 if selectable else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--create-candidate", action="store_true")
    args = parser.parse_args()
    try:
        raise SystemExit(run(args.create_candidate))
    except Exception as exc:
        # Never echo credential/provider response bodies or raw exceptions.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}), flush=True)
        raise SystemExit(1)

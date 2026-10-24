"""Opt-in full-period verification through the ordinary authenticated UI proxy.

Never activate a model, scrape, purchase, settle, install, or restart services.
Credentials remain in memory; outputs contain only job/model research receipts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
from urllib.request import Request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))
sys.path.insert(0, str(ROOT / "keiba"))
import evaluate_db_shared_venues as protection
import smoke_local_authenticated_readonly as auth


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def inventory():
    paths = {"training": ROOT / "python-api/data/local_retrain_jobs.db",
             "acquisition": Path.home() / "AppData/Local/Temp/keiba-ai-pro/operational-saga.sqlite3"}
    records = {}
    for label, path in paths.items():
        if not path.exists():
            records[label] = {"exists": False}
            continue
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            groups = {}
            for table in tables:
                if not table.replace("_", "").isalnum():
                    raise ValueError("unsafe-inventory-table")
                columns = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
                state_column = "state" if "state" in columns else "status" if "status" in columns else None
                if state_column:
                    groups[table] = dict(db.execute(f'SELECT "{state_column}",count(*) FROM "{table}" GROUP BY "{state_column}"'))
            records[label] = {"exists": True, "state_counts": groups}
    return {"preservation": protection.preservation_receipt(), "job_inventory": records}


def call(opener, token, path, data=None):
    request = Request("http://127.0.0.1:3000" + path,
        data=None if data is None else json.dumps(data).encode(),
        method="GET" if data is None else "POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    status, body = auth.request_json(opener, request)
    if status != 200 or not isinstance(body, dict):
        raise ValueError(f"ordinary-proxy-request-failed:{path.split('?')[0]}:{status}")
    return body


def publication(job_id, *, feature_pipeline="local-db-shared-v1"):
    import joblib
    from services.db_shared_input import validate_published_artifact, verify_bundle_sources
    from training.db_shared_contract import fit_module
    with sqlite3.connect((ROOT / "python-api/data/local_retrain_jobs.db").as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT j.state,j.snapshot_path,j.snapshot_sha256,j.created_at,j.finished_at,"
            "p.published_path,p.artifact_sha256,j.request_json FROM local_retrain_jobs j "
            "JOIN local_retrain_publications p ON p.job_id=j.job_id WHERE j.job_id=? AND p.state='published'", (job_id,)).fetchone()
    if not row or row[0] != "completed":
        raise ValueError("normal-completed-publication-required")
    state, snapshot, snapshot_sha, created, finished, model_path, model_sha, request = row
    if validate_published_artifact(Path(model_path)) != model_sha:
        raise ValueError("normal-publication-hash-mismatch")
    bundle = joblib.load(model_path)
    if protection.file_hash(Path(model_path)) != model_sha:
        raise ValueError("normal-publication-changed-during-load")
    verify_bundle_sources(bundle)
    fit_module(feature_pipeline)._validate_prediction_bundle(bundle)
    if protection.file_hash(Path(snapshot)) != snapshot_sha:
        raise ValueError("normal-snapshot-hash-mismatch")
    execution = bundle["local_execution"]
    if execution["job_id"] != job_id or execution["data_snapshot_sha256"] != snapshot_sha:
        raise ValueError("normal-publication-job-binding-mismatch")
    total = (datetime.fromisoformat(finished.replace("Z", "+00:00"))
             - datetime.fromisoformat(created.replace("Z", "+00:00"))).total_seconds()
    report = {"job_id": job_id, "job_state": state, "request": json.loads(request),
        "snapshot_path": snapshot, "snapshot_sha256": snapshot_sha,
        "model_id": Path(model_path).stem, "model_path": model_path, "artifact_sha256": model_sha,
        "publication_verified": True, "feature_source_hashes": bundle["feature_source_hashes"],
        "feature_count": len(bundle["feature_columns"]), "feature_columns": bundle["feature_columns"],
        "feature_pipeline": bundle.get("feature_pipeline"),
        "fit_audit": bundle["shared_training_audit"], "evaluation": bundle["evaluation"],
        "probability_validation": bundle["probability_validation"],
        "training_eligibility": bundle["training_eligibility"],
        "dataset_period_start": bundle["dataset_period_start"], "dataset_period_end": bundle["dataset_period_end"],
        "training_stage_seconds": bundle["training_stage_seconds"], "whole_job_seconds": total,
        "training_time_seconds": bundle["training_time_seconds"],
        "acceleration": bundle["acceleration"], "training_execution": bundle["training_execution"]}
    return report


def compare(reports):
    baseline = reports[0]
    base_audit = baseline["fit_audit"]
    base_primary = baseline["evaluation"]["primary"]
    checks = []
    for current in reports[1:]:
        audit = current["fit_audit"]
        primary = current["evaluation"]["primary"]
        rho_delta = primary["rank_correlation"] - base_primary["rank_correlation"]
        rmse_relative = (primary["rmse"] - base_primary["rmse"]) / base_primary["rmse"]
        same_data = current["snapshot_sha256"] == baseline["snapshot_sha256"]
        same_inputs = (current["acceleration"]["cache_receipt"]["input_hashes"]
                       == baseline["acceleration"]["cache_receipt"]["input_hashes"])
        same_partitions = audit["partitions"] == base_audit["partitions"]
        same_selected = audit["selected_preset"] == base_audit["selected_preset"]
        same_sources = (current["feature_source_hashes"] == baseline["feature_source_hashes"]
                        and audit["source_hashes"] == base_audit["source_hashes"])
        same_schema = current["feature_columns"] == baseline["feature_columns"] and current["feature_count"] == 47
        cache = current["acceleration"]["cache_receipt"]
        base_cache = baseline["acceleration"]["cache_receipt"]
        same_cache_lineage = (cache["cache_key_sha256"] == base_cache["cache_key_sha256"]
                             and cache["source_hashes"] == base_cache["source_hashes"]
                             and cache.get("point_in_time_validated") is True
                             and base_cache.get("point_in_time_validated") is True)
        gates_match = (current["probability_validation"]["gate_results"]
                       == baseline["probability_validation"]["gate_results"])
        probability_checks = {}
        for key in ("nll", "brier"):
            old = baseline["probability_validation"]["metrics"][key]
            new = current["probability_validation"]["metrics"][key]
            probability_checks[key] = {"baseline": old, "candidate": new,
                                       "relative_deterioration": (new - old) / old}
        values = [rho_delta, rmse_relative, base_primary["rmse"], primary["rmse"],
                  baseline["whole_job_seconds"], current["whole_job_seconds"],
                  baseline["acceleration"]["total_seconds"], current["acceleration"]["total_seconds"]]
        finite = all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)
        finite = finite and all(value > 0 for value in values[2:])
        finite = finite and all(math.isfinite(row["relative_deterioration"]) for row in probability_checks.values())
        valid = (finite and same_data and same_inputs and same_partitions and same_selected and gates_match
                 and same_sources and same_schema and same_cache_lineage
                 and abs(rho_delta) <= .001 and rmse_relative <= .001
                 and all(row["relative_deterioration"] <= .001 for row in probability_checks.values()))
        checks.append({"job_id": current["job_id"], "same_snapshot": same_data,
            "same_raw_inputs": same_inputs, "same_partitions": same_partitions,
            "same_selected_preset": same_selected, "same_probability_gates": gates_match,
            "same_sources": same_sources, "same_feature_schema": same_schema,
            "same_cache_lineage": same_cache_lineage, "finite_metrics_and_timings": finite,
            "rank_correlation_delta": rho_delta, "rmse_relative_deterioration": rmse_relative,
            "probability": probability_checks, "quality_pass": valid,
            "pipeline_speedup": baseline["acceleration"]["total_seconds"] / current["acceleration"]["total_seconds"],
            "whole_job_speedup": baseline["whole_job_seconds"] / current["whole_job_seconds"]})
    actual_gpu = reports[-1]["training_execution"]
    gpu_verified = (reports[-1]["acceleration"]["device"] == "gpu"
                    and actual_gpu.get("device") == "gpu" and actual_gpu.get("gpu_device_verified") is True
                    and len(actual_gpu.get("gpu_fit_records", [])) == 3
                    and all(row.get("gpu_device_verified") is True for row in actual_gpu["gpu_fit_records"]))
    reused = all(report["acceleration"]["cache_hit"] is True for report in reports[1:])
    return {"reference_job": baseline["job_id"], "comparisons": checks,
            "actual_gpu_verified": gpu_verified, "feature_reuse_verified": reused,
            "quality_pass": len(checks) == 2 and all(row["quality_pass"] for row in checks),
            "acceleration_path_pass": gpu_verified and reused}


def run():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    output = ROOT / "reports/generated/normal-training-acceleration" / stamp
    output.mkdir(parents=True, exist_ok=False)
    before = inventory()
    save(output / "before.json", before)
    # Fixed before result observation. Same selection/calibration/evaluation
    # protocol and tolerances as the preceding full-data benchmark.
    protocol = {"schema": "normal-training-acceleration-verification-v1",
        "period": ["2016-01", "2026-09"], "feature_pipeline": "local-db-shared-v1",
        "cases": [{"compute_device": "cpu", "reuse_features": False},
                  {"compute_device": "cpu", "reuse_features": True},
                  {"compute_device": "auto", "reuse_features": True}],
        "quality_limits": {"absolute_rank_correlation_delta": .001,
                           "relative_rmse_nll_brier_deterioration": .001},
        "no_activation": True, "no_roi_evaluation": True, "no_forward_changes": True}
    save(output / "protocol.json", protocol)
    auth.TIMEOUT_SECONDS = 40
    opener = auth.secure_opener()
    status, token = auth.authenticate(opener, auth.configuration(ROOT))
    if status != 200 or not token:
        raise ValueError("ordinary-admin-authentication-unavailable")
    capability = call(opener, token, "/api/ml/train/capability")
    save(output / "capability.json", capability)
    if not capability.get("enabled") or not capability.get("acceleration", {}).get("feature_cache_supported"):
        raise ValueError("ordinary-acceleration-not-deployed")
    reports = []
    for index, case in enumerate(protocol["cases"]):
        request = {"target": "speed_deviation", "model_type": "lightgbm",
            "feature_pipeline": protocol["feature_pipeline"], "force_sync": False,
            "use_sqlite": True, "ultimate_mode": True, "use_optimizer": True,
            "use_optuna": False, "test_size": .2, "cv_folds": 5,
            "training_date_from": "2016-01", "training_date_to": "2026-09", **case}
        started = time.perf_counter()
        response = call(opener, token, "/api/ml/train/start", request)
        job_id = str(uuid.UUID(response["job_id"]))
        save(output / f"case-{index + 1}-request.json", {"request": request, "job_id": job_id})
        print(json.dumps({"case": index + 1, "job_id": job_id, "stage": "started", **case}), flush=True)
        previous = None
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            state = call(opener, token, f"/api/ml/train/status/{job_id}")
            stage = (state.get("status"), state.get("progress"))
            if stage != previous:
                print(json.dumps({"case": index + 1, "status": stage[0], "progress": stage[1]}, ensure_ascii=False), flush=True)
                previous = stage
            if state.get("status") == "completed":
                break
            if state.get("status") in {"failed", "error"}:
                save(output / f"case-{index + 1}-failed.json", state)
                raise ValueError("ordinary-training-job-failed:" + job_id)
            time.sleep(2)
        else:
            raise ValueError("ordinary-training-verification-timeout-job-not-stopped:" + job_id)
        report = publication(job_id)
        report["request_through_observed_completion_seconds"] = time.perf_counter() - started
        save(output / f"case-{index + 1}.json", report)
        reports.append(report)
        print(json.dumps({"case": index + 1, "stage": "completed", "whole_job_seconds": report["whole_job_seconds"],
                          "acceleration": {key: report["acceleration"][key] for key in
                            ("device", "cache_hit", "preparation_seconds", "fit_seconds", "total_seconds")}}), flush=True)
    after = inventory()
    save(output / "after.json", after)
    preserved = before["preservation"] == after["preservation"]
    result = {"schema": protocol["schema"], "preserved": preserved, "cases": reports, "comparison": compare(reports)}
    save(output / "results.json", result)
    manifest = {path.name: protection.file_hash(path) for path in sorted(output.iterdir()) if path.is_file()}
    save(output / "artifact_manifest.json", {"sha256": manifest})
    print(json.dumps({"output": str(output), "preserved": preserved, "quality_pass": result["comparison"]["quality_pass"]}), flush=True)
    return 0 if preserved and result["comparison"]["quality_pass"] and result["comparison"]["acceleration_path_pass"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-training", action="store_true")
    args = parser.parse_args()
    if args.run_training:
        raise SystemExit(run())
    result = inventory()
    print(json.dumps({"active_model_id": result["preservation"]["active_model_id"],
        "active_pointer_sha256": result["preservation"]["active_pointer_sha256"],
        "forward_processes": result["preservation"]["forward_processes"],
        "forward_source_checks": len(result["preservation"]["forward_source_pins"]),
        "job_inventory": result["job_inventory"]}, ensure_ascii=False, indent=2))

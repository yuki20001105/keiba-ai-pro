"""Freeze an all-row, label-free training-data audit then publish new files."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "scripts"), str(ROOT / "keiba")]
import pandas as pd
from feature_provenance.training import analyze_training
from verify_market_segments_outputs import sha256, require, read, verify_files
from snapshot_feature_pit_audit import protected

COHORTS = {"original_market_research": ROOT / "reports/generated/model-experiments/market-edge-v1/development.parquet",
           "released_clean": ROOT / "reports/generated/data-quality/market-h04-clean-v2/clean_runners.parquet"}


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def load_source(snapshot):
    path = Path(snapshot).resolve(strict=True)
    wal = Path(str(path) + "-wal")
    require(not wal.exists() or not wal.stat().st_size, "pending WAL in immutable snapshot")
    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        schemas = {name: [list(row) for row in db.execute('PRAGMA table_info("' + name + '")')]
                   for name in ("training_data", "races_ultimate", "race_results_ultimate", "races")}
        # All training values are in scope. Race/runner projections intentionally
        # cannot contain finish/time/payout/odds/result labels in any year.
        training = pd.read_sql_query("SELECT * FROM training_data ORDER BY id", db)
        races = pd.read_sql_query("SELECT race_id,inner_race_id,data_json_valid,race_date,num_horses,race_name,venue,source_host,created_at FROM races_ultimate", db)
        runners = pd.read_sql_query("SELECT source_row_id,race_id,inner_race_id,data_json_valid,horse_id,horse_number,horse_name,created_at FROM race_results_ultimate", db)
        legacy = pd.read_sql_query("SELECT race_id,kaisai_date,source,created_at FROM races", db)
    return training, races, runners, legacy, schemas


def execute(snapshot, expected_sha, output):
    snapshot, output = Path(snapshot).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    require(not (output / "audit_plan.json").exists(), "audit plan already exists; never overwrite a run")
    require(sha256(snapshot) == expected_sha, "source snapshot hash mismatch")
    source_audit = output / "training_source_audit.json"
    require(source_audit.is_file(), "independent parser/source provenance audit is required first")
    snapshot_manifest = snapshot.parent / "snapshot-manifest.json"
    before = protected()
    code = {name: sha256(ROOT / name) for name in ("scripts/audit_training_data_provenance.py", "python-api/feature_provenance/training.py")}
    plan = {"schema": "training-data-pit-audit-plan-v1", "frozen_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_path": str(snapshot), "snapshot_sha256": expected_sha,
        "snapshot_manifest_sha256": sha256(snapshot_manifest), "source_audit_sha256": sha256(source_audit),
        "cohorts": {key: {"path": str(path), "sha256": sha256(path)} for key, path in COHORTS.items()}, "source_sha256": code,
        "scope": "all training_data rows; all canonical race/runner identity/date projections; 2013-2026 plus out-of-range/invalid year accounting",
        "race_outcome_columns_loaded": [], "performance_metrics": False, "imputation": False, "model_training": False,
        "identity": "race+number candidate; verified by exact NFKC + outer-whitespace-trimmed race-local name; absent number may use unique race-local normalized name; conflicting keys never overridden",
        "event_date_classes": {"A": "workout day strictly before race day", "B": "same day, intraday time unknown", "C": "after race day", "D": "date/race unknown"},
        "source_capture_rule": "Neither workout date nor created_at proves publication/capture; no current records can be upgraded to SAFE_POINT_IN_TIME",
        "registration_clock": "stored created_at naive calendar day comparison only; timezone unverified; never a source receipt",
        "missingness": "null/blank/marker/invalid/zero kept separate; structural vs extraction missingness remains unresolved",
        "statistics": "exact NFKC-normalized course + condition + canonical race year; descriptive only, never fitted feature transforms",
        "protected_before": before}
    write(output / "audit_plan.json", plan)
    print(json.dumps({"stage": "plan_frozen", "plan_sha256": sha256(output / "audit_plan.json")}), flush=True)
    training, races, runners, legacy, schemas = load_source(snapshot)
    cohorts = {key: pd.read_parquet(path, columns=["race_id", "race_date", "horse_number", "horse_id"],
        filters=[("race_date", ">=", "20210101"), ("race_date", "<=", "20221231")], use_threads=False) for key, path in COHORTS.items()}
    print(json.dumps({"stage": "loaded_label_free", "training_rows": len(training), "race_headers": len(races), "runner_keys": len(runners)}), flush=True)
    artifacts = analyze_training(training, races, runners, legacy, cohorts, schemas)
    sets = {key: set(zip(frame.race_id.astype(str), frame.horse_number)) for key, frame in cohorts.items()}
    artifacts["training_data_inventory.json"]["original_vs_released_keys"] = {
        "identical": sets["original_market_research"] == sets["released_clean"],
        "original_only": len(sets["original_market_research"] - sets["released_clean"]),
        "released_only": len(sets["released_clean"] - sets["original_market_research"])}
    artifacts["training_data_inventory.json"]["audit_plan_sha256"] = sha256(output / "audit_plan.json")
    artifacts["training_data_inventory.json"]["source_audit_sha256"] = plan["source_audit_sha256"]
    require(sha256(snapshot) == expected_sha and sha256(source_audit) == plan["source_audit_sha256"], "source changed during audit")
    verify_files(ROOT, code)
    for key, item in plan["cohorts"].items(): require(sha256(item["path"]) == item["sha256"], "cohort changed")
    after = protected()
    for filename, value in artifacts.items():
        if isinstance(value, pd.DataFrame): value.to_csv(output / filename, index=False, encoding="utf-8", mode="x", lineterminator="\n")
        else: write(output / filename, value)
    result = {"status": "completed", "audit_plan_sha256": sha256(output / "audit_plan.json"), "snapshot_sha256": expected_sha,
        "rows": len(training), "race_outcome_columns_loaded": [], "safe_point_in_time_records": 0,
        "temporal_feature_gate": "NO", "performance_metrics_computed": False, "model_training_performed": False,
        "source_mutated": False, "protected_after": after,
        "files": {filename: sha256(output / filename) for filename in artifacts}, "source_sha256": code}
    write(output / "training-audit-result.json", result)
    print(json.dumps({"status": "completed", "rows": len(training), "event_date_classes": artifacts["training_data_inventory.json"]["event_date_classes"],
        "cohorts": artifacts["training_data_inventory.json"]["cohort_reproduction"], "result_sha256": sha256(output / "training-audit-result.json")}, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--expected-snapshot-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.name == "nt":
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    execute(args.snapshot, args.expected_snapshot_sha256, args.output)

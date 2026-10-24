"""Read-only real-snapshot context80 parity and cold/warm feature benchmark.

No model fit/prediction, HTTP request, live SQLite access, active-model change
or paper/purchase record is performed. The source-bound formula is not edited.
Timings cover feature creation only, never the full UI prediction pipeline.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "python-api", ROOT / "keiba"):
    sys.path.insert(0, str(directory))

from training import db_context_features as context
from evaluate_db_shared_venues import preservation_receipt

SNAPSHOT = ROOT / "reports/generated/context-features/20261012T133445-becbcd12/snapshot.sqlite3"
STUDY = ROOT / "reports/generated/context-features/20261012T133656-decee88e"
RACES = ("202606040902", "202609040903")
DAY = "20260927"
OLD_SOURCE = ROOT / "reports/generated/context-features/20261012-serving-order-v2/old-db_context_features.py"


def digest(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def compare_features(actual, expected):
    rows = []
    for name in context.FEATURE_COLUMNS:
        left, right = actual[name], expected[name]
        missing_equal = bool(np.array_equal(left.isna().to_numpy(), right.isna().to_numpy()))
        if name in context.CATEGORICAL_COLUMNS:
            equality = left.astype("string").eq(right.astype("string"))
            mismatches = int((~equality.fillna(left.isna() & right.isna())).sum())
            maximum = None
            exact = missing_equal and mismatches == 0
        else:
            a, b = pd.to_numeric(left).to_numpy(dtype=float), pd.to_numeric(right).to_numpy(dtype=float)
            present = np.isfinite(a) & np.isfinite(b)
            maximum = float(np.max(np.abs(a[present]-b[present]))) if present.any() else None
            # No relaxation is needed: the stored raw matrix is float32 and
            # must equal the serving matrix, not merely produce similar ranks.
            exact = missing_equal and bool(np.array_equal(a, b, equal_nan=True))
            mismatches = int((~((a == b) | (np.isnan(a) & np.isnan(b)))).sum())
        rows.append({"feature": name, "missingness_equal": missing_equal,
                     "exact_equal": exact, "mismatch_rows": mismatches,
                     "max_absolute_difference": maximum})
    return {"all80_exact_equal": all(row["exact_equal"] for row in rows),
            "all80_missingness_equal": all(row["missingness_equal"] for row in rows),
            "columns": rows, "numeric_tolerance": {"rtol": 0, "atol": 0}}


def execute():
    output = ROOT / "reports/generated/context-features" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-serving-parity-v2")
    output.mkdir(parents=True, exist_ok=False)
    protocol = json.loads((STUDY / "protocol.json").read_text(encoding="utf-8"))
    manifest = json.loads((STUDY / "artifact_manifest.json").read_text(encoding="utf-8"))["files"]
    bound = context.feature_source_hashes()
    old_bytes = OLD_SOURCE.read_bytes()
    new_bytes = Path(context.__file__).read_bytes()
    old_line = b'"baseline": _baseline_state(history, day), "coverage": coverage,'
    new_line = b'"baseline": _baseline_state(checked, day), "coverage": coverage,'
    if (old_bytes.count(old_line) != 1 or old_bytes.replace(old_line, new_line, 1) != new_bytes
            or digest(OLD_SOURCE) != protocol["source_hashes"]["training.db_context_features"]):
        raise ValueError("context-serving-v2-must-be-exact-one-line-sort-fix-only")
    if not all(protocol["source_hashes"].get(name) == value for name, value in bound.items()
               if name != "training.db_context_features"):
        raise ValueError("context-serving-feature-source-does-not-match-sealed-study")
    migration = {"version": "serving-order-v2", "old_feature_module_sha256": digest(OLD_SOURCE),
        "new_feature_module_sha256": digest(context.__file__), "changed_function": "_serving_state",
        "only_change": "pass checked race_date/race_id/horse_number-sorted history to cached baseline",
        "whole_source_identical_except_this_one_line": True,
        "training_function_formula_groups_and_PIT_thresholds_unchanged": True,
        "old_training_matrix_not_regenerated": True, "training_matrix_hash_checked": True,
        "old_study_artifacts_preserved": True}
    source_files = {"snapshot.sqlite3": SNAPSHOT, "target.parquet": STUDY / "target.parquet",
                    "features.parquet": STUDY / "features.parquet", "protocol.json": STUDY / "protocol.json"}
    before_hashes = {name: digest(path) for name, path in source_files.items()}
    expected_hashes = {name: manifest[name] for name in source_files if name != "snapshot.sqlite3"}
    expected_hashes["snapshot.sqlite3"] = protocol["snapshot_sha256"]
    if before_hashes != expected_hashes:
        raise ValueError("context-serving-input-artifact-hash-mismatch")
    before_protected = preservation_receipt()
    save(output / "before.json", {"inputs": before_hashes, "protected": before_protected,
         "feature_source_hashes": bound, "script_sha256": digest(__file__), "network": False,
         "live_database_access": False, "model_fit": False, "model_inference": False})
    save(output / "source_migration.json", migration)
    target = pd.read_parquet(STUDY / "target.parquet")
    features = pd.read_parquet(STUDY / "features.parquet")
    if (len(target) != len(features) or not target.index.equals(features.index)
            or list(features.columns) != list(context.FEATURE_COLUMNS)):
        raise ValueError("context-serving-study-row-schema-mismatch")
    if target.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("context-serving-study-identity-duplicate")
    reports = []
    with context._SERVING_LOCK:
        context._SERVING_STATES.clear()
    for index, rid in enumerate(RACES):
        positions = np.flatnonzero(target.race_id.astype(str).eq(rid).to_numpy())
        current = target.iloc[positions].copy().reset_index(drop=True)
        expected = features.iloc[positions].copy().reset_index(drop=True)
        if (current.empty or not current.race_date.astype(str).eq(DAY).all()
                or not current.num_horses.eq(len(current)).all()
                or current.horse_number.duplicated().any()
                or current.horse_id.loc[current.horse_id.ne("")].duplicated().any()):
            raise ValueError("context-serving-selected-complete-race-identity-required")
        info = current.iloc[0].to_dict()
        # All result columns remain present in the input card deliberately;
        # the canonical serving card physically excludes them before computing.
        print(json.dumps({"stage": "feature_build", "race_id": rid,
              "mode": "cold_first" if index == 0 else "warm_same_day"}), flush=True)
        started = time.perf_counter()
        actual, audit = context.build_serving_features(SNAPSHOT, info, current)
        elapsed = time.perf_counter() - started
        comparison = compare_features(actual, expected)
        record = {"race_id": rid, "race_date": DAY, "venue": info["venue"], "surface": info["surface"],
            "runner_count": len(current), "identity": current[["race_id", "horse_number", "horse_id"]].to_dict("records"),
            "feature_count": len(actual.columns), "feature_generation_seconds": elapsed,
            "timing_mode": "cold_first" if index == 0 else "warm_same_day",
            "history_state_cache_hit": audit["history_state_cache_hit"],
            "history_rows": audit["history_rows"], "history_max_source_date": audit["history_max_source_date"],
            "history_state_data_revision": audit["history_state_data_revision"],
            "history_state_revision_kind": audit["history_state_revision_kind"],
            "history_state_source_tracking": audit["history_state_source_tracking"],
            "current_outcomes_consumed": audit["current_outcomes_consumed"],
            "same_day_or_future_history_used": audit["same_day_or_future_history_used"],
            "comparison": comparison}
        reports.append(record)
        save(output / f"{rid}.json", record)
        print(json.dumps({"stage": "race_verified", "race_id": rid, "seconds": elapsed,
            "cache_hit": audit["history_state_cache_hit"], "exact_equal": comparison["all80_exact_equal"]}), flush=True)
        if not comparison["all80_exact_equal"]:
            save(output / "failure.json", {"race_id": rid, "reason": "serving-training-feature-parity-failed",
                 "differing_features": [row for row in comparison["columns"] if not row["exact_equal"]],
                 "formula_not_changed": True})
            raise ValueError("context-serving-training-feature-parity-failed")
    after_hashes = {name: digest(path) for name, path in source_files.items()}
    after_protected = preservation_receipt()
    protected = before_protected == after_protected
    stable = before_hashes == after_hashes and bound == context.feature_source_hashes()
    if not (protected and stable and reports[0]["history_state_cache_hit"] is False
            and reports[1]["history_state_cache_hit"] is True):
        raise ValueError("context-serving-protection-or-cache-contract-failed")
    receipt = {"schema": "db-context-real-serving-parity-v2", "status": "PASS",
        "source_snapshot": str(SNAPSHOT.relative_to(ROOT)), "study": str(STUDY.relative_to(ROOT)),
        "race_ids": list(RACES), "runner_count": sum(row["runner_count"] for row in reports),
        "feature_count": 80, "all_numeric_exact_and_nan_parity": True,
        "cold_first_feature_seconds": reports[0]["feature_generation_seconds"],
        "warm_second_same_day_feature_seconds": reports[1]["feature_generation_seconds"],
        "timing_scope": "feature generation only; different complete races; not whole model/UI prediction speed",
        "whole_prediction_speedup_claimed": False, "protected_state_unchanged": protected,
        "source_artifacts_unchanged": stable, "feature_source_hashes": bound,
        "source_artifact_hashes": after_hashes, "network": False, "live_database_access": False,
        "model_fit": False, "model_inference": False, "purchase": False,
        "forward_collection_changed": False, "active_model_changed": False,
        "historical_reconstruction_is_not_prestart_capture_evidence": True,
        "source_migration": migration,
        "script_sha256": digest(__file__)}
    save(output / "verification.json", receipt)
    save(output / "after.json", {"inputs": after_hashes, "protected": after_protected})
    save(output / "artifact_manifest.json", {"files": {path.name: digest(path) for path in sorted(output.glob("*.json"))}})
    print(json.dumps({"stage": "completed", "directory": str(output), "status": "PASS",
        "cold_seconds": receipt["cold_first_feature_seconds"],
        "warm_seconds": receipt["warm_second_same_day_feature_seconds"]}), flush=True)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run the fixed read-only two-race verification")
    args = parser.parse_args()
    if not args.run:
        parser.error("explicit --run is required")
    execute()

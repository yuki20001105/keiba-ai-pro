"""One stored card, two frozen candidates, RO DB and new report only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]


def main() -> int:
    import numpy as np
    import pandas as pd
    from services.local_candidate_input import (
        build_candidate_input, candidate_specs, explain_candidate_scores,
        load_candidate_bundle, predict_candidate_scores,
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--race-id", default="202605040301")
    parser.add_argument("--database", type=Path, default=ROOT / "keiba/data/keiba_ultimate.db")
    args = parser.parse_args()
    database = args.database.resolve(strict=True)
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        meta = connection.execute("SELECT data FROM races_ultimate WHERE race_id=?", (args.race_id,)).fetchone()
        raw_rows = [row[0] for row in connection.execute("SELECT data FROM race_results_ultimate WHERE race_id=?", (args.race_id,))]
    finally:
        connection.close()
    if meta is None or not raw_rows:
        raise ValueError("stored-current-card-required")
    info = json.loads(meta[0])
    frame = pd.DataFrame([json.loads(row) for row in raw_rows])
    candidates = []
    for spec in candidate_specs():
        started = time.perf_counter()
        original = Path(spec["source_path"])
        before = hashlib.sha256(original.read_bytes()).hexdigest()
        bundle = load_candidate_bundle(original)
        features, diagnostics = build_candidate_input(bundle, frame, db_path=database, race_info=info)
        raw, weights = predict_candidate_scores(bundle, features)
        explanations = explain_candidate_scores(bundle, features)
        full_contributions = bundle["model"].predict(features, pred_contrib=True, num_threads=2)
        shape_exact = list(features.columns) == bundle["feature_columns"]
        after = hashlib.sha256(original.read_bytes()).hexdigest()
        candidates.append({
            "model_id": spec["model_id"], "feature_count": len(features.columns),
            "runner_count": len(features), "artifact_sha256": before, "artifact_unchanged": before == after == spec["sha256"],
            "fitted_encoder_only": True, "column_order_exact": shape_exact,
            "all_predictions_finite": bool(np.isfinite(raw).all()),
            "reference_weight_sum": float(weights.sum()), "weights_are_calibrated_wins": False,
            "shap_rows": len(explanations), "shap_prediction_max_abs_difference": float(np.max(np.abs(full_contributions.sum(axis=1) - raw))),
            "elapsed_seconds": time.perf_counter() - started, "input_diagnostics": diagnostics,
        })
    if not all(row["artifact_unchanged"] and row["column_order_exact"] and row["all_predictions_finite"]
               and abs(row["reference_weight_sum"] - 1.0) < 1e-12
               and row["shap_rows"] == row["runner_count"]
               and row["shap_prediction_max_abs_difference"] < 1e-10 for row in candidates):
        raise ValueError("local-candidate-smoke-invariant-failed")
    report = {
        "schema": "local-safe-candidate-readonly-smoke-v1", "status": "passed",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(), "race_id": args.race_id,
        "race_date": info.get("date"), "runner_count": len(frame), "database_open_mode": "ro; query_only; read transaction",
        "input_objects_sha256": hashlib.sha256((meta[0] + "\n" + "\n".join(raw_rows)).encode()).hexdigest(),
        "network_calls": 0, "training_started": False, "active_model_changed": False,
        "collector_or_track_a_changed": False, "production_changed": False, "actual_purchase": False,
        "candidate_results": candidates, "scope": "offline transform/scoring/SHAP smoke, not ROI or as-of authorization",
    }
    output = ROOT / "reports/generated/local-release"
    output.mkdir(parents=True, exist_ok=True)
    path = output / ("candidate-adapter-smoke-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"status": report["status"], "report": str(path), "candidates": [{"model_id": row["model_id"], "elapsed_seconds": row["elapsed_seconds"], "feature_count": row["feature_count"], "status": row["input_diagnostics"]["status"], "history_rows": row["input_diagnostics"]["history_audit"]["eligible_rows"]} for row in candidates]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

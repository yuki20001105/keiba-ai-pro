"""Bounded local research: immutable DB, chronological heads, matched controls."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

import joblib
import numpy as np
import pandas as pd

from training import db_context_features as context
from training import db_context_fit as control
from training import db_shared_optimized as optimized
from training import pace_fit


def _write(path, body):
    temporary = path.with_suffix(path.suffix + ".pending")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(body, stream, indent=2, ensure_ascii=False, allow_nan=False)
    temporary.replace(path)


def _snapshot(source, destination):
    if destination.exists():
        raise ValueError("fresh-immutable-snapshot-required")
    with sqlite3.connect(source.resolve(strict=True).as_uri() + "?mode=ro", uri=True, timeout=30) as live:
        live.execute("PRAGMA query_only=ON")
        with sqlite3.connect(destination) as snapshot:
            live.backup(snapshot, pages=4096, sleep=.1)
            snapshot.execute("PRAGMA journal_mode=DELETE")
            if snapshot.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("snapshot-integrity-failed")


def _labels(path, target):
    if path is None:
        return None, {"verified_labels": 0, "reason": "no_recovered_evidence"}
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict) or body.get("schema") != "verified-pace-lap-recovery-v1":
        raise ValueError("verified-recovery-schema-required")
    records = body.get("observations")
    if not isinstance(records, list):
        raise ValueError("verified-recovery-observations-required")
    if not records:
        return None, {"path": str(path), "sha256": control.file_hash(path), "verified_labels": 0}
    frame = pd.DataFrame(records)
    # Recovery adapters are deliberately not permissive. An unknown schema
    # needs explicit review rather than accepting a caller's boolean claim.
    required = {"race_id", "race_date", "first600_seconds", "source_sha256", "validated", "lap_evidence"}
    if not required.issubset(frame):
        raise ValueError("verified-recovery-schema-required")
    frame = frame.loc[frame.race_id.isin(target.race_id)].copy()
    expected = target.drop_duplicates("race_id").set_index("race_id").race_date
    if not frame.race_date.astype(str).eq(frame.race_id.map(expected).astype(str)).all():
        raise ValueError("recovery-target-date-mismatch")
    distances = pd.to_numeric(frame.lap_evidence.map(
        lambda item: item.get("total_distance") if isinstance(item, dict) and item.get("schema") == "lap-observation-v1" else None),
        errors="coerce")
    expected_distance = pd.to_numeric(target.drop_duplicates("race_id").set_index("race_id").distance, errors="coerce")
    if not (np.isfinite(distances).all() and distances.eq(frame.race_id.map(expected_distance)).all()):
        raise ValueError("recovery-target-distance-mismatch")
    if frame.race_id.duplicated().any():
        raise ValueError("recovery-race-identity-duplicate")
    return frame, {"path": str(path), "sha256": control.file_hash(path),
                   "verified_labels": int(frame.validated.eq(True).sum()),
                   "by_year": frame.loc[frame.validated.eq(True)].race_date.astype(str).str[:4].value_counts().sort_index().to_dict()}


def _comparison_passed(results):
    candidate = results["pace_stack"]["probability_metrics"]
    controls = [results[name]["probability_metrics"] for name in ("baseline46", "context80")]
    if not all(item and item.get("nll") is not None and item.get("brier") is not None
               for item in (candidate, *controls)):
        return False
    return all(candidate["nll"] < item["nll"] and candidate["brier"] <= item["brier"] for item in controls)


def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    protocol = {"schema": "pace-stack-research-protocol-v1", "created_at": datetime.now().isoformat(),
                "date_from": args.date_from, "date_to": args.date_to,
                "TRAIN": "race_date <= 20241231", "CAL": "20250101 <= race_date <= 20251231",
                "TEST": "race_date >= 20260101", "controls": ["baseline46", "context80"],
                "candidate": "context80 + 10 chronological supervised forecasts",
                "cpu_threads": 2, "concurrent_presets": False, "optuna": False,
                "selection": "TRAIN only; CAL temperature; fixed TEST matched comparison",
                "known_test_period_not_claimed_unseen": True, "network_requests": 0,
                "active_model_changed": False, "live_database_mutated": False,
                "sources": pace_fit.source_hashes(), "runner_sha256": control.file_hash(__file__)}
    if (output / "protocol.json").exists():
        existing = json.loads((output / "protocol.json").read_text(encoding="utf-8"))
        if (any(existing.get(key) != protocol[key] for key in ("date_from", "date_to"))
                or any(existing.get("sources", {}).get(key) != value for key, value in control.source_hashes().items())):
            raise ValueError("research-protocol-drift")
    else:
        _write(output / "protocol.json", protocol)
    snapshot = output / "snapshot.sqlite"
    if not snapshot.exists():
        print("Creating read-only online backup...", flush=True)
        _snapshot(args.db, snapshot)
    target, features, coverage, receipt = control.prepare_inputs(
        snapshot, args.date_from, args.date_to, output / "inputs", progress=lambda text, _: print(text, flush=True))
    # Target observations are labels only, never features or serving inputs.
    target, corner_evidence = context._attach_history_corners(snapshot, target)
    target, features = control.prepare(target, features)
    _write(output / "input-receipt.json", {**receipt, "coverage": coverage, "position_label_evidence": corner_evidence})
    splits = {"TRAIN": np.flatnonzero(target.race_date.le("20241231")),
              "CAL": np.flatnonzero(target.race_date.between("20250101", "20251231")),
              "TEST": np.flatnonzero(target.race_date.ge("20260101"))}
    pace_fit._validate_partitions(target, splits)
    if args.prepare_only:
        print(json.dumps({"stage": "prepared", "races": int(target.race_id.nunique()),
                          "rows": len(target), "position_label_evidence": corner_evidence}, ensure_ascii=False), flush=True)
        return
    labels, label_receipt = _labels(args.labels, target)
    _write(output / "label-receipt.json", label_receipt)
    if (output / "comparison.json").exists():
        raise ValueError("completed-research-run-not-overwritten")
    _write(output / "training-protocol.json", {**protocol, "input_receipt_sha256": control.file_hash(output / "input-receipt.json"),
                                              "label_receipt_sha256": control.file_hash(output / "label-receipt.json")})
    results = {}
    for name, columns in (("baseline46", optimized.FEATURE_COLUMNS), ("context80", context.FEATURE_COLUMNS)):
        print(f"Fitting matched {name}...", flush=True)
        bundle, result, _ = control.fit_on_partitions(target, features, splits, columns=columns,
                                                     n_threads=2, preset_parallel=False)
        joblib.dump(bundle, output / f"{name}.research.joblib", compress=3)
        _write(output / f"{name}.json", result)
        results[name] = result
    print("Fitting chronological supervised development stack...", flush=True)
    bundle, result, predictions = pace_fit.fit_on_partitions(
        target, features, splits, pace_labels=labels, progress=lambda text, _: print(text, flush=True))
    scores, _ = pace_fit.predict_scores(bundle, target.iloc[splits["TEST"]], features.iloc[splits["TEST"]])
    if not np.allclose(scores, predictions["scores"], atol=1e-8, rtol=1e-8):
        raise ValueError("frozen-head-inference-parity-failed")
    result["serialized_inference_parity"] = False
    artifact = output / "pace-stack.research.joblib"
    joblib.dump(bundle, artifact, compress=3)
    restored = joblib.load(artifact)
    restored_scores, _ = pace_fit.predict_scores(restored, target.iloc[splits["TEST"]], features.iloc[splits["TEST"]])
    if not np.allclose(scores, restored_scores, atol=1e-8, rtol=1e-8):
        raise ValueError("serialized-inference-parity-failed")
    result.update(serialized_inference_parity=True, artifact_sha256=control.file_hash(artifact))
    _write(output / "pace-stack.json", result)
    results["pace_stack"] = result
    comparison = {"schema": "pace-stack-matched-comparison-v1", "results": results,
                  "labels": label_receipt, "candidate_artifact": str(artifact),
                  "candidate_artifact_sha256": control.file_hash(artifact),
                  "comparison_succeeded": _comparison_passed(results),
                  "activation_blockers": result["activation_blockers"],
                  "active_model_changed": False, "publication_performed": False,
                  "win_roi": None, "actual_purchase_results_unchanged": True}
    _write(output / "comparison.json", comparison)
    print(json.dumps({k: v for k, v in comparison.items() if k != "results"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "keiba" / "data" / "keiba_ultimate.db")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date-from", default="20160101")
    parser.add_argument("--date-to", default=(datetime.now() - timedelta(days=1)).strftime("%Y%m%d"))
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    run(parser.parse_args())

"""Publish two explicit reference selections and import their sealed TEST books."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

import numpy as np
import pandas as pd

from services import monthly_reference_input as reference


def _features(bundle, spec):
    from training import db_shared_fit as legacy

    receipt = reference._study()
    target_path = reference.STUDY_ROOT / "target.parquet"
    if hashlib.sha256(target_path.read_bytes()).hexdigest() != receipt["source_inputs"]["target.parquet"]:
        raise ValueError("monthly-reference-target-input-changed")
    target = pd.read_parquet(target_path)
    path = reference.STUDY_ROOT / "features.parquet"
    if hashlib.sha256(path.read_bytes()).hexdigest() != receipt["source_inputs"]["features.parquet"]:
        raise ValueError("monthly-reference-feature-input-changed")
    features = pd.read_parquet(path)
    single = target.finish.eq(1).groupby(target.race_id).transform("sum").eq(1)
    target, features = target.loc[single].reset_index(drop=True), features.loc[single].reset_index(drop=True)
    target, features = legacy._prepare(target, features)
    if spec["directory"] == "monthly_random":
        # This matrix was generated after canonical sorting, not acquisition order.
        features = pd.read_parquet(reference.STUDY_ROOT / "random-features.parquet")
    if legacy._frame_digest(features) != bundle["model_input_contract"]["raw_feature_sha256"]:
        raise ValueError("monthly-reference-fitted-feature-digest-mismatch")
    return target, features


def verify_parity(spec, bundle, predictions):
    path = reference.MODELS_DIR / (spec["model_id"] + ".joblib")
    races = [str(predictions.race_id.iloc[0]), str(predictions.race_id.iloc[-1])]
    checks = []
    with sqlite3.connect((reference.STUDY_ROOT / "source.sqlite").resolve().as_uri() + "?mode=ro", uri=True) as db:
        for rid in races:
            group = predictions.loc[predictions.race_id.eq(rid)]
            info = json.loads(db.execute("SELECT data FROM races_ultimate WHERE race_id=?", (rid,)).fetchone()[0])
            result = reference.run_prediction(
                bundle, group, db_path=reference.STUDY_ROOT / "source.sqlite",
                race_info={**info, "race_id": rid}, model_path=path, n_threads=1)
            np.testing.assert_array_equal(result["raw"], group.raw_score.to_numpy())
            np.testing.assert_allclose(result["probabilities"], group.p_norm.to_numpy(), rtol=0, atol=1e-14)
            checks.append({"race_id": rid, "raw_scores_exact": True, "probabilities_equal": True})
    return checks


def import_test_books(spec, bundle, predictions, *, owner):
    from app_config import ULTIMATE_DB
    from betting.paper_simulation import list_paper_performance, record_paper_prediction

    target, features = _features(bundle, spec)
    test_ids = set(bundle["_monthly_reference"]["test_dates"])
    if (set(predictions.race_id) != test_ids
            or predictions.duplicated(["race_id", "horse_number"]).any()):
        raise ValueError("monthly-reference-test-prediction-cohort-mismatch")
    ledger = ROOT / "keiba" / "data" / "betting_performance.db"
    counts = Counter()
    for rid, group in predictions.groupby("race_id", sort=False):
        if (not target.loc[group.index, ["race_id", "horse_number"]].equals(group[["race_id", "horse_number"]])
                or not group.num_horses.eq(len(group)).all()):
            raise ValueError("monthly-reference-test-feature-or-field-mismatch")
        raw = features.loc[group.index, bundle["feature_columns"]]
        payload = {"success": True, "model_id": spec["model_id"],
                   "race_info": {"race_id": str(rid), "date": str(group.race_date.iloc[0]),
                                 "num_horses": len(group)},
                   "predictions": group[["horse_number", "horse_id", "p_norm", "odds"]].to_dict("records"),
                   "input_diagnostics": reference.diagnostics(
                       bundle, missing_value_rate=float(raw.isna().to_numpy().mean()),
                       no_history_rows=int(raw.safe_prior_starts.eq(0).sum()))}
        record = record_paper_prediction(
            ledger, owner, payload, bundle, model_sha256=spec["sha256"], historical_validation=True)
        counts["existing" if record.get("existing") else "recorded" if record["eligible"] else record["reason"]] += 1
    data = list_paper_performance(ledger, ULTIMATE_DB, owner, origin="historical_validation",
                                  model_id=spec["model_id"], race_ids=test_ids)
    return {"test_races": len(test_ids), "training_calibration_overlap": 0, "records": dict(counts),
            "summary": data["summary"], "models": [{k: v for k, v in model.items() if k != "points"}
                                                   for model in data["model_performance"]["models"]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-receipt", type=Path)
    parser.add_argument("--verify-parity", action="store_true")
    args = parser.parse_args()
    from app_config import MODELS_DIR

    pointer = MODELS_DIR / ".active_model.json"
    before = pointer.read_bytes()
    owner = None
    if args.owner_receipt:
        receipt = json.loads(args.owner_receipt.read_text(encoding="utf-8"))
        if receipt.get("verified_status") != 200 or not receipt.get("owner"):
            raise ValueError("A genuine authenticated API owner receipt is required")
        owner = receipt["owner"]
    reference.publish()
    report = {"schema": "monthly-reference-app-publication-v1", "reference_only": True,
              "purchase_authorized": False, "models": []}
    for spec in reference.specs():
        bundle = reference.load_published_bundle(MODELS_DIR / (spec["model_id"] + ".joblib"))
        predictions = pd.read_parquet(reference.verified_file(Path(spec["directory"]) / "predictions.parquet"))
        item = {"model_id": spec["model_id"], "artifact_sha256": spec["sha256"]}
        if args.verify_parity:
            item["serving_parity"] = verify_parity(spec, bundle, predictions)
        if owner:
            item["performance"] = import_test_books(spec, bundle, predictions, owner=owner)
        report["models"].append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    if pointer.read_bytes() != before:
        raise ValueError("Active model changed during reference publication")
    report["active_model_changed"] = False
    (reference.STUDY_ROOT / "app-publication.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

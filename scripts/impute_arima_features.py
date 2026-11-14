"""Materialize isolated, flagged model inputs from the audited Arima race."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

import numpy as np
import pandas as pd

from experiments.common import file_sha256, read_json
from experiments.search import freeze_json
from scraping.missingness_goal import _protected
from training.structural_imputation import FALLBACKS, FLAGS, POLICY_ID, impute_condition_history

BASE = ROOT / "reports" / "generated" / "data-quality" / "feature-gap"
STAGE = BASE / "arima-stage-20261026-v1"
CAREER = BASE / "arima-history-ajax-20261026-v1"
MANIFEST = BASE / "pedigree-reacquisition-20261026-pilot" / "dataset" / "dataset_manifest.json"


def run(root: Path) -> dict:
    root = root.resolve()
    if root.parent != BASE.resolve():
        raise ValueError("imputation-output-outside-feature-gap")
    protected = _protected()
    paths = [STAGE / name for name in (
        "race-features.csv", "verification.json", "stage-acceptance.json", "artifacts.json",
    )] + [CAREER / "verification.json", CAREER / "artifacts.json", MANIFEST]
    inputs = {str(path.relative_to(ROOT)): file_sha256(path) for path in paths}
    for directory, names in (
        (STAGE, ("race-features.csv", "verification.json")),
        (CAREER, ("verification.json",)),
    ):
        sealed = read_json(directory / "artifacts.json")
        if any(file_sha256(directory / name) != sealed[name] for name in names):
            raise ValueError("imputation-source-hash-mismatch")
    acceptance = read_json(STAGE / "stage-acceptance.json")
    stage = read_json(STAGE / "verification.json")
    career = read_json(CAREER / "verification.json")
    if not acceptance["verified"] or acceptance["unexplained_model_feature_missing_cells"] != 0:
        raise ValueError("imputation-stage-not-accepted")
    if career["errors"] or len(career["results"]) != 3:
        raise ValueError("imputation-career-audit-not-verified")
    manifest = read_json(MANIFEST)
    features = manifest["feature_sets"]["M03"]
    frame = pd.read_csv(STAGE / "race-features.csv", dtype={"horse_id": str})
    if (len(frame) != 16 or frame.horse_id.nunique() != 16 or len(features) != 72
            or list(frame.columns) != ["horse_number", "horse_id", *features]
            or stage["race_id"] != "202506050811"):
        raise ValueError("imputation-race-schema-mismatch")
    audits = {item["horse_id"]: item for item in career["results"]}
    authorized = set()
    for item in stage["remaining"]:
        audit = audits[item["horse_id"]]
        if (not audit["coverage_matches_published_career_count"]
                or audit["prior_same_condition_rows"]):
            raise ValueError("imputation-condition-gap-not-confirmed")
        indices = frame.index[frame.horse_id.eq(item["horse_id"])]
        if len(indices) != 1:
            raise ValueError("imputation-runner-not-unique")
        authorized.update((int(indices[0]), column) for column in item["columns"])
    before = frame[features]
    after, changes = impute_condition_history(before, authorized)
    if (int(before.isna().sum().sum()) != 9 or len(changes) != 9
            or after.isna().any().any()
            or not np.isfinite(after.select_dtypes(include="number").to_numpy()).all()
            or after.select_dtypes(exclude="number").eq("").any().any()):
        raise ValueError("imputation-input-completeness-failed")
    pd.testing.assert_frame_equal(before, after[features].mask(before.isna()))
    for change in changes:
        index = change["row_index"]
        change.update(horse_id=frame.at[index, "horse_id"],
                      horse_number=int(frame.at[index, "horse_number"]))
    root.mkdir()
    shutil.copyfile(STAGE / "race-features.csv", root / "observed-features.csv")
    after.to_csv(root / "model-inputs.csv", index=False)
    frame[["horse_number", "horse_id"]].to_csv(root / "runners.csv", index=False)
    freeze_json(root / "imputations.json", changes)
    freeze_json(root / "contract.json", {
        "policy_id": POLICY_ID, "research_only": True, "race_id": stage["race_id"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "value_features": features, "imputation_flags": FLAGS,
        "input_features": [*features, *FLAGS],
        "categorical_features": manifest["categorical_features"],
        "fallbacks": FALLBACKS, "fitted_population_statistics": None,
        "requires_audited_zero_condition_history": True,
        "unavailable_surface_history": "raise; no automatic zero or target-period fit",
        "training_inference_transform": "training.structural_imputation.impute_condition_history",
        "source_hashes": inputs,
        "code_hashes": {str(path.relative_to(ROOT)): file_sha256(path) for path in (
            Path(__file__), ROOT / "python-api" / "training" / "structural_imputation.py",
        )},
    })
    replay = pd.read_csv(root / "model-inputs.csv")
    pd.testing.assert_frame_equal(after, replay, check_dtype=False)
    if _protected() != protected or any(
        file_sha256(ROOT / name) != digest for name, digest in inputs.items()
    ):
        raise ValueError("imputation-protected-input-changed")
    result = {
        "verified": True, "policy_id": POLICY_ID, "research_only": True,
        "race_id": stage["race_id"], "runners": len(frame),
        "value_features": len(features), "imputation_flags": len(FLAGS),
        "observed_missing_cells": 9, "imputed_cells": len(changes),
        "model_input_missing_cells": int(replay.isna().sum().sum()),
        "observed_values_and_counts_unchanged": True, "source_artifacts_unchanged": True,
        "protected_state": protected, "production_model_activated": False,
        "historical_prestart_capture_proven": False,
        "full_period_verified": False, "full_period_acquisition_resumed": False,
        "accuracy_or_profit_improvement_proven": False,
    }
    freeze_json(root / "verification.json", result)
    freeze_json(root / "artifacts.json", {
        path.name: file_sha256(path) for path in sorted(root.iterdir()) if path.is_file()
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root), ensure_ascii=False))


if __name__ == "__main__":
    main()

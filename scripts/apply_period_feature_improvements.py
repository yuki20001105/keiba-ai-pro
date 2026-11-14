"""Apply history and backoff improvements to an isolated full-period cohort."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
from pathlib import Path
import json
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

import pandas as pd

from experiments import dataset as ds
from experiments.common import file_sha256, read_json
from experiments.search import freeze_json
from scraping.missingness_goal import _protected
from training.eligibility import select_training_eligible_rows
from training.period_quality import audit_period_features
from training.period_feature_application import (
    IDENTITY, POLICY_ID, backoff_observed_history, missing_by_year, rebuild_same_cohort,
)
from training.structural_imputation import FLAGS

BASE = ROOT / "reports" / "generated" / "data-quality" / "feature-gap"


def run(root: Path, baseline: Path) -> dict:
    root, baseline = root.resolve(), baseline.resolve(strict=True)
    if root.parent != BASE.resolve():
        raise ValueError("period-application-output-outside-feature-gap")
    source = baseline / "research.db"
    parquet = baseline / "dataset" / "dataset.parquet"
    manifest_path = baseline / "dataset" / "dataset_manifest.json"
    manifest = read_json(manifest_path)
    if Path(str(source) + "-wal").exists() and Path(str(source) + "-wal").stat().st_size:
        raise ValueError("period-application-source-has-wal")
    if source.is_symlink() or file_sha256(source) != manifest["snapshot_sha256"]:
        raise ValueError("period-application-source-hash-mismatch")
    if file_sha256(parquet) != manifest["dataset_sha256"]:
        raise ValueError("period-application-dataset-hash-mismatch")
    features = manifest["feature_sets"]["M03"]
    before = pd.read_parquet(parquet)
    today = min(datetime.now().strftime("%Y%m%d"), "20261231")
    if not before.race_date.between("20160101", today).all():
        raise ValueError("period-application-baseline-outside-period")
    protected = _protected()
    root.mkdir()
    paths = [
        Path(__file__), ROOT / "python-api" / "training" / "period_feature_application.py",
        ROOT / "python-api" / "training" / "structural_imputation.py",
        ROOT / "python-api" / "experiments" / "dataset.py",
        ROOT / "python-api" / "experiments" / "features.py",
        ROOT / "python-api" / "training" / "eligibility.py",
        ROOT / "python-api" / "training" / "period_quality.py",
        ROOT / "python-api" / "experiments" / "goal_diagnostics.py",
    ]
    freeze_json(root / "contract.json", {
        "policy_id": POLICY_ID, "research_only": True, "baseline": str(baseline),
        "source_sha256": manifest["snapshot_sha256"], "dataset_sha256": manifest["dataset_sha256"],
        "manifest_sha256": file_sha256(manifest_path), "protected": protected,
        "value_features": features, "imputation_flags": FLAGS,
        "model_input_columns": [*features, *FLAGS],
        "source_history": "Individually eligible rows before complete-runner target-cohort exclusion.",
        "target_cohort": "Identical frozen rows and order; no new target races.",
        "history_boundary": "source_race_date < target_race_date; same-day results excluded",
        "history_start": manifest["scope"]["history_start"],
        "backoff": "Same horse's strictly prior surface history; no fitted population statistics.",
        "public_career_completeness_verified": False,
        "no_prior_stored_condition_is_not_proof_of_public_first_condition": True,
        "not_the_source_audited_single_race_imputation_contract": True,
        "unresolved_features": "Retain NaN and reasons; never zero-fill.",
        "code_hashes": {str(path.relative_to(ROOT)): file_sha256(path) for path in paths},
    })
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        compact, quality, _, source_audit = ds._load_compact(db)
    compact = compact.loc[compact.race_date.le(today)].reset_index(drop=True)
    selection = select_training_eligible_rows(
        compact, target="speed_deviation", recorded_quality_states=quality,
    )
    history = selection.frame.reset_index(drop=True)
    freeze_json(root / "history-selection.json", {
        "source_audit": source_audit, "eligibility": selection.manifest.as_dict(),
        "target_complete_race_filter_applied_to_history": False,
        "history_rows": len(history), "history_races": int(history.race_id.nunique()),
    })
    history.to_parquet(root / "history-observations.parquet", index=False, compression="zstd")
    del compact, selection
    print(json.dumps({"stage": "history_selected", "rows": len(history)}), flush=True)
    rebuilt, history_audit = rebuild_same_cohort(before, history, features)
    print(json.dumps({"stage": "history_rebuilt",
                      "missing": int(rebuilt[features].isna().sum().sum())}), flush=True)
    after, journal = backoff_observed_history(rebuilt)
    pd.testing.assert_frame_equal(before[IDENTITY], after[IDENTITY])
    if int(rebuilt[features].isna().sum().sum() - after[features].isna().sum().sum()) != len(journal):
        raise ValueError("period-application-journal-count-mismatch")
    rebuilt.to_parquet(root / "history-rebuilt.parquet", index=False, compression="zstd")
    after.to_parquet(root / "applied-dataset.parquet", index=False, compression="zstd")
    after[[*features, *FLAGS]].to_parquet(root / "model-inputs.parquet", index=False, compression="zstd")
    journal.to_parquet(root / "imputations.parquet", index=False, compression="zstd")
    summary = {
        "before": missing_by_year(before, features),
        "history_rebuilt": missing_by_year(rebuilt, features),
        "after": missing_by_year(after, features),
    }
    freeze_json(root / "yearly-missingness.json", summary)
    freeze_json(root / "feature-changes.json", {
        column: {
            "restored_from_history": int((before[column].isna() & rebuilt[column].notna()).sum()),
            "newly_missing_after_history_change": int((before[column].notna() & rebuilt[column].isna()).sum()),
            "changed_existing_derived_values": int((
                before[column].notna() & rebuilt[column].notna()
                & ~before[column].eq(rebuilt[column]).fillna(False)
            ).sum()),
            "estimated_by_backoff": int((rebuilt[column].isna() & after[column].notna()).sum()),
        }
        for column in features
    })
    remaining = audit_period_features(
        after, features, manifest["categorical_features"], start_year=2016, end_date=today,
    )
    remaining.update({
        "imputation_applied": True, "imputation_policy_id": POLICY_ID,
        "public_career_completeness_verified": False,
    })
    freeze_json(root / "remaining-missingness.json", remaining)
    freeze_json(root / "history-point-in-time-audit.json", history_audit)
    if (file_sha256(source) != manifest["snapshot_sha256"]
            or file_sha256(parquet) != manifest["dataset_sha256"] or _protected() != protected):
        raise ValueError("period-application-input-or-production-changed")
    replay = pd.read_parquet(root / "applied-dataset.parquet")
    pd.testing.assert_frame_equal(after, replay)
    result = {
        "verified": True, "policy_id": POLICY_ID, "research_only": True,
        "races": int(before.race_id.nunique()), "rows": len(before), "value_features": len(features),
        "observed_date_from": str(before.race_date.min()), "observed_date_to": str(before.race_date.max()),
        "before_missing_cells": int(before[features].isna().sum().sum()),
        "after_history_rebuild_missing_cells": int(rebuilt[features].isna().sum().sum()),
        "after_backoff_missing_cells": int(after[features].isna().sum().sum()),
        "imputed_cells": len(journal), "imputation_flags": len(FLAGS),
        "same_target_cohort_and_base_observations": True,
        "history_definition_changed": True, "public_career_completeness_verified": False,
        "all_period_zero_missing": not after[features].isna().any().any(),
        "production_model_or_raw_db_changed": False, "network_requests": 0,
    }
    freeze_json(root / "verification.json", result)
    freeze_json(root / "artifacts.json", {
        path.name: file_sha256(path) for path in root.iterdir() if path.is_file()
    })
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=BASE / "pedigree-reacquisition-20261026-pilot")
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.baseline), ensure_ascii=False))

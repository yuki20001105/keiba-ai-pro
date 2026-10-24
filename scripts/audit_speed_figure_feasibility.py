"""Fail-closed numeric feasibility audit, not a substitute Speed Figure model."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "python-api"), str(ROOT / "keiba")]
for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[key] = "2"
import pandas as pd
from snapshot_feature_pit_audit import protected, write
from verify_market_segments_outputs import require, read, sha256

OUT = ROOT / "reports/generated/model-experiments/speed-figure-pit-v1"
SOURCE = ROOT / "reports/generated/data-quality/feature-pit-source-v1"
BASE = ["sf_max", "sf_avg_5", "sf_dist_max", "sf_course_max", "sf_last", "sf_2ago", "sf_3ago"]
DERIVED = ["sf_last_minus_avg5", "sf_last_minus_3ago", "sf_max_minus_avg5", "sf_dist_max_minus_max",
           "sf_course_max_minus_max", "sf_recent_std", "sf_recent_slope"]
INDEX = ["max_index", "avg_5_index", "dist_max_index", "course_max_index", "index_last", "index_2ago", "index_3ago"]


def dates(values):
    text = values.astype("string").str.strip()
    return pd.to_datetime(text.where(text.str.fullmatch(r"\d{8}", na=False)), format="%Y%m%d", errors="coerce")


def profile(races, runners, speed):
    require(not races.race_id.duplicated().any(), "ambiguous race key")
    race = races[["race_id", "race_date"]].copy()
    race["parsed_date"] = dates(race.race_date)
    joined = runners.merge(race, on="race_id", how="left", validate="many_to_one", indicator=True)
    joined["year"] = joined.parsed_date.dt.year
    stored = speed.merge(race, on="race_id", how="left", validate="many_to_one", indicator=True)
    stored["year"] = stored.parsed_date.dt.year
    # This is coverage of stored external records, never proof of historical availability.
    keys = speed[["race_id", "horse_number"]].drop_duplicates()
    presence = joined.merge(keys.assign(external_present=True), on=["race_id", "horse_number"], how="left", validate="many_to_one")
    rows = []
    for year in range(2013, 2023):
        part = presence.loc[presence.year.eq(year)]
        ext = stored.loc[stored.year.eq(year)]
        rows.append({"year": year, "rows": len(part), "races": int(part.race_id.nunique()),
            "available": None, "missing": None, "invalid": None, "coverage_percent": None,
            "required_raw_fields_status": "NOT_DETERMINABLE_FORMULA_UNAVAILABLE",
            "reconstructed_rows": 0, "reconstruction_coverage_percent": 0.0 if len(part) else None,
            "stored_external_records": len(ext), "stored_external_races": int(ext.race_id.nunique()),
            "runner_rows_with_external_key": int(part.external_present.eq(True).sum()),
            "historical_external_values_used": False})
    comparable = stored.loc[stored.year.eq(2026)]
    metrics = []
    for col in INDEX:
        values = pd.to_numeric(comparable[col], errors="coerce")
        missing = comparable[col].isna()
        invalid = (~missing & (values.isna() | values.mod(1).ne(0) | ~values.abs().lt(float("inf"))))
        metrics.append({"field": col, "stored_2026_rows": len(comparable), "stored_available": int((~missing & ~invalid).sum()),
            "stored_missing": int(missing.sum()), "stored_invalid": int(invalid.sum()), "comparison_pairs": 0,
            "exact_match_count": None, "exact_match_rate": None, "MAE": None, "RMSE": None,
            "correlation": None, "max_absolute_difference": None,
            "status": "NOT_COMPARABLE_FORMULA_UNAVAILABLE", "cause": "hidden_external_value",
            "cause_scope": "external values observed; not an empirical mismatch diagnosis"})
    summary = {"runner_rows": len(runners), "race_rows": len(races), "speed_rows": len(speed),
        "runner_race_join_missing": int(joined._merge.ne("both").sum()),
        "runner_date_unknown": int(joined.parsed_date.isna().sum()),
        "speed_race_join_missing": int(stored._merge.ne("both").sum()),
        "speed_date_unknown": int(stored.parsed_date.isna().sum()),
        "stored_2026_rows": len(comparable), "stored_2026_races": int(comparable.race_id.nunique()),
        "speed_duplicate_race_horse_keys": int(speed.duplicated(["race_id", "horse_number"], keep=False).sum()),
        "created_at_min": str(speed.created_at.min()), "created_at_max": str(speed.created_at.max()),
        "known_race_date_min": str(stored.parsed_date.min().date()), "known_race_date_max": str(stored.parsed_date.max().date())}
    return pd.DataFrame(rows), pd.DataFrame(metrics), summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--formula-sha256", required=True)
    args = parser.parse_args()
    formula = OUT / "speed_figure_formula_audit.json"
    require(sha256(formula) == args.formula_sha256, "provenance audit hash mismatch")
    audit = read(formula)
    # Auditor declares availability explicitly, not by interpreting a narrative.
    require(audit.get("RECONSTRUCTION_VALIDATED") is False, "this script is only for the no-formula branch")
    outputs = ["feasibility-plan.json", "historical_raw_coverage.csv", "current_2026_reproducibility.csv", "feasibility-result.json",
               "feature_candidates.json", "model_evaluation_status.csv", "per_run_speed_figures.parquet"]
    require(not any((OUT / name).exists() for name in outputs), "prior audit output exists; no overwrite")
    snap = read(SOURCE / "snapshot-manifest.json")
    db_path = Path(snap["snapshot"]["path"])
    require(sha256(db_path) == snap["snapshot"]["sha256"], "frozen snapshot changed")
    before = protected()
    write(OUT / outputs[0], {"created_at": datetime.now(timezone.utc).isoformat(), "formula_sha256": args.formula_sha256,
        "snapshot_sha256": snap["snapshot"]["sha256"], "code_sha256": sha256(Path(__file__)),
        "frozen_features": BASE + DERIVED, "performance_outcomes": "not read", "formula_unavailable_action": "do not reconstruct or fit",
        "required_raw_fields": None, "no_surrogate_formula": True, "comparison_2026_only": True,
        "historical_join_is_inventory_only": True, "protected_before": before})
    with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        races = pd.read_sql_query("SELECT race_id,race_date FROM races_ultimate", db)
        runners = pd.read_sql_query("SELECT source_row_id,race_id,horse_id,horse_number FROM race_results_ultimate", db)
        speed = pd.read_sql_query("SELECT * FROM speed_figures", db)
    yearly, match, summary = profile(races, runners, speed)
    yearly.to_csv(OUT / "historical_raw_coverage.csv", index=False)
    match.to_csv(OUT / "current_2026_reproducibility.csv", index=False)
    candidates = {"schema": "requested-speed-features-gated-v1", "base": BASE, "derived": DERIVED,
        "all_status": "BLOCKED_FORMULA_UNAVAILABLE", "model_input_eligible": False,
        "semantic_limit": "external aggregate window/distance/course/surface semantics are not proven; do not define a replacement from column names",
        "derived_std_slope_window": "NOT_IMPLEMENTED_PENDING_BASE_DEFINITION; cannot be selected against results",
        "missing_reasons": ["no_history", "insufficient_history", "raw_field_missing", "condition_history_missing", "formula_unavailable"],
        "current_blocking_reason": "formula_unavailable", "zero_imputation": False,
        "future_PIT_contract": "history_race_date < target_race_date; no unproven same-day order; historical-only standards",
        "feature_selection_or_fits_performed": False}
    write(OUT / "feature_candidates.json", candidates)
    statuses = [{"model": name, "status": "NOT_RUN_RECONSTRUCTION_GATE_FAILED", "NLL": None, "Brier": None,
                 "Spearman": None, "Top1": None, "selection_calibration": None, "delta_NLL": None, "delta_Brier": None,
                 "bootstrap_95_CI": None} for name in ["Market Only", "Market+H04", "Market+Speed", "Market+H04+Speed", "H04+Speed"]]
    pd.DataFrame(statuses).to_csv(OUT / "model_evaluation_status.csv", index=False)
    pd.DataFrame({name: pd.Series(dtype="float64" if name == "speed_figure" else "string") for name in
        ["horse_id", "race_id", "race_date", "speed_figure", "calculation_version", "source_hash", "missing_reason"]}).to_parquet(OUT / "per_run_speed_figures.parquet", index=False)
    require(sha256(db_path) == snap["snapshot"]["sha256"], "source mutated")
    summary.update(RECONSTRUCTION_VALIDATED=False, point_in_time_reconstruction="NO", formula_sha256=args.formula_sha256,
        required_raw_fields=None, coverage_not_computable_reason="formal_required_raw_field_set_unknown",
        calculated_per_run_rows=0, current_comparison_pairs=0, new_model_fits=0, market_edge="INCONCLUSIVE",
        h04_retraining_candidate="NO", protected_after=protected())
    write(OUT / "feasibility-result.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "protected_after"}, indent=2))


if __name__ == "__main__":
    main()

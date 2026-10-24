"""Phase 1 only: reconcile target data, freeze clean cohort, and fail-closed gate."""
from pathlib import Path
from datetime import datetime, timezone
import json
import sys
import os

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]
import pandas as pd
from market_quality.integrity import audit_integrity
from market_quality.lineage import sha256
from market_quality.observations import audit_odds, audit_calibration_split
from market_edge.data import _source
from market_edge.compute import CollectorMonitor

QUALITY = ROOT / "reports/generated/data-quality" / os.environ.get("MARKET_QUALITY_REVISION", "market-h04-clean-v1")
if QUALITY.resolve().parent != (ROOT / "reports/generated/data-quality").resolve():
    raise ValueError("Quality revision must be one isolated directory name")
OLD = ROOT / "reports/generated/model-experiments/market-edge-v1"
SEARCH = ROOT / "reports/generated/model-experiments/20260919-feature-search-v1"
TRACK = ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def csv_new(frame, name):
    with (QUALITY / name).open("x", encoding="utf-8", newline="") as stream:
        frame.to_csv(stream, index=False)


def protected():
    manifest = read(OLD / "artifact_manifest.json")
    failures = []
    for name, digest in manifest["files"].items():
        if sha256(OLD / name) != digest:
            failures.append("old artifact changed:" + name)
    supplement = read(OLD / "supplement_manifest.json")
    # This later manifest covers the prior result review and Japanese report.
    supplementary = supplement["repo_relative_files"]
    for name, item in supplementary.items():
        digest = item.get("sha256") if isinstance(item, dict) else item
        if sha256(ROOT / name) != digest:
            failures.append("old supplement changed:" + name)
    freeze = read(OLD / "research_freeze.json")
    for name, digest in freeze["source_sha256"].items():
        if sha256(ROOT / name) != digest:
            failures.append("old code changed:" + name)
    if sha256(ROOT / "python-api/models/.active_model.json") != freeze["active_model_sha256"]:
        failures.append("active model changed")
    if sha256(TRACK / "protocol.json") != freeze["track_a_protocol_file_sha256"]:
        failures.append("Track A protocol changed")
    health = CollectorMonitor(TRACK).snapshot()
    if not health.get("healthy"):
        failures.append("collector health requires review:" + str(health))
    return {"passed": not failures, "issues": failures, "original_artifacts_checked": len(manifest["files"]),
            "supplement_files_checked": len(supplementary), "active_model_sha256": freeze["active_model_sha256"],
            "protocol_sha256": freeze["track_a_protocol_file_sha256"], "collector": health}


def main():
    if (QUALITY / "quality_summary.json").exists():
        raise ValueError("Final quality gate already frozen; use a new experiment")
    plan = read(QUALITY / "audit_plan.json")
    cohort_path = OLD / "development.parquet"
    if sha256(cohort_path) != plan["original_cohort_sha256"]:
        raise ValueError("Original cohort hash changed")
    protection_start = protected()
    if not protection_start["passed"]:
        raise ValueError(str(protection_start["issues"]))
    cohort = pd.read_parquet(cohort_path, use_threads=False)
    result, oof_path, source = _source(SEARCH)
    oof = pd.read_parquet(oof_path, use_threads=False)
    snapshot = ROOT / f"reports/generated/model-experiments/20260919-jra-safe-v1/snapshots/{plan['snapshot_sha256']}.db"
    if sha256(snapshot) != plan["snapshot_sha256"]:
        raise ValueError("Immutable snapshot hash changed")
    runners, payouts, integrity_receipt = audit_integrity(snapshot, cohort, oof)
    csv_new(runners, "runner_integrity.csv")
    csv_new(payouts, "payout_audit.csv")
    write(QUALITY / "integrity_source_audit.json", integrity_receipt)
    odds, odds_summary = audit_odds(cohort, {
        "historical_source_verified": True, "source_kind": "historical_result_sqlite",
        "source_sha256": plan["snapshot_sha256"],
        "evidence_ref": "integrity_source_audit.json:raw_vs_saved_value_reconciliation;source_snapshot_sha256=" + plan["snapshot_sha256"]})
    # C classification only proves the type of source; conflicts still exclude the race below.
    csv_new(odds, "odds_quality.csv")
    write(QUALITY / "odds_summary.json", odds_summary)
    feature_specs = {path.parent.name: read(path)["features"] for path in (OLD / "jobs").glob("*/model.json")}
    calibration = audit_calibration_split(cohort, read(OLD / "seed-calibrator.json"), read(OLD / "provenance.json"), feature_specs)
    write(QUALITY / "calibration_audit.json", calibration)
    lineage = read(QUALITY / "lineage.json")
    worker_result = read(ROOT / "reports/generated/model-experiments/market-quality-audit-v1/lineage-job-result.json")
    if (worker_result["status"] != "completed" or worker_result["lineage_sha256"] != sha256(QUALITY / "lineage.json")
            or lineage["audit_plan_sha256"] != sha256(QUALITY / "audit_plan.json")
            or lineage["source_snapshot_sha256"] != plan["snapshot_sha256"]):
        raise ValueError("Lineage worker result/source/plan hash binding failed")
    pit = lineage.get("point_in_time") or {}
    exact = (pit.get("features_checked") == 99 and len(pit.get("per_feature", [])) == 99
        and {x["feature"] for x in pit["per_feature"]} == set(lineage["feature_columns"])
        and all(x["mismatched_rows"] == 0 and x.get("max_absolute_difference") in (None, 0) for x in pit["per_feature"]))
    leakage = [
        {"check": "strict_prior_event_history_and_no_same_day_updates", "pass": pit.get("actual_event_replay") is True and pit.get("same_day_future_references") == 0 and pit.get("future_leakage_rows") == 0, "violations": pit.get("future_leakage_rows"), "evidence": "lineage.json + frozen features.py day-batch source + exact99 replay"},
        {"check": "all_99_features_exact_including_training_rows", "pass": exact, "violations": pit.get("mismatched_feature_rows"), "evidence": "lineage.json;298654 supervised rows, source history through2022 only"},
        {"check": "no_profile_totals_scraped_prev_current_results_or_odds_as_H04_inputs", "pass": lineage.get("point_in_time_pass") is True and len(lineage.get("verified_source_modules", {})) > 0, "violations": 0 if lineage.get("point_in_time_pass") else None, "evidence": "verified compact loader whitelist + feature allowlist + actual event-history rebuild"},
        {"check": "year_excluded_saved_models_exact_score_replay", "pass": lineage.get("reinference_pass") is True, "violations": sum(x["mismatched_scores"] for x in lineage.get("reinference", [])), "evidence": "lineage.json;2021 cutoff2020 and2022 cutoff2021"},
        {"check": "fixed_calibration_no_cross_use", "pass": calibration["passed"], "violations": len(calibration["issues"]), "evidence": "calibration_audit.json"},
    ]
    leakage_frame = pd.DataFrame(leakage)
    csv_new(leakage_frame, "leakage_audit.csv")
    acquisition_summary = read(QUALITY / "acquisition/acquisition_summary.json")
    for artifact in acquisition_summary["artifacts"].values():
        if sha256(artifact["path"]) != artifact["sha256"]:
            raise ValueError("Acquisition audit artifact changed")
    acquisition = pd.read_csv(QUALITY / "acquisition/acquisition_audit.csv", dtype={"race_id": str, "race_date": str})
    missing_manifest = read(QUALITY / "missingness/artifact_manifest.json")
    for name, value in missing_manifest.items():
        digest = value.get("sha256") if isinstance(value, dict) else value
        if sha256(QUALITY / "missingness" / name) != digest:
            raise ValueError("Missingness audit changed")
    missingness = pd.read_csv(QUALITY / "missingness/missingness_report.csv")
    csv_new(missingness, "missingness_report.csv")
    missing_summary = read(QUALITY / "missingness/missingness_summary.json")
    global_checks = {
        "original_scope_exact": len(cohort) == 84128 and cohort.race_id.nunique() == 6109,
        "lineage": lineage.get("lineage_pass") is True,
        "OOF_reinference": lineage.get("reinference_pass") is True,
        "PIT_and_exact_features": bool(leakage_frame["pass"].all()),
        "calibration_split": calibration["passed"],
        "odds_classification_complete": bool(odds_summary["classification_complete"]),
        "missing_safe_inputs": missing_summary["H04_invalid_cells"] == 0,
    }
    global_blockers = [name for name, passed in global_checks.items() if not passed]
    race_odds = odds.groupby("race_id").agg(odds_invalid=("invalid_odds", "any"), odds_unknown=("odds_quality", lambda x: x.eq("D").any()), market_mismatch=("market_probability_mismatch", "any"), implied_mismatch=("raw_implied_mismatch", "any"))
    race = acquisition.merge(runners, on="race_id", validate="one_to_one").merge(payouts, on="race_id", validate="one_to_one", suffixes=("_runner", "_payout")).merge(race_odds, on="race_id", validate="one_to_one")
    if len(race) != 6109:
        raise ValueError("Missing race-level audit rows")
    classifications, reasons, accepted = [], [], []
    for row in race.to_dict("records"):
        issues = []
        for flag, name in [("runner_pass", "runner_conflict"), ("payout_pass", "payout_conflict"), ("acquisition_pass", "acquisition_incomplete")]:
            if not row[flag]:
                issues.append(name)
        if any(row[k] for k in ("odds_invalid", "odds_unknown", "market_mismatch", "implied_mismatch")):
            issues.append("odds_invalid_unverified_or_mismatched")
        if "runner_conflict" in issues or "payout_conflict" in issues or "odds_invalid_unverified_or_mismatched" in issues:
            grade = "E CONFLICT"
        elif row["excluded_from_standard"]:
            grade = "F EXCLUDED"
        elif not row["acquisition_pass"]:
            grade = "C PARTIAL" if row.get("quality_status") == "partial" else "D INCOMPLETE"
        elif global_blockers:
            grade = "F EXCLUDED"
        else:
            grade = "B CLEAN_WITH_ALLOWED_MISSING" if json.loads(row["allowed_missing_json"]) else "A CLEAN"
        classifications.append(grade)
        reasons.append(json.dumps(issues + global_blockers, ensure_ascii=False))
        accepted.append(grade.startswith(("A ", "B ")))
    race["quality_class"] = classifications
    race["exclusion_reasons_json"] = reasons
    race["included_in_clean"] = accepted
    csv_new(race, "race_quality_audit.csv")
    clean_races = race.loc[race.included_in_clean].copy()
    excluded = race.loc[~race.included_in_clean].copy()
    csv_new(clean_races, "clean_races.csv")
    csv_new(excluded, "excluded_races.csv")
    clean = cohort.loc[cohort.race_id.isin(clean_races.race_id)].copy()
    clean_path = QUALITY / "clean_runners.parquet"
    if clean_path.exists():
        raise ValueError("Clean cohort already exists")
    clean.to_parquet(clean_path, index=False)
    protection_end = protected()
    write(QUALITY / "protected_state_audit.json", {"before": protection_start, "after": protection_end})
    global_checks["protected_unchanged"] = protection_end["passed"]
    global_checks["clean_cohort_nonempty"] = len(clean_races) > 0
    global_checks["whole_race_exclusions_explicit"] = len(clean_races) + len(excluded) == len(race)
    summary = {"schema": "market-h04-data-quality-v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "MODEL_RESEARCH_READY": all(global_checks.values()), "gate_checks": global_checks,
        "blockers": [name for name, passed in global_checks.items() if not passed],
        "original_races": 6109, "original_runners": 84128, "clean_races": len(clean_races),
        "clean_runners": len(clean), "excluded_races": len(excluded), "classification": race.quality_class.value_counts().to_dict(),
        "odds_quality": odds_summary["classification"], "integrity": integrity_receipt,
        "leakage_count": int(sum(row["violations"] or 0 for row in leakage)),
        "point_in_time_scope": "historical event-date safety; not contemporaneous T-5 availability",
        "incomplete_race_count": int((~race.acquisition_pass).sum()), "legacy_conflict_races": int(race.legacy_conflict.sum()),
        "data_quality_effect": {"cohort_changed": len(clean) != len(cohort), "recalculation_required": len(clean) != len(cohort),
            "note": "Same input keys and values: no recomputation or model refitting required" if len(clean) == len(cohort) else "Reapply fixed predictions and rules only; no training/tuning; data quality effect, not model improvement"},
        "clean_runners_sha256": sha256(clean_path), "clean_races_sha256": sha256(QUALITY / "clean_races.csv"),
        "audit_plan_sha256": sha256(QUALITY / "audit_plan.json"),
        "new_model_training_performed": False, "post_2022_outcomes_read": False,
        "supplemental_training_and_speed_asof_verified": False,
        "phase2_permission": "Only on MODEL_RESEARCH_READY=true; new independent predeclared plan required"}
    write(QUALITY / "quality_summary.json", summary)
    manifest = {"schema": "market-h04-quality-artifacts-v1", "files": {str(path.relative_to(QUALITY)): sha256(path) for path in sorted(QUALITY.rglob("*")) if path.is_file()},
        "code": {str(path.relative_to(ROOT)): sha256(path) for path in [*sorted((ROOT / "python-api/market_quality").glob("*.py")), Path(__file__), ROOT / "scripts/run_market_quality_replay.py", ROOT / "scripts/run_market_edge_quality_worker.py", ROOT / "scripts/audit_market_missingness.py"]}}
    write(QUALITY / "artifact_manifest.json", manifest)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

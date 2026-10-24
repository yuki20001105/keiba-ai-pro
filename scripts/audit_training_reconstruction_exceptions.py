"""Re-audit training exceptions and event-valid coverage without repairing data.

Only label-free immutable source projections are read. Event validity is never
promoted to publication/capture proof or permission to use a model feature.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "scripts"), str(ROOT / "keiba")]
import pandas as pd

from audit_training_data_provenance import load_source
from feature_provenance.training import _join, _coverage, dates, name_key, number
from verify_market_segments_outputs import sha256, require, read, verify_files
from snapshot_feature_pit_audit import protected

AUDIT = ROOT / "reports/generated/model-experiments/training-data-pit-audit-v1"
COHORT = ROOT / "reports/generated/data-quality/market-h04-clean-v2/clean_runners.parquet"
OUTPUT_NAMES = ("post_race_date_anomalies.csv", "unmatched_runner_records.csv",
                "yearly_coverage_after_reconstruction.csv", "exceptions-result.json")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _plain(value):
    if value is None or pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def build_artifacts(training, races, runners, cohort, snapshot_sha):
    """Return new tables and counts; never mutate the input frames."""
    require(not training.empty and not training.id.duplicated().any(), "unique training IDs required")
    require(not cohort.duplicated(["race_id", "horse_number"]).any(), "cohort duplicate runner key")
    cohort_dates = dates(cohort.race_date)
    require(cohort_dates.notna().all() and cohort_dates.dt.year.isin([2021, 2022]).all(), "cohort must be 2021-2022")
    data = training.reset_index(drop=True).copy()
    data["race_id"] = data.race_id.astype(str)
    joined, race_join, _ = _join(data, races, runners, pd.DataFrame())
    require(joined.training_id.equals(data.id.rename("training_id")), "record alignment changed")
    event, target = dates(data.training_date), dates(joined.race_date)
    joined["event_date_class"] = "D_DATE_OR_RACE_UNKNOWN"
    joined.loc[event.lt(target), "event_date_class"] = "A_BEFORE_RACE"
    joined.loc[event.eq(target), "event_date_class"] = "B_SAME_DATE_TIME_UNKNOWN"
    joined.loc[event.gt(target), "event_date_class"] = "C_AFTER_RACE"
    after = joined.event_date_class.eq("C_AFTER_RACE")
    unmatched = ~joined.identity_verified
    valid = joined.identity_verified & joined.event_date_class.eq("A_BEFORE_RACE")
    exception_ids = set(joined.loc[after | unmatched, "race_id"])
    selected_runners = runners.loc[runners.race_id.astype(str).isin(exception_ids)].copy()
    selected_runners["number_key"] = selected_runners.horse_number.map(number)
    rosters = {str(rid): part for rid, part in selected_runners.groupby("race_id", sort=False)}
    headers = {str(rid): part for rid, part in races.loc[races.race_id.astype(str).isin(exception_ids)].groupby("race_id", sort=False)}
    all_name_counts = runners.horse_name.map(name_key).value_counts().to_dict()
    cohort_keys = set(zip(cohort.race_id.astype(str), cohort.horse_number.map(number)))

    def records(mask, reason):
        rows = []
        for pos in joined.index[mask]:
            original = {str(k): _plain(v) for k, v in data.iloc[pos].items()}
            link = joined.iloc[pos]
            rid = str(link.race_id)
            roster = rosters.get(rid, selected_runners.iloc[:0])
            header = headers.get(rid, races.iloc[:0])
            canonical = header.iloc[0] if len(header) == 1 else None
            declared = number(canonical.num_horses) if canonical is not None else None
            stored_numbers = sorted(set(int(n) for n in roster.number_key.dropna()))
            roster_keys = [{"source_row_id": _plain(row.source_row_id), "horse_id": _plain(row.horse_id),
                "horse_number": _plain(row.horse_number), "horse_name": _plain(row.horse_name),
                "inner_race_id": _plain(row.inner_race_id), "json_valid": _plain(row.data_json_valid)}
                for row in roster.itertuples(index=False)]
            rows.append({**original, "race_date": _plain(link.race_date), "training_date_validated":
                None if pd.isna(event.iloc[pos]) else event.iloc[pos].strftime("%Y%m%d"),
                "days_after_race": None if pd.isna(event.iloc[pos]) or pd.isna(target.iloc[pos]) else int((event.iloc[pos] - target.iloc[pos]).days),
                "race_join_status": link.race_join_status, "runner_join_status": link.runner_join_status,
                "identity_verified": bool(link.identity_verified), "resolved_horse_id": _plain(link.resolved_horse_id),
                "event_date_class": link.event_date_class, "number_match_rows": int(link.number_match_rows),
                "name_match_rows": int(link.name_match_rows), "same_name_source_runner_rows_all_races":
                    int(all_name_counts.get(name_key(original["horse_name"]), 0)),
                "canonical_race_header_rows": len(header), "canonical_race_date_raw": _plain(canonical.race_date) if canonical is not None else None,
                "canonical_race_name": _plain(canonical.race_name) if canonical is not None else None,
                "canonical_source_host": _plain(canonical.source_host) if canonical is not None else None,
                "canonical_declared_runner_count": declared, "canonical_runner_rows": len(roster),
                "canonical_distinct_runner_numbers": len(stored_numbers),
                "canonical_stored_numbers_json": _json(stored_numbers), "canonical_roster_identity_only_json": _json(roster_keys),
                "header_count_exceeds_stored_unique_numbers": declared > len(stored_numbers) if declared is not None else None,
                "in_market_clean_2021_2022": (rid, number(original["horse_number"])) in cohort_keys,
                "quarantine": True, "quarantine_reason": reason, "cause": "UNKNOWN_WITHOUT_ORIGINAL_SOURCE",
                "year_error_verified": False, "month_error_verified": False, "parser_error_verified": False,
                "source_display_error_verified": False, "other_race_contamination_verified": False,
                "cancellation_verified": False, "exclusion_verified": False, "renumbering_verified": False,
                "result_row_absent_from_projection": int(link.number_match_rows) == 0,
                "missing_result_cause_verified": False,
                "raw_original_status": "NOT_PROVIDED_OR_VERIFIED_FOR_THIS_RECORD",
                "source_url_template_only": "https://race.netkeiba.com/race/oikiri.html?race_id=" + rid,
                "source_url_verified_for_record": False, "source_snapshot_sha256": snapshot_sha,
                "source_record_sha256": hashlib.sha256(_json(original).encode()).hexdigest(),
                "retrieved_at": None, "created_at_is_capture_time": False,
                "strict_pit": False, "model_input_permitted": False, "correction_applied": False,
                "next_evidence_needed": "original race-bound training HTML and identity/date headers; capture/version evidence"})
        # Schema is stable even when a synthetic fixture has no exceptions.
        if not rows:
            return pd.DataFrame(columns=[*data.columns, "race_date", "quarantine", "quarantine_reason", "cause"])
        return pd.DataFrame(rows).sort_values(["race_date", "race_id", "horse_number", "id"], kind="stable")

    _, race_coverage = _coverage(races, runners, data, joined, {"market_clean_2021_2022": cohort})
    coverage = []
    for scope in ["all_canonical", "market_clean_2021_2022"]:
        for year in range(2013, 2027):
            part = race_coverage.loc[race_coverage.scope.eq(scope) & race_coverage.year.eq(year)]
            known = part.loc[part.runners.notna()]
            denominator = int(known.runners.sum())
            item = {"scope": scope, "year": year, "total_races": len(part),
                "known_runner_key_rows": int(part.known_runner_keys.sum()),
                "denominator_runner_count": denominator, "denominator_unknown_races": int(part.runners.isna().sum()),
                "complete_saved_runner_races": int(part.known_runner_keys.eq(part.runners).sum()),
                "denominator_policy": "all canonical: validated declared count >= stored unique keys; market: frozen complete cohort",
                "data_reconstructed": False, "records_repaired": 0,
                "event_valid_is_feature_permission": False, "distance_mapping_verified": False,
                "model_input_gate": "NO", "strict_pit_runner_count": 0, "strict_pit_races": 0,
                "strict_pit_ge50pct_races": 0, "strict_pit_ge80pct_races": 0, "strict_pit_100pct_races": 0,
                "strict_pit_runner_coverage_percent": 0. if denominator else None}
            for prefix, column in [("stored_presence", "training_runners"), ("event_valid_identity", "prior_event_runners")]:
                ratio = known[column] / known.runners
                item.update({prefix + "_races": int(part[column].gt(0).sum()),
                    prefix + "_runner_count": int(part[column].sum()),
                    prefix + "_known_denominator_runner_count": int(known[column].sum()),
                    prefix + "_runner_coverage_percent": float(known[column].sum() / denominator * 100) if denominator else None,
                    prefix + "_ge50pct_races": int(ratio.ge(.5).sum()), prefix + "_ge80pct_races": int(ratio.ge(.8).sum()),
                    prefix + "_100pct_races": int(ratio.eq(1).sum())})
            coverage.append(item)
    summary = {"source_training_rows": len(data), "source_races": int(data.race_id.nunique()),
        "post_race_date_records": int(after.sum()), "unmatched_runner_records": int(unmatched.sum()),
        "exception_union_records": int((after | unmatched).sum()), "exception_overlap_records": int((after & unmatched).sum()),
        "event_date_before_records": int(joined.event_date_class.eq("A_BEFORE_RACE").sum()),
        "event_valid_identity_records": int(valid.sum()), "strict_pit_records": 0,
        "original_raw_evidence_attached": 0, "causes_confirmed": 0, "corrections_applied": 0,
        "market_cohort_races": int(cohort.race_id.nunique()), "market_cohort_runners": len(cohort),
        "market_exception_records": int(sum((rid, number(num)) in cohort_keys for rid, num in
            zip(joined.loc[after | unmatched, "race_id"], joined.loc[after | unmatched, "horse_number"]))),
        "coverage_is_existing_inventory_not_recovered_data": True, "model_input_gate": "NO"}
    return {OUTPUT_NAMES[0]: records(after, "AFTER_RACE_DATE"),
            OUTPUT_NAMES[1]: records(unmatched, "RUNNER_IDENTITY_UNVERIFIED"),
            OUTPUT_NAMES[2]: pd.DataFrame(coverage)}, summary


def execute(snapshot, expected_sha, output):
    snapshot, output = Path(snapshot).resolve(strict=True), Path(output).resolve()
    require(sha256(snapshot) == expected_sha, "snapshot SHA mismatch")
    prior_result = read(AUDIT / "training-audit-result.json")
    require(prior_result["snapshot_sha256"] == expected_sha, "old audit source mismatch")
    verify_files(ROOT, prior_result["source_sha256"])
    verify_files(AUDIT, prior_result["files"])
    old_plan = read(AUDIT / "audit_plan.json")
    require(sha256(COHORT) == old_plan["cohorts"]["released_clean"]["sha256"], "cohort SHA mismatch")
    output.mkdir(parents=True, exist_ok=True)
    require(not any((output / name).exists() for name in OUTPUT_NAMES), "exclusive outputs already exist")
    before = protected()
    source_hash = sha256(Path(__file__))
    training, races, runners, _, _ = load_source(snapshot)
    cohort = pd.read_parquet(COHORT, columns=["race_id", "race_date", "horse_number", "horse_id"],
        filters=[("race_date", ">=", "20210101"), ("race_date", "<=", "20221231")], use_threads=False)
    tables, counts = build_artifacts(training, races, runners, cohort, expected_sha)
    require(sha256(snapshot) == expected_sha and sha256(Path(__file__)) == source_hash, "source changed during audit")
    verify_files(ROOT, prior_result["source_sha256"])
    require(sha256(COHORT) == old_plan["cohorts"]["released_clean"]["sha256"], "cohort changed during audit")
    after = protected()
    for filename, frame in tables.items():
        frame.to_csv(output / filename, index=False, encoding="utf-8", mode="x", lineterminator="\n")
    result = {"schema": "training-reconstruction-exceptions-v1", "status": "completed",
        "created_at": datetime.now(timezone.utc).isoformat(), "snapshot_sha256": expected_sha,
        "prior_audit_result_sha256": sha256(AUDIT / "training-audit-result.json"),
        "cohort_sha256": sha256(COHORT), "code_sha256": source_hash,
        "imported_frozen_code_sha256": prior_result["source_sha256"], "counts": counts,
        "files": {name: sha256(output / name) for name in tables}, "protected_before": before, "protected_after": after,
        "network_requests": 0, "model_fits": 0, "source_mutated": False, "race_outcome_columns_loaded": [],
        "raw_source_limit": "No record-bound raw original was supplied; causes are unverified, not inferred from clock values or missing result rows."}
    with (output / OUTPUT_NAMES[3]).open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"status": "completed", "counts": counts, "result_sha256": sha256(output / OUTPUT_NAMES[3])}, ensure_ascii=False))
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

"""Independently check cell coverage, public-source claims and saved-field SQL counts."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))
sys.path.insert(0, str(ROOT / "keiba"))

import pandas as pd
import pyarrow.parquet as pq

from experiments.common import file_sha256, read_json
from experiments.features import APTITUDE_COLUMNS, HISTORY_COLUMNS
from experiments.goal_diagnostics import _history_requirement
from experiments.search import freeze_json
from scraping.acquisition_horse_history import prepare_history
from scraping.fetch_pipeline import decode_html_body
from scraping.missingness_goal import _protected

IDENTITY = ["race_id", "horse_id", "horse_number", "race_date"]


def readonly(path):
    return sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True)


def check_reasons(part, targets):
    for reason, rows in part.groupby("reason", sort=False):
        actual = targets.loc[rows.row_index]
        column = rows.feature.iloc[0]
        if reason == "no_prior_stored_horse_history":
            requirement = _history_requirement(column)
            valid = requirement is not None and requirement[1] == 1 and actual[requirement[0]].eq(0).all()
        elif reason == "fewer_than_two_prior_stored_starts":
            valid = column.startswith("safe_prev2_") and actual.safe_prior_starts.isin([0, 1]).all()
        elif reason == "no_surface_history_for_surface_or_course":
            valid = (column.startswith(("safe_horse_surface_", "safe_horse_course_"))
                     and actual.safe_horse_surface_starts.eq(0).all())
        elif reason == "no_distance_band_history_or_surface_fallback":
            valid = (column.startswith("safe_horse_distance_band_")
                     and actual.safe_horse_distance_band_starts.eq(0).all()
                     and actual.safe_horse_surface_starts.eq(0).all())
        elif reason in {"unidentified_entity", "identified_entity_without_prior_stored_results"}:
            entity = column.split("_")[1]
            valid = entity in {"sire", "damsire", "jockey", "trainer"}
            if valid:
                starts = actual[f"safe_{entity}_starts"]
                valid = (starts.isna().all() if reason == "unidentified_entity"
                         else starts.eq(0).all() and column.endswith(("_win_rate", "_top3_rate")))
        elif reason == "unavailable_race_class":
            valid = column == "safe_race_class"
        elif reason == "prior_starts_without_last3f_measurement":
            requirement = _history_requirement(column)
            valid = column.endswith("avg_last3f_time") and actual[requirement[0]].gt(0).all()
        else:
            valid = False
        if not valid:
            raise ValueError(f"acquisition-replay-invalid-cell-reason: {column}/{reason}")


def verify(root):
    task = read_json(root / "contract.json")
    for name, digest in read_json(root / "artifacts.json").items():
        if file_sha256(root / name) != digest:
            raise ValueError(f"acquisition-replay-artifact-changed: {name}")
    if file_sha256(ROOT / "scripts" / "audit_acquisition_adequacy.py") != task["code_sha256"]:
        raise ValueError("acquisition-replay-audit-code-changed")
    for name, digest in task["code_hashes"].items():
        if file_sha256(Path(name)) != digest:
            raise ValueError("acquisition-replay-source-parser-changed")
    source, cohort, reuse = Path(task["source"]), Path(task["cohort"]), Path(task["reuse_sources"])
    if (file_sha256(source) != task["source_sha256"] or _protected() != task["protected"]
            or file_sha256(cohort / "artifacts.json") != task["cohort_artifacts_sha256"]
            or file_sha256(reuse / "artifacts.json") != task["reused_artifacts_sha256"]):
        raise ValueError("acquisition-replay-input-changed")
    if task.get("career_source_cache") and file_sha256(
        Path(task["career_source_cache"]) / "sources.db"
    ) != task["career_source_cache_sha256"]:
        raise ValueError("acquisition-replay-career-cache-changed")
    for parent in (cohort, reuse):
        for name, digest in read_json(parent / "artifacts.json").items():
            if file_sha256(parent / name) != digest:
                raise ValueError("acquisition-replay-parent-artifact-changed")
    targets = pd.read_parquet(cohort / "applied-dataset.parquet")
    history = pd.read_parquet(cohort / "history-observations.parquet")
    public = {}
    with closing(readonly(root / "sources.db")) as current, closing(readonly(reuse / "sources.db")) as old:
        for parent in (reuse, root):
            for claim in read_json(parent / "career-audit.json"):
                bodies = []
                for prefix in ("profile", "ajax"):
                    url = claim[f"{prefix}_source"]
                    row = (current if parent == root else old).execute(
                        "SELECT final_url,status,body,fetched_at FROM sources WHERE url=?", (url,),
                    ).fetchone()
                    if (row is None or row[1] != 200 or row[0].rstrip("/") != url.rstrip("/")
                            or hashlib.sha256(row[2]).hexdigest() != claim[f"{prefix}_sha256"]
                            or row[3] != claim[f"{prefix}_received_at_unix"]):
                        raise ValueError("acquisition-replay-career-transport-or-bytes-changed")
                    bodies.append(decode_html_body(row[2]))
                _, parsed = prepare_history(*bodies, claim["horse_id"], claim["target_date"])
                if any(claim[key] != value for key, value in parsed.items()):
                    raise ValueError("acquisition-replay-career-claim-disagrees")
                expected_no_prior = (parsed["coverage_matches_published_career_count"]
                                     and parsed["strictly_prior_started_rows"] == 0
                                     and parsed["unknown_prior_start_status_rows"] == 0)
                if claim["confirmed_no_public_prior_start"] != expected_no_prior:
                    raise ValueError("acquisition-replay-false-public-debut-claim")
                public[(claim["horse_id"], claim["target_date"])] = claim
    pedigree = {row["horse_id"]: row["status"]
                for row in read_json(root / "pedigree-queue-snapshot.json")["rows"]}
    clocks = {(p["race_id"], row["horse_id"]): row["last3f_state"]
              for p in read_json(reuse / "race-proofs.json") for row in p["rows"]}
    reason_counts, resolution_counts, cells = Counter(), Counter(), Counter()
    parquet = pq.ParquetFile(root / "missing-cell-ledger.parquet")
    for number in range(parquet.num_row_groups):
        part = parquet.read_row_group(number).to_pandas()
        if part.empty:
            continue
        if part.feature.nunique() != 1 or part.row_index.duplicated().any():
            raise ValueError("acquisition-replay-duplicate-or-mixed-feature-cell")
        column = part.feature.iloc[0]
        if column not in task["features"]:
            raise ValueError("acquisition-replay-feature-outside-contract")
        expected = targets.index[targets[column].isna()].tolist()
        if part.row_index.tolist() != expected:
            raise ValueError("acquisition-replay-missing-cell-population-disagrees")
        pd.testing.assert_frame_equal(part[IDENTITY].reset_index(drop=True),
                                      targets.loc[expected, IDENTITY].reset_index(drop=True),
                                      check_dtype=False)
        check_reasons(part, targets)
        for row in part.itertuples():
            resolution = row.source_resolution
            if resolution.startswith("public_") or resolution == "verified_no_public_prior_start":
                if resolution == "public_source_not_audited":
                    continue
                claim = public.get((row.horse_id, row.race_date))
                if (claim is None or row.reason not in {
                    "no_prior_stored_horse_history", "fewer_than_two_prior_stored_starts"
                }):
                    raise ValueError("acquisition-replay-public-claim-without-exact-source")
                if resolution not in {
                    "verified_no_public_prior_start", "public_prior_outside_current_jra_scope_observed",
                    "public_prior_start_observed_requires_result_and_eligibility_audit",
                    "public_career_audited_coverage_or_start_status_unresolved",
                }:
                    raise ValueError("acquisition-replay-unknown-public-source-resolution")
                if resolution == "verified_no_public_prior_start" and not claim["confirmed_no_public_prior_start"]:
                    raise ValueError("acquisition-replay-unverified-public-debut")
                if resolution == "public_prior_outside_current_jra_scope_observed" and not any(
                    item["scope"] == "outside_jra" and item["start_status"] == "started"
                    for item in claim["strictly_prior_race_inventory"]
                ):
                    raise ValueError("acquisition-replay-unproven-outside-scope-start")
                if (resolution == "public_prior_start_observed_requires_result_and_eligibility_audit"
                        and claim["strictly_prior_started_rows"] <= 0):
                    raise ValueError("acquisition-replay-unproven-prior-start")
            elif resolution.startswith("pedigree_"):
                state = pedigree.get(row.horse_id)
                allowed = {
                    "pedigree_acquired_pending_materialization": state == "accepted",
                    "pedigree_fetch_pending": state == "pending",
                    "pedigree_source_not_accepted_not_proof_of_nonexistent_parents":
                        state is not None and state not in {"accepted", "pending"},
                }
                if (not allowed.get(resolution, False) or row.reason != "unidentified_entity"
                        or not column.startswith(("safe_sire_", "safe_damsire_"))):
                    raise ValueError("acquisition-replay-pedigree-stage-disagrees")
            elif resolution == "verified_public_prior_clock_empty":
                window = int(column.split("_")[1].removeprefix("past"))
                prior = history.loc[history.horse_id.eq(row.horse_id)
                                    & history.race_date.lt(row.race_date)].sort_values(
                                        ["race_date", "race_id"]).tail(window)
                if (prior.empty or not prior.last_3f_time.isna().all()
                        or not all(clocks.get((p.race_id, p.horse_id)) == "missing"
                                   for p in prior.itertuples())):
                    raise ValueError("acquisition-replay-unproven-public-clock-gap")
            else:
                raise ValueError("acquisition-replay-unknown-source-resolution")
        cells[column] += len(part)
        reason_counts.update(part.reason)
        resolution_counts.update(part.source_resolution)
    receipt = read_json(root / "verification.json")
    expected_cells = {c: int(targets[c].isna().sum()) for c in task["features"]}
    if ({c: cells[c] for c in task["features"]} != expected_cells
            or reason_counts != Counter(receipt["stored_history_reasons"])
            or dict(resolution_counts) != receipt["public_source_resolution"]
            or sum(cells.values()) != receipt["remaining_missing_cells"]):
        raise ValueError("acquisition-replay-feature-or-source-totals-disagree")
    observed_history = set(zip(history.race_id, history.horse_id, history.race_date))
    expected_plan = []
    for claim in read_json(root / "career-audit.json"):
        for entry in claim["strictly_prior_race_inventory"]:
            if entry["race_date"] >= claim["target_date"]:
                raise ValueError("acquisition-replay-nonprior-repair-target")
            if entry["start_status"] == "not_started":
                action = "confirmed_nonstart_do_not_import_as_a_start"
            elif entry["race_id"] is None:
                action = "resolve_unlinked_public_history"
            elif entry["scope"] != "jra":
                action = "outside_jra_contract_separate_scope_review"
            elif entry["race_date"] < "20130101":
                action = "before_history_start_contract_separate_scope_review"
            elif entry["start_status"] == "unknown":
                action = "audit_unknown_finish_do_not_assume_nonstart"
            elif (entry["race_id"], claim["horse_id"], entry["race_date"]) in observed_history:
                action = "eligible_history_already_present"
            else:
                action = "audit_full_result_before_history_import"
            expected_plan.append({
                "horse_id": claim["horse_id"], "target_date": claim["target_date"], **entry,
                "action": action, "profile_sha256": claim["profile_sha256"],
                "ajax_sha256": claim["ajax_sha256"],
                "public_career_coverage_verified": claim["coverage_matches_published_career_count"],
            })
    if read_json(root / "history-repair-plan.json") != expected_plan:
        raise ValueError("acquisition-replay-history-repair-routing-disagrees")
    all_history = pd.read_parquet(root / "all-history-gap-targets.parquet")
    historical = [column for column in task["features"] if column in [*HISTORY_COLUMNS, *APTITUDE_COLUMNS]]
    expected_history = targets.loc[targets[historical].isna().any(axis=1), IDENTITY].copy()
    expected_history["missing_history_cells"] = targets.loc[expected_history.index, historical].isna().sum(axis=1)
    pd.testing.assert_frame_equal(all_history, expected_history.reset_index(drop=True))
    inventory = read_json(root / "raw-field-inventory.json")
    if any(sum(counts.values()) != inventory["canonical_runner_rows"]
           for counts in inventory["canonical_fields"].values()):
        raise ValueError("acquisition-replay-canonical-field-populations-disagree")
    with closing(readonly(source)) as db:
        for table, report_key, count_key in [
            ("races_ultimate", "race_fields", "metadata_rows"),
            ("race_results_ultimate", "runner_fields", "runner_rows"),
        ]:
            where = "substr(race_id,1,4) BETWEEN '2016' AND '2026'"
            total = db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
            if total != inventory[count_key]:
                raise ValueError("acquisition-replay-raw-row-count-disagrees")
            sql = f"""
                SELECT j.key, COUNT(*), SUM(j.type='null' OR
                    (j.type='text' AND lower(trim(j.value)) IN
                     ('','none','nan','<na>','unknown','unknown_local','null','n/a','-','--','不明')))
                FROM {table},json_each(data) AS j WHERE {where} GROUP BY j.key
            """
            actual = {key: (count, blanks) for key, count, blanks in db.execute(sql)}
            expected = {row["field"]: (row["declared_rows"], row["null_or_unknown_when_declared"])
                        for row in inventory[report_key]}
            if actual != expected:
                raise ValueError("acquisition-replay-raw-field-catalog-disagrees")
    if file_sha256(source) != task["source_sha256"] or _protected() != task["protected"]:
        raise ValueError("acquisition-replay-source-or-production-changed")
    result = {
        "verified": True, "features_verified": len(task["features"]),
        "missing_cells_independently_verified": sum(cells.values()),
        "exact_public_career_claims_replayed": len(public),
        "raw_fields_verified_by_sql": len(inventory["runner_fields"]) + len(inventory["race_fields"]),
        "source_and_protected_state_unchanged": True, "research_only": True,
        "public_absence_not_generalized_from_sample": True,
        "all_missing_zero": receipt["all_missing_zero"], "verifier_sha256": file_sha256(Path(__file__)),
    }
    freeze_json(root / "replay-verification.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    print(json.dumps(verify(parser.parse_args().root.resolve(strict=True)), ensure_ascii=False), flush=True)

"""Audit every frozen feature and saved field without treating unknown coverage as absence."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import closing, ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))
sys.path.insert(0, str(ROOT / "keiba"))

import aiohttp
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from experiments import dataset as ds
from experiments.common import file_sha256, read_json
from experiments.features import BASE_CATEGORICAL, BASE_NUMERIC, FEATURE_GROUPS
from experiments.goal_diagnostics import _history_requirement
from experiments.search import freeze_json
from scraping.acquisition_horse_history import prepare_history
from scraping.fetch_pipeline import (
    FetchAccessBlocked, decode_html_body, fetch_context, fetch_text, get_fetch_control_status,
)
from scraping.horse_history_ajax import history_url
from scraping.missingness_goal import _protected

BASE = ROOT / "reports" / "generated" / "data-quality" / "feature-gap"
IDENTITY = ["race_id", "horse_id", "horse_number", "race_date"]


def readonly(path):
    return sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True)


def feature_spec(column):
    group = next(name for name, columns in FEATURE_GROUPS.items() if column in columns)
    if group == "base":
        source = {**BASE_NUMERIC, **BASE_CATEGORICAL,
                  "safe_month": "race_date", "safe_race_number": "race_id"}[column]
        return group, source, "target race/entry; retrospective capture does not prove prestart availability"
    if group == "entities":
        entity = column.split("_")[1]
        source = f"{entity}_id" if entity in {"jockey", "trainer"} else entity
        return group, f"{source}; prior population race_date/finish", "strictly prior population results"
    if column.endswith("avg_last3f_time"):
        source = "prior race_date/last_3f_time"
    elif column.endswith("distance") or column == "safe_distance_change":
        source = "prior and target race_date/distance"
    elif column == "safe_days_since_last":
        source = "prior and target race_date"
    elif column.endswith(("speed_mps", "time_seconds")):
        source = "prior race_date/distance/time_seconds"
    elif column.endswith(("finish", "win_rate", "top3_rate")):
        source = "prior race_date/finish"
    else:
        source = "prior eligible race identities; counts are not public career coverage"
    if group == "distance_course":
        source += "; target and prior surface/venue/distance band"
    return group, source, "strictly prior result pages; current profile totals are not historical predictors"


def missing_reasons(frame, column):
    missing = frame[column].isna()
    reason = pd.Series("not_missing", index=frame.index, dtype="string")
    reason.loc[missing] = "unclassified"
    group, _, _ = feature_spec(column)
    if group == "base":
        reason.loc[missing] = "unavailable_race_class" if column == "safe_race_class" else "base_source_gap"
    elif group == "entities":
        entity = column.split("_")[1]
        starts = frame[f"safe_{entity}_starts"]
        reason.loc[missing & starts.isna()] = "unidentified_entity"
        reason.loc[missing & starts.eq(0)] = "identified_entity_without_prior_stored_results"
    elif group == "distance_course" and column != "safe_distance_change":
        match = re.fullmatch(r"safe_horse_(surface|course|distance_band)_(.+)", column)
        condition = match[1]
        starts = frame[f"safe_horse_{condition}_starts"]
        mask = missing & starts.eq(0)
        if condition == "distance_band":
            reason.loc[mask & frame.safe_horse_surface_starts.eq(0)] = (
                "no_distance_band_history_or_surface_fallback"
            )
        else:
            reason.loc[mask & frame.safe_horse_surface_starts.eq(0)] = (
                "no_surface_history_for_surface_or_course"
            )
    else:
        requirement = _history_requirement(column)
        if requirement is not None:
            count, minimum, _ = requirement
            reason.loc[missing & frame[count].ge(0) & frame[count].lt(minimum)] = (
                "fewer_than_two_prior_stored_starts" if minimum == 2 else "no_prior_stored_horse_history"
            )
            if column.endswith("avg_last3f_time"):
                reason.loc[missing & frame[count].gt(0)] = "prior_starts_without_last3f_measurement"
    if reason.eq("unclassified").any():
        raise ValueError(f"acquisition-audit-unclassified-missing-cells: {column}")
    return reason


def career_target_rows(frame, features):
    history_features = [column for column in features
                        if feature_spec(column)[0] in {"history", "distance_course"}]
    cold = frame.loc[frame.safe_prior_starts.eq(0), IDENTITY].copy()
    impacts = frame[history_features].isna().sum(axis=1).groupby(frame.horse_id).sum()
    cold = cold.sort_values(["race_date", "race_id", "horse_number"]).drop_duplicates("horse_id")
    cold["affected_history_cells"] = cold.horse_id.map(impacts).astype("int64")
    return cold.sort_values(["affected_history_cells", "race_date", "horse_id"],
                            ascending=[False, True, True]).reset_index(drop=True)


def public_no_prior_confirmed(career):
    return (career["coverage_matches_published_career_count"]
            and career["strictly_prior_started_rows"] == 0
            and career["unknown_prior_start_status_rows"] == 0)


def compare_inventory(career, history):
    observed = {(str(row.race_id), str(row.horse_id), str(row.race_date))
                for row in history.loc[history.horse_id.eq(career["horse_id"])].itertuples()}
    plan = []
    for entry in career["strictly_prior_race_inventory"]:
        if entry["race_date"] >= career["target_date"]:
            raise ValueError("acquisition-audit-current-or-future-inventory")
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
        elif (entry["race_id"], career["horse_id"], entry["race_date"]) in observed:
            action = "eligible_history_already_present"
        else:
            action = "audit_full_result_before_history_import"
        plan.append({
            "horse_id": career["horse_id"], "target_date": career["target_date"],
            **entry, "action": action, "profile_sha256": career["profile_sha256"],
            "ajax_sha256": career["ajax_sha256"],
            "public_career_coverage_verified": career["coverage_matches_published_career_count"],
        })
    return plan


async def audit_careers(root, candidates, history, maximum, reuse, career_cache=None, offline=False):
    careers, errors, plan = [], [], []
    with ExitStack() as stack:
        archive = stack.enter_context(closing(sqlite3.connect(root / "sources.db")))
        previous = stack.enter_context(closing(readonly(reuse / "sources.db")))
        cached = stack.enter_context(closing(readonly(career_cache / "sources.db"))) if career_cache else None
        metrics = stack.enter_context(fetch_context(job_id=root.name))
        archive.execute("CREATE TABLE sources(url TEXT PRIMARY KEY,final_url TEXT,status INTEGER,body BLOB,fetched_at REAL)")
        archive.execute("CREATE TABLE career_queue(horse_id TEXT PRIMARY KEY,target_date TEXT,status TEXT,error TEXT)")
        archive.executemany("INSERT INTO career_queue VALUES(?,?,'pending',NULL)",
                            [(row.horse_id, row.race_date) for row in candidates.itertuples()])
        archive.commit()
        async with aiohttp.ClientSession() as session:
            async def receive(url):
                row = previous.execute("SELECT url,final_url,status,body,fetched_at FROM sources WHERE url=?",
                                       (url,)).fetchone()
                if row is None and cached is not None:
                    row = cached.execute("SELECT url,final_url,status,body,fetched_at FROM sources WHERE url=?",
                                         (url,)).fetchone()
                if row is None:
                    if offline:
                        raise ValueError("acquisition-audit-offline-source-unavailable")
                    response, _ = await fetch_text(
                        session, url, max_retries=1, allow_redirects=False, min_interval_sec=2,
                        total_timeout_sec=30, max_body_bytes=2_000_000, force_refresh=True,
                    )
                    row = (url, response.url, response.status, response.body,
                           (response.cache_snapshot or {}).get("fetched_at"))
                archive.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?)", row)
                archive.commit()
                if row[2] != 200 or row[1].rstrip("/") != url.rstrip("/") or not row[4]:
                    raise ValueError(f"acquisition-audit-transport-unverified: status={row[2]}")
                return decode_html_body(row[3]), row

            consecutive_failures = 0
            stop = "sample_limit"
            for target in candidates.head(maximum).itertuples():
                if not offline:
                    control = get_fetch_control_status()
                    if control.get("blocked_reason"):
                        stop = "provider_blocked"
                        break
                    if control.get("cooldown_until", 0) > datetime.now(timezone.utc).timestamp():
                        stop = "provider_cooldown"
                        break
                if consecutive_failures >= 3:
                    stop = "consecutive_unaccepted_sources"
                    break
                try:
                    profile, p = await receive(f"https://db.netkeiba.com/horse/{target.horse_id}/")
                    response, a = await receive(history_url(target.horse_id))
                    _, evidence = prepare_history(profile, response, target.horse_id, target.race_date)
                    evidence.update({
                        "horse_id": target.horse_id, "target_race_id": target.race_id,
                        "profile_source": p[0], "profile_sha256": hashlib.sha256(p[3]).hexdigest(),
                        "ajax_source": a[0], "ajax_sha256": hashlib.sha256(a[3]).hexdigest(),
                        "profile_received_at_unix": p[4], "ajax_received_at_unix": a[4],
                    })
                    evidence["confirmed_no_public_prior_start"] = public_no_prior_confirmed(evidence)
                    careers.append(evidence)
                    plan.extend(compare_inventory(evidence, history))
                    status, error = "source_audited", None
                    consecutive_failures = 0
                except FetchAccessBlocked as exc:
                    status, error, stop = "provider_stopped", str(exc), "provider_blocked"
                except ValueError as exc:
                    status, error = "source_unresolved", str(exc)
                    consecutive_failures += 1
                archive.execute("UPDATE career_queue SET status=?,error=? WHERE horse_id=?",
                                (status, error, target.horse_id))
                archive.commit()
                if error is not None:
                    errors.append({"horse_id": target.horse_id, "target_date": target.race_date,
                                   "reason": error, "status": status})
                if stop == "provider_blocked":
                    break
            counts = dict(archive.execute("SELECT status,COUNT(*) FROM career_queue GROUP BY status"))
            network = metrics.get("network_requests", 0)
    freeze_json(root / "career-audit.json", careers)
    freeze_json(root / "history-repair-plan.json", plan)
    freeze_json(root / "career-collection.json", {
        "sample_not_population_proof": True, "queue_counts": counts, "errors": errors,
        "network_requests": network, "stop_reason": stop,
        "confirmed_no_public_prior_start": sum(public_no_prior_confirmed(c) for c in careers),
        "prior_inventory_actions": dict(Counter(row["action"] for row in plan)),
        "no_raw_results_imported": True,
    })
    return careers, plan


def raw_inventory(source):
    appearances, blanks, declared = Counter(), Counter(), Counter()
    meta_appearances, meta_blanks = Counter(), Counter()
    rows, races, metadata_rows = 0, set(), 0
    leakage = Counter()
    scope = Counter()
    canonical_fields = {column: Counter() for column in [*ds.SOURCE_COLUMNS, "dam"]}
    normalized_rows, invalid_rows = 0, Counter()
    with closing(readonly(source)) as db:
        metadata, valid_metadata = {}, {}
        for race_id, raw in db.execute(
            "SELECT race_id,data FROM races_ultimate WHERE substr(race_id,1,4) BETWEEN '2016' AND '2026'"
        ):
            item = json.loads(raw)
            if not isinstance(item, dict):
                raise ValueError("acquisition-audit-race-json-not-object")
            day = ds._date(ds._first(item, "date", "race_date"))
            metadata[race_id] = day
            metadata_rows += 1
            normalized, reason = ds._race_metadata(race_id, item)
            if reason is None:
                valid_metadata[race_id] = normalized
            scope[reason if reason is not None else "jra_flat_metadata_accepted"] += 1
            for key, value in item.items():
                meta_appearances[key] += 1
                if value is None or (isinstance(value, str) and ds._text(value) is None):
                    meta_blanks[key] += 1
        for race_id, raw in db.execute(
            "SELECT race_id,data FROM race_results_ultimate WHERE substr(race_id,1,4) BETWEEN '2016' AND '2026'"
        ):
            item = json.loads(raw)
            if not isinstance(item, dict):
                raise ValueError("acquisition-audit-runner-json-not-object")
            rows += 1
            races.add(race_id)
            for key, value in item.items():
                appearances[key] += 1
                if value is None or (isinstance(value, str) and ds._text(value) is None):
                    blanks[key] += 1
            if race_id in valid_metadata:
                normalized, invalid = ds._runner(race_id, item, valid_metadata[race_id])
                if invalid is not None:
                    invalid_rows[invalid] += 1
                else:
                    normalized_rows += 1
                    normalized["dam"] = ds._text(ds._first(item, "dam", "dam_name"))
                    finish_label = ds._text(ds._first(item, "finish", "finish_position"))
                    nonfinish = finish_label in {"取", "取消", "除", "除外", "中", "中止", "失", "失格"}
                    numeric = {"horse_number", "finish", "time_seconds", "distance", "num_horses", "odds",
                               "popularity", "bracket_number", "age", "burden_weight", "horse_weight",
                               "horse_weight_change", "last_3f_time"}
                    for key, value in normalized.items():
                        if key not in canonical_fields:
                            continue
                        missing = (not np.isfinite(value)) if key in numeric else ds._text(value) is None
                        if missing:
                            state = ("explicit_nonfinish_without_measurement" if nonfinish
                                     and key in {"finish", "time_seconds", "last_3f_time"} else
                                     "unknown_or_nonfinite_in_saved_snapshot")
                        elif key in numeric and key != "horse_weight_change" and value <= 0:
                            state = "nonpositive_numeric_not_a_valid_measurement"
                        elif key == "last_3f_time" and not 10 < value < 100:
                            state = "outside_last3f_clock_domain"
                        else:
                            state = "present_in_saved_snapshot_not_source_reverified"
                        canonical_fields[key][state] += 1
            day = metadata.get(race_id)
            if day is None:
                leakage["runner_rows_without_valid_metadata_date"] += 1
                continue
            for prefix in ("prev", "prev2"):
                previous = ds._date(item.get(f"{prefix}_race_date"))
                if previous is not None and previous >= day:
                    leakage[f"{prefix}_same_day_or_future_date_rows"] += 1
            declared.update({key: 1 for key in item if key.startswith("_")})
    def catalog(total, counts, unknown):
        return [{
            "field": key, "declared_rows": counts[key], "absent_key_rows": total - counts[key],
            "null_or_unknown_when_declared": unknown[key],
            "unspecified_schema_absence_is_not_a_scrape_failure": True,
        } for key in sorted(counts)]
    return {
        "scope": "all saved race IDs beginning 2016-2026; includes out-of-model-scope records",
        "runner_rows": rows, "runner_races": len(races), "metadata_rows": metadata_rows,
        "race_metadata_scope_reasons": dict(scope), "runner_fields": catalog(rows, appearances, blanks),
        "race_fields": catalog(metadata_rows, meta_appearances, meta_blanks),
        "canonical_scope": "2016-2026 saved JRA-flat metadata and normalizable runner identities; not eligibility",
        "canonical_runner_rows": normalized_rows, "canonical_identity_errors": dict(invalid_rows),
        "canonical_fields": {key: dict(value) for key, value in canonical_fields.items()},
        "raw_measurement_zero_is_not_valid_finish_time_odds_or_last3f": True,
        "legacy_prev_fields_excluded_from_safe_features": True, "legacy_date_conflicts": dict(leakage),
        "source_metadata_presence_is_not_independent_source_verification": dict(declared),
        "all_optional_json_keys_zero_is_not_a_meaningful_acquisition_contract": True,
    }


def career_resolutions(frame, careers):
    lookup = {}
    for career in careers:
        if public_no_prior_confirmed(career):
            resolution = "verified_no_public_prior_start"
        elif any(item["scope"] == "outside_jra" and item["start_status"] == "started"
                 for item in career["strictly_prior_race_inventory"]):
            resolution = "public_prior_outside_current_jra_scope_observed"
        elif career["strictly_prior_started_rows"] > 0:
            resolution = "public_prior_start_observed_requires_result_and_eligibility_audit"
        else:
            resolution = "public_career_audited_coverage_or_start_status_unresolved"
        lookup[(career["horse_id"], career["target_date"])] = resolution
    keys = pd.MultiIndex.from_frame(frame[["horse_id", "race_date"]])
    return pd.Series(keys.map(lookup), index=frame.index, dtype="string").fillna("public_source_not_audited")


def source_resolution(frame, column, reasons, careers, pedigree, clock_proofs, history, resolved_careers=None):
    result = pd.Series("public_source_not_audited", index=frame.index, dtype="string")
    if resolved_careers is None:
        resolved_careers = career_resolutions(frame, careers)
    horse_history = reasons.isin(["no_prior_stored_horse_history", "fewer_than_two_prior_stored_starts"])
    result.loc[horse_history] = resolved_careers.loc[horse_history]
    if column.startswith(("safe_sire_", "safe_damsire_")):
        states = frame.horse_id.map(pedigree)
        unknown = reasons.eq("unidentified_entity")
        result.loc[unknown & states.eq("accepted")] = "pedigree_acquired_pending_materialization"
        result.loc[unknown & states.eq("pending")] = "pedigree_fetch_pending"
        result.loc[unknown & states.notna() & ~states.isin(["accepted", "pending"])] = (
            "pedigree_source_not_accepted_not_proof_of_nonexistent_parents"
        )
    if column.endswith("avg_last3f_time"):
        window = int(re.search(r"past(\d+)", column)[1])
        for row in frame.loc[reasons.eq("prior_starts_without_last3f_measurement")].itertuples():
            prior = history.loc[history.horse_id.eq(row.horse_id)
                                & history.race_date.lt(row.race_date)].sort_values(
                                    ["race_date", "race_id"]).tail(window)
            if (len(prior) and prior.last_3f_time.isna().all()
                    and all(clock_proofs.get((r.race_id, r.horse_id)) == "missing"
                            for r in prior.itertuples())):
                result.loc[row.Index] = "verified_public_prior_clock_empty"
    return result


def write_cell_audit(root, frame, features, careers, pedigree, clock_proofs, history):
    summary, reason_totals, resolution_totals = [], Counter(), Counter()
    resolved_careers = career_resolutions(frame, careers)
    schema = pa.schema([(name, pa.string()) for name in
                        ["race_id", "horse_id", "race_date", "feature", "reason", "source_resolution"]]
                       + [("horse_number", pa.float64()), ("row_index", pa.int64())])
    with pq.ParquetWriter(root / "missing-cell-ledger.parquet", schema, compression="zstd") as writer:
        for column in features:
            reasons = missing_reasons(frame, column)
            selected = frame.loc[frame[column].isna(), IDENTITY].copy()
            selected["row_index"] = selected.index.astype("int64")
            selected["feature"] = column
            selected["reason"] = reasons.loc[selected.index]
            resolutions = source_resolution(frame, column, reasons, careers, pedigree, clock_proofs, history,
                                             resolved_careers)
            selected["source_resolution"] = resolutions.loc[selected.index]
            writer.write_table(pa.Table.from_pandas(selected, schema=schema, preserve_index=False))
            rc, sc = dict(Counter(selected.reason)), dict(Counter(selected.source_resolution))
            reason_totals.update(rc)
            resolution_totals.update(sc)
            group, source, availability = feature_spec(column)
            summary.append({
                "feature": column, "group": group, "source_fields": source,
                "availability": availability, "missing_cells": len(selected),
                "stored_history_reasons": rc, "public_source_resolution": sc,
            })
    freeze_json(root / "feature-audit.json", summary)
    pd.DataFrame([{**item, "stored_history_reasons": json.dumps(item["stored_history_reasons"]),
                   "public_source_resolution": json.dumps(item["public_source_resolution"])}
                  for item in summary]).to_csv(root / "all-features.csv", index=False, encoding="utf-8-sig")
    return dict(reason_totals), dict(resolution_totals)


async def run(root, cohort, reuse, pedigree_root, maximum, career_cache=None, offline=False):
    contract = read_json(cohort / "contract.json")
    receipt = read_json(cohort / "replay-verification.json")
    if receipt.get("verified") is not True:
        raise ValueError("acquisition-audit-requires-independently-verified-cohort")
    for name, digest in read_json(cohort / "artifacts.json").items():
        if file_sha256(cohort / name) != digest:
            raise ValueError(f"acquisition-audit-cohort-artifact-changed: {name}")
    for name, digest in contract["code_hashes"].items():
        if file_sha256(ROOT / name) != digest:
            raise ValueError(f"acquisition-audit-cohort-code-changed: {name}")
    for name, digest in read_json(reuse / "artifacts.json").items():
        if file_sha256(reuse / name) != digest:
            raise ValueError(f"acquisition-audit-source-evidence-changed: {name}")
    source = Path(contract["baseline"]) / "research.db"
    if file_sha256(source) != contract["source_sha256"]:
        raise ValueError("acquisition-audit-raw-source-changed")
    frame = pd.read_parquet(cohort / "applied-dataset.parquet")
    history = pd.read_parquet(cohort / "history-observations.parquet")
    features = contract["value_features"]
    if set(features) != {c for columns in FEATURE_GROUPS.values() for c in columns}:
        raise ValueError("acquisition-audit-feature-contract-changed")
    protected = _protected()
    root.mkdir()
    freeze_json(root / "contract.json", {
        "schema": "all-fields-acquisition-adequacy-v1", "cohort": str(cohort),
        "cohort_artifacts_sha256": file_sha256(cohort / "artifacts.json"),
        "source": str(source), "source_sha256": contract["source_sha256"],
        "reuse_sources": str(reuse), "reused_artifacts_sha256": file_sha256(reuse / "artifacts.json"),
        "protected": protected, "features": features, "maximum_career_samples": maximum,
        "research_only": True, "code_sha256": file_sha256(Path(__file__)),
        "no_source_or_production_model_writes": True, "network_interval_seconds": 2,
        "sample_not_population_proof": True, "public_absence_requires_source_confirmation": True,
        "career_source_cache": str(career_cache) if career_cache else None,
        "career_source_cache_sha256": file_sha256(career_cache / "sources.db") if career_cache else None,
        "offline_source_replay": offline,
        "code_hashes": {str(p): file_sha256(p) for p in [
            Path(__file__), ROOT / "python-api" / "scraping" / "acquisition_horse_history.py",
            ROOT / "python-api" / "scraping" / "horse_history_ajax.py",
            ROOT / "python-api" / "experiments" / "dataset.py",
            ROOT / "python-api" / "experiments" / "features.py",
            ROOT / "python-api" / "experiments" / "goal_diagnostics.py",
        ]},
    })
    candidates = career_target_rows(frame, features)
    candidates.to_parquet(root / "career-audit-targets.parquet", index=False, compression="zstd")
    historical = [column for column in features if feature_spec(column)[0] in {"history", "distance_course"}]
    all_history = frame.loc[frame[historical].isna().any(axis=1), IDENTITY].copy()
    all_history["missing_history_cells"] = frame.loc[all_history.index, historical].isna().sum(axis=1)
    all_history.to_parquet(root / "all-history-gap-targets.parquet", index=False, compression="zstd")
    careers, _ = await audit_careers(root, candidates, history, maximum, reuse, career_cache, offline)
    known = {(c["horse_id"], c["target_date"]): c for c in read_json(reuse / "career-audit.json")}
    known.update({(c["horse_id"], c["target_date"]): c for c in careers})
    with closing(readonly(pedigree_root / "source-archive.db")) as db:
        db.execute("BEGIN")
        queued = [{"horse_id": horse, "status": status, "error": error}
                  for horse, status, error in db.execute("SELECT horse_id,status,error FROM queue ORDER BY horse_id")]
    freeze_json(root / "pedigree-queue-snapshot.json", {
        "at_utc": datetime.now(timezone.utc).isoformat(), "root": str(pedigree_root),
        "counts": dict(Counter(row["status"] for row in queued)), "rows": queued,
        "collecting_snapshot_not_final_materialized_result": True,
    })
    pedigree = {row["horse_id"]: row["status"] for row in queued}
    clocks = {(proof["race_id"], row["horse_id"]): row["last3f_state"]
              for proof in read_json(reuse / "race-proofs.json") for row in proof["rows"]}
    reasons, resolutions = write_cell_audit(root, frame, features, list(known.values()), pedigree, clocks, history)
    if Counter(reasons) != Counter(receipt["remaining_reason_cells"]):
        raise ValueError("acquisition-audit-reason-partition-disagrees-with-independent-replay")
    reasons = {key: reasons.get(key, 0) for key in receipt["remaining_reason_cells"]}
    inventory = raw_inventory(source)
    freeze_json(root / "raw-field-inventory.json", inventory)
    if file_sha256(source) != contract["source_sha256"] or _protected() != protected:
        raise ValueError("acquisition-audit-frozen-input-or-production-changed")
    if career_cache and file_sha256(career_cache / "sources.db") != read_json(
        root / "contract.json"
    )["career_source_cache_sha256"]:
        raise ValueError("acquisition-audit-career-cache-changed")
    result = {
        "audited": True, "research_only": True, "feature_count": len(features),
        "target_rows": len(frame), "target_races": int(frame.race_id.nunique()),
        "remaining_missing_cells": sum(reasons.values()), "stored_history_reasons": reasons,
        "public_source_resolution": resolutions, "raw_saved_runner_fields": len(inventory["runner_fields"]),
        "raw_saved_race_fields": len(inventory["race_fields"]),
        "pending_career_audit_horses": int(len(candidates) - len(careers)),
        "all_history_gap_target_rows": len(all_history),
        "all_history_gap_target_horses": int(all_history.horse_id.nunique()),
        "current_acquisition_is_not_complete_past_result_import": True,
        "all_missing_public_source_reasons_known": resolutions.get("public_source_not_audited", 0) == 0,
        "all_missing_zero": sum(reasons.values()) == 0,
        "source_and_protected_state_unchanged": True,
    }
    freeze_json(root / "verification.json", result)
    freeze_json(root / "artifacts.json", {p.name: file_sha256(p) for p in root.iterdir() if p.is_file()})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, default=BASE / "period-root-cause-applied-20261031-v1")
    parser.add_argument("--reuse-sources", type=Path, default=BASE / "residual-source-audit-20261031-v4")
    parser.add_argument("--pedigree-root", type=Path, default=BASE / "pedigree-root-cause-20261031-full")
    parser.add_argument("--max-careers", type=int, default=100)
    parser.add_argument("--career-cache", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if root.parent != BASE.resolve() or root.exists() or args.max_careers < 0:
        parser.error("a new feature-gap output root and nonnegative sample limit are required")
    print(json.dumps(asyncio.run(run(root, args.cohort.resolve(strict=True),
                                    args.reuse_sources.resolve(strict=True),
                                    args.pedigree_root.resolve(strict=True), args.max_careers,
                                    args.career_cache.resolve(strict=True) if args.career_cache else None,
                                    args.offline)),
                     ensure_ascii=False), flush=True)

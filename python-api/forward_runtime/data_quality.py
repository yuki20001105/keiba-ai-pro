"""Non-destructive canonical audit/export and pre-cutoff daily history state.

Only new isolated outputs are written. Raw DB rows are never repaired in place;
exact duplicate JSON objects collapse only in the export, conflicts quarantine
the entire race, and unavailable fields remain missing rather than zero-filled.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd

from experiments import dataset as canonical
from experiments.common import REPO_ROOT, file_sha256, json_sha256, read_json, write_json
from forward_adapters.feature_state import build_history_state
from training.eligibility import select_training_eligible_rows


SCHEMA = "non-destructive-forward-clean-history-v1"


def _day(value):
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        raise ValueError("canonical-cutoff-date-required")
    datetime.strptime(value, "%Y%m%d")
    return value


def _new_output(path):
    path = Path(path).resolve()
    for protected in (REPO_ROOT / "keiba", REPO_ROOT / "python-api", REPO_ROOT / "src"):
        if path == protected or path.is_relative_to(protected):
            raise ValueError("cleaning-output-must-not-touch-application-data-or-source")
    if path == REPO_ROOT or (path.exists() and (not path.is_dir() or any(path.iterdir()))):
        raise ValueError("cleaning-requires-new-empty-output")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _missing(value):
    return canonical._text(value) is None


def _object(payload):
    row = json.loads(payload)
    if not isinstance(row, dict):
        raise ValueError("object-required")
    return row, json_sha256(row)


def _summary_missing(frame):
    return {name: {"missing": int(frame[name].map(_missing).sum()), "rows": len(frame)} for name in frame}


def audit_clean_history(snapshot_path, output_dir, *, before_date, progress=None):
    """Audit an immutable SQLite backup and publish a separate canonical export.

    The live primary DB is intentionally rejected: create a consistent backup
    first using training.local_retrain.create_sqlite_snapshot (online read-only
    source backup). JSON payloads are streamed; only compact columns are kept.
    """
    before_date = _day(before_date)
    if Path(snapshot_path).is_symlink():
        raise ValueError("immutable-snapshot-required")
    source = Path(snapshot_path).resolve(strict=True)
    if source == (REPO_ROOT / "keiba/data/keiba_ultimate.db").resolve():
        raise ValueError("audit-requires-consistent-backup-not-live-database")
    if not source.is_file() or source.is_symlink():
        raise ValueError("immutable-snapshot-required")
    wal = Path(str(source) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("snapshot-has-pending-wal")
    output = _new_output(output_dir)
    notify = progress or (lambda stage, pct: None)
    notify("hash immutable audit snapshot", 0)
    snapshot_sha = file_sha256(source)
    connection = sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=5)
    connection.execute("PRAGMA query_only=ON")
    counters = Counter({name: 0 for name in ("raw_race_rows", "raw_runner_rows", "exact_duplicate_race_rows",
        "conflicting_duplicate_race_rows", "raw_invalid_or_missing_runner_number_rows", "exact_duplicate_runner_rows",
        "conflicting_duplicate_runner_rows", "canonical_exact_duplicate_rows_collapsed", "canonical_prequality_runner_rows",
        "duplicate_horse_identity_rows")})
    reasons, missing = Counter(), Counter()
    race_hashes, metadata, quarantine, runner_hashes, horse_numbers = {}, {}, {}, {}, {}
    exact_race_rows, exact_runner_rows = Counter(), Counter()
    columns = {name: [] for name in canonical.SOURCE_COLUMNS}
    quality = {}
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"races_ultimate", "race_results_ultimate"}.issubset(tables):
            raise ValueError("source-ultimate-tables-required")
        for sidecar in ("licensed_history_entries", "official_history_entries"):
            if sidecar in tables and connection.execute(f"SELECT 1 FROM {sidecar} LIMIT 1").fetchone():
                raise ValueError("source-sidecar-contract-not-supported")
        for rid, payload in connection.execute("SELECT race_id,data FROM races_ultimate"):
            rid = str(rid); counters["raw_race_rows"] += 1
            try:
                row, sha = _object(payload)
            except (ValueError, TypeError):
                quarantine[rid] = "invalid_race_json"; reasons["invalid_race_json_rows"] += 1
                continue
            if rid in race_hashes:
                if sha == race_hashes[rid]:
                    counters["exact_duplicate_race_rows"] += 1; exact_race_rows[rid] += 1
                else:
                    quarantine[rid] = "conflicting_race_duplicates"; counters["conflicting_duplicate_race_rows"] += 1
                continue
            race_hashes[rid] = sha
            meta, reason = canonical._race_metadata(rid, row)
            if reason:
                reasons[reason] += 1
            elif meta["race_date"] >= before_date:
                reasons["on_or_after_target_day"] += 1
            else:
                metadata[rid] = meta
        notify("stream raw runner duplicates and canonical missingness", 15)
        for rid, payload in connection.execute("SELECT race_id,data FROM race_results_ultimate"):
            rid = str(rid); counters["raw_runner_rows"] += 1
            if counters["raw_runner_rows"] % 50000 == 0:
                notify(f"audit raw runners {counters['raw_runner_rows']}", 25)
            try:
                row, sha = _object(payload)
            except (ValueError, TypeError):
                quarantine[rid] = "invalid_runner_json"; reasons["invalid_runner_json_rows"] += 1
                continue
            number = canonical._number(row.get("horse_number"))
            key = (rid, int(number)) if math.isfinite(number) and number == int(number) and 1 <= number <= 18 else None
            if key is None:
                counters["raw_invalid_or_missing_runner_number_rows"] += 1
                quarantine[rid] = "invalid_runner_number"
                continue
            if key in runner_hashes:
                if sha == runner_hashes[key]:
                    counters["exact_duplicate_runner_rows"] += 1; exact_runner_rows[rid] += 1
                    if rid in metadata: counters["canonical_exact_duplicate_rows_collapsed"] += 1
                else:
                    counters["conflicting_duplicate_runner_rows"] += 1
                    quarantine[rid] = "conflicting_runner_duplicates"
                continue
            runner_hashes[key] = sha
            if rid not in metadata:
                continue
            compact, reason = canonical._runner(rid, row, metadata[rid])
            if reason:
                quarantine[rid] = reason; reasons[reason + "_rows"] += 1
                continue
            counters["canonical_prequality_runner_rows"] += 1
            for name in canonical.SOURCE_COLUMNS:
                value = compact.get(name)
                columns[name].append(value)
                if _missing(value): missing[name] += 1
            horse = compact["horse_id"]
            if horse:
                identity = (rid, horse)
                if identity in horse_numbers and horse_numbers[identity] != number:
                    quarantine[rid] = "duplicate_horse_identity"
                    counters["duplicate_horse_identity_rows"] += 1
                horse_numbers[identity] = number
        if "scrape_race_acquisition_state" in tables:
            for rid, lifecycle, status, excluded in connection.execute(
                    "SELECT race_id,lifecycle,quality_status,excluded_from_standard FROM scrape_race_acquisition_state"):
                if str(rid) in metadata:
                    quality[str(rid)] = (str(lifecycle or ""), str(status or ""), bool(excluded))
    finally:
        connection.close()
    notify("apply existing settled-result eligibility and complete-cohort gates", 55)
    frame = pd.DataFrame(columns)
    quarantine_rows = int(frame.race_id.isin(quarantine).sum()) if not frame.empty else 0
    frame = frame.loc[~frame.race_id.isin(quarantine)].reset_index(drop=True)
    if frame.empty:
        raise ValueError("no-canonical-history-after-integrity-quarantine")
    selection = select_training_eligible_rows(frame, target="speed_deviation", recorded_quality_states=quality)
    raw_counts = frame.groupby("race_id").size()
    selected_counts = selection.frame.groupby("race_id").size()
    partial = selected_counts.index[selected_counts.ne(raw_counts.reindex(selected_counts.index))]
    clean = selection.frame.loc[~selection.frame.race_id.isin(partial)].sort_values(
        ["race_date", "race_id", "horse_number"], kind="mergesort").reset_index(drop=True)
    if clean.empty:
        raise ValueError("no-clean-complete-historical-races")
    if not clean.race_date.lt(before_date).all():
        raise ValueError("history-cutoff-invariant-failed")
    clean = clean.loc[:, canonical.SOURCE_COLUMNS]
    eligibility = selection.manifest.as_dict()
    dropped_races = sorted(set(frame.race_id) - set(clean.race_id))
    counts = clean.groupby("race_id").size()
    if not clean.num_horses.eq(clean.race_id.map(counts)).all() or clean.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("final-clean-cohort-invariant-failed")
    known = clean.horse_id.ne("") & clean.horse_id.notna()
    first = clean.loc[known].groupby("horse_id").race_date.min()
    cold = known & clean.race_date.eq(clean.horse_id.map(first))
    clean_path = output / "clean_history.parquet"
    notify("write new clean history only; originals untouched", 75)
    clean.to_parquet(clean_path, index=False, compression="zstd")
    clean_sha = file_sha256(clean_path)
    repair = {"schema": SCHEMA, "source_snapshot": str(source), "snapshot_sha256": snapshot_sha,
              "before_date_exclusive": before_date, "originals_modified": False,
              "deduplication_rule": "same race_id and runner number plus identical canonicalized full raw JSON object; no ignored field differences",
              "exact_duplicate_race_rows_by_race": dict(exact_race_rows), "exact_duplicate_runner_rows_by_race": dict(exact_runner_rows),
              "quarantined_races": dict(sorted(quarantine.items())), "eligibility_excluded_race_ids": dropped_races,
              "partial_target_races_excluded_in_full": list(map(str, partial)), "eligibility": eligibility,
              "clean_history": {"path": str(clean_path), "sha256": clean_sha, "rows": len(clean), "races": int(clean.race_id.nunique())},
              "module_sha256": file_sha256(Path(__file__)), "source_dataset_module_sha256": file_sha256(Path(canonical.__file__))}
    report = {"schema": SCHEMA, "status": "completed", "before_date_exclusive": before_date, "raw_counts": dict(counters),
              "scope_and_invalid_reasons": dict(reasons), "quarantined_race_count": len(quarantine),
              "quarantined_canonical_runner_rows": quarantine_rows,
              "canonical_prequality_missingness": {name: {"missing": missing[name], "rows": counters["canonical_prequality_runner_rows"]}
                                                   for name in canonical.SOURCE_COLUMNS},
              "clean_rows": len(clean), "clean_races": int(clean.race_id.nunique()),
              "clean_date_from": str(clean.race_date.min()), "clean_date_to": str(clean.race_date.max()),
              "clean_missingness": _summary_missing(clean), "eligibility": eligibility,
              "history_missingness": {"known_horse_first_observed_day_runner_rows": int(cold.sum()), "unknown_horse_id_runner_rows": int((~known).sum()),
                  "meaning": "No earlier eligible observation is a valid missing-history state, not a repairable zero or proof of no career starts."},
              "missingness_policy": "No zero fill, no latest-profile borrowing, no future or same-day history; numeric missing values remain NaN for the fixed encoder.",
              "raw_vs_feature_policy": "Raw database observations are not a training-ready feature table. Current/unsettled races are excluded; past-derived features are generated from this clean history separately.",
              "payouts_or_odds_repaired": False, "network_repairs_performed": False, "originals_modified": False,
              "repair_manifest": str(output / "repair_manifest.json"), "clean_history": repair["clean_history"], "snapshot_sha256": snapshot_sha}
    if file_sha256(source) != snapshot_sha:
        raise ValueError("source-snapshot-changed-during-audit")
    write_json(output / "repair_manifest.json", repair)
    report["repair_manifest_sha256"] = file_sha256(output / "repair_manifest.json")
    write_json(output / "quality_report.json", report)
    notify("non-destructive clean history audit complete", 100)
    return report


def prepare_daily_history(clean_dir, output_dir, *, target_date, progress=None):
    """Persist a reusable daily state outside the T-5 acquisition window.

    Never scans a live DB here. Input must be a hash-bound completed clean export
    whose own cutoff is no later than the requested target; stale coverage is
    retained explicitly rather than renamed as a newly observed provider field.
    """
    target_date = _day(target_date)
    directory = Path(clean_dir).resolve(strict=True)
    report = read_json(directory / "quality_report.json")
    repair_path = directory / "repair_manifest.json"
    if report.get("schema") != SCHEMA or report.get("status") != "completed" or file_sha256(repair_path) != report["repair_manifest_sha256"]:
        raise ValueError("completed-clean-export-proof-required")
    repair = read_json(repair_path)
    if repair["before_date_exclusive"] > target_date:
        raise ValueError("clean-export-cutoff-later-than-target")
    path = directory / "clean_history.parquet"
    if file_sha256(path) != repair["clean_history"]["sha256"] or report["clean_history"] != repair["clean_history"]:
        raise ValueError("clean-history-hash-mismatch")
    output = _new_output(output_dir)
    notify = progress or (lambda stage, pct: None)
    notify("load prepared canonical clean history, not live database", 0)
    history = pd.read_parquet(path, filters=[("race_date", "<", target_date)])
    state = build_history_state(history, target_date=target_date)
    state_path = output / "history_state.json"
    notify("persist immutable daily state and prior-only bloodline identities", 75)
    write_json(state_path, state)
    bloodlines, conflicts = {}, {}
    identified = history.loc[history.horse_id.notna() & history.horse_id.ne("")]
    for horse, group in identified.groupby("horse_id", sort=True):
        entry = {"history_max_source_date": str(group.race_date.max())}
        bad = []
        for name in ("sire", "damsire"):
            values = sorted({value for raw in group[name] if (value := canonical._text(raw)) is not None})
            entry[name] = values[0] if len(values) == 1 else None
            if len(values) > 1:
                bad.append(name)
        bloodlines[str(horse)] = entry
        if bad: conflicts[str(horse)] = bad
    blood_path = output / "prior_bloodlines.json"
    write_json(blood_path, {"target_date": target_date, "history_boundary": "source race_date < target_date",
                           "policy": "exact consistent historical names only; no arbitrary normalization or future profile fallback",
                           "horses": bloodlines, "conflicting_fields_by_horse": conflicts})
    manifest = {"schema": "prepared-forward-daily-history-v1", "status": "completed", "target_date": target_date,
                "state_path": str(state_path), "state_sha256": file_sha256(state_path), "state_content_sha256": state["state_sha256"],
                "bloodline_path": str(blood_path), "bloodline_sha256": file_sha256(blood_path),
                "history_max_source_date": state["state"]["history_max_source_date"],
                "history_rows": state["state"]["history_row_count"], "history_races": state["state"]["history_race_count"],
                "bloodline_conflicting_horses": len(conflicts), "clean_history_sha256": repair["clean_history"]["sha256"],
                "repair_manifest_sha256": file_sha256(repair_path), "snapshot_sha256": repair["snapshot_sha256"],
                "feature_state_module_sha256": state["state"]["source_modules"]["feature_state"],
                "ready_for_canonical_feature_generation": True, "live_provider_parser_verified": False,
                "prepared_at_utc": datetime.now(timezone.utc).isoformat(), "preparation_purpose": "prepare before the race cutoff; scheduling remains caller responsibility",
                "live_database_scanned": False, "same_day_or_future_history_used": False, "originals_modified": False}
    if file_sha256(path) != repair["clean_history"]["sha256"]:
        raise ValueError("clean-export-changed-during-state-preparation")
    write_json(output / "preparation_manifest.json", manifest)
    notify("daily history ready for separate live adapter validation", 100)
    return manifest

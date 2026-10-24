"""Read-only freshness and exclusion audit of the fixed DB history contract.

This is an inventory audit, not data repair, model training, a claim of full
career coverage, or evidence that old observations were captured pre-start.
The eligibility formula and production databases are never modified.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import uuid

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for _directory in (ROOT / "python-api", ROOT / "keiba"):
    sys.path.insert(0, str(_directory))
from experiments import dataset as canonical
from training import db_shared_features as shared
from training import eligibility

SCHEMA = "db-shared-history-freshness-audit-v1"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _json_object(payload):
    try:
        value = json.loads(payload)
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError):
        return None


def _physical_source(path):
    return {"sha256": sha(path), "bytes": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
            "data_sidecars": {suffix: ({"sha256": sha(sidecar), "bytes": sidecar.stat().st_size}
                if (sidecar := Path(str(path) + suffix)).exists() else None)
                for suffix in ("-wal", "-journal")}}


def _range(values):
    ordered = sorted(set(values))
    return {"from": ordered[0] if ordered else None,
            "to": ordered[-1] if ordered else None}


def _valid_positive(value):
    number = canonical._number(value)
    return math.isfinite(number) and number > 0


def _raw_runner_summary(rows):
    """Observed fields only: cancellation labels are not target-value guesses."""
    valid_objects = [row for row in rows if row is not None]
    finish_text = [canonical._text(canonical._first(row, "finish", "finish_position"))
                   for row in valid_objects]
    return {
        "rows": len(rows), "invalid_json_rows": len(rows) - len(valid_objects),
        "positive_finish_rows": sum(_valid_positive(canonical._first(row, "finish", "finish_position"))
                                    for row in valid_objects),
        "positive_time_rows": sum(_valid_positive(canonical._time(canonical._first(
            row, "time_seconds", "finish_time", "time"))) for row in valid_objects),
        "positive_odds_rows": sum(_valid_positive(row.get("odds")) for row in valid_objects),
        "positive_popularity_rows": sum(_valid_positive(row.get("popularity")) for row in valid_objects),
        "winner_rows": sum(canonical._number(canonical._first(row, "finish", "finish_position")) == 1
                           for row in valid_objects),
        "explicit_nonfinisher_labels": dict(Counter(text for text in finish_text
            if text and (text in {"取", "除", "中", "失"} or
                         any(marker in text for marker in ("取消", "除外", "中止", "失格"))))),
    }


def _race_detail(rid, raw_metadata, raw_runner_rows, quality, eligible_ids):
    raw = raw_metadata[0] if len(raw_metadata) == 1 else None
    meta, metadata_reason = canonical._race_metadata(rid, raw) if raw is not None else (
        None, "race_json_invalid" if len(raw_metadata) == 1 else "race_metadata_duplicate_or_missing")
    errors, parsed = Counter(), []
    for row in raw_runner_rows:
        if meta is None:
            break
        try:
            item, reason = canonical._runner(rid, shared._weight_alias(row), meta) if row is not None else (
                None, "runner_json_or_alias_invalid")
        except (ValueError, TypeError):
            item, reason = None, "runner_json_or_alias_invalid"
        if reason:
            errors[reason] += 1
        else:
            parsed.append(item)
    nonexclusive, partial = {}, False
    if parsed and not errors and metadata_reason is None:
        selected = eligibility.select_training_eligible_rows(
            pd.DataFrame(parsed, columns=canonical.SOURCE_COLUMNS), target="speed_deviation",
            recorded_quality_states={rid: quality[rid]} if rid in quality else {})
        nonexclusive = {reason: count for reason, count in
                        selected.manifest.as_dict()["exclusion_reason_counts"].items() if count["rows"]}
        partial = 0 < len(selected.frame) < len(parsed)
    reasons = ([metadata_reason] if metadata_reason else []) + sorted(errors)
    reasons += sorted(nonexclusive)
    if not raw_runner_rows:
        reasons.append("runner_rows_missing")
    if partial:
        reasons.append("partial_selected_race_rejected_by_shared_complete_cohort")
    summary = _raw_runner_summary(raw_runner_rows)
    if rid in eligible_ids:
        stage = "eligible_complete_jra_flat_history"
    elif metadata_reason:
        stage = "metadata_or_domain_quarantine"
    elif errors:
        stage = "runner_parse_or_identity_quarantine"
    elif summary["positive_finish_rows"] == 0 and summary["positive_time_rows"] == 0:
        stage = "card_only_or_unsettled_observation"
    else:
        stage = "quality_or_result_completeness_exclusion"
    return {"race_id": rid, "raw_date": canonical._date(canonical._first(raw or {}, "date", "race_date")),
            "raw_date_fields": {name: (raw or {}).get(name) for name in ("date", "race_date")},
            "raw_race_name": (raw or {}).get("race_name"),
            "raw_track_type": (raw or {}).get("track_type"),
            "raw_surface": (raw or {}).get("surface"),
            "raw_distance": (raw or {}).get("distance"),
            "raw_declared_horses": (raw or {}).get("num_horses"),
            "metadata_rows": len(raw_metadata), "eligible": rid in eligible_ids,
            "stage": stage, "exclusion_reasons": sorted(set(reasons)),
            "canonical_runner_errors": dict(errors),
            "nonexclusive_eligibility_reasons": nonexclusive,
            "quality_state": list(quality[rid]) if rid in quality else None,
            "partial_complete_contract_exclusion_with_explicit_nonfinisher": bool(
                partial and summary["explicit_nonfinisher_labels"]),
            "raw_runner_summary": summary}


def target_history_audit(current, raw_metadata, raw_runner_rows, eligible, cutoff):
    """Separate observed raw career rows from usable complete-race history."""
    eligible_latest = eligible.groupby("horse_id").race_date.max().to_dict() if len(eligible) else {}
    eligible_counts = eligible.groupby("horse_id").size().to_dict() if len(eligible) else {}
    raw_dates = defaultdict(list)
    for rid, rows in raw_runner_rows.items():
        dates = [canonical._date(canonical._first(row or {}, "date", "race_date"))
                 for row in raw_metadata.get(rid, [])]
        day = dates[0] if len(dates) == 1 else None
        if not day or day >= cutoff:
            continue
        for raw in rows:
            if raw is not None:
                horse = canonical._text(raw.get("horse_id"))
                if horse:
                    raw_dates[horse].append(day)
    output = []
    target_day = date.fromisoformat(f"{cutoff[:4]}-{cutoff[4:6]}-{cutoff[6:]}")
    for row in current:
        horse = canonical._text(row.get("horse_id"))
        usable = eligible_latest.get(horse)
        raw_latest = max(raw_dates[horse]) if raw_dates[horse] else None
        gap = (target_day - date(int(usable[:4]), int(usable[4:6]), int(usable[6:]))).days if usable else None
        output.append({"horse_number": row.get("horse_number"), "horse_id": horse,
            "raw_prior_rows": len(raw_dates[horse]), "raw_latest_prior_date": raw_latest,
            "eligible_prior_rows": int(eligible_counts.get(horse, 0)),
            "eligible_latest_prior_date": usable, "days_since_latest_eligible_history": gap,
            "raw_rows_newer_than_eligible": bool(raw_latest and (usable is None or raw_latest > usable)),
            "no_eligible_history": usable is None,
            "complete_career_coverage_verified": False})
    return output


def audit_database(db_path, *, cutoff="20261012", focus_from="20260914", focus_to="20261011",
                   current_capture=None):
    if not all(canonical._date(value) == value for value in (cutoff, focus_from, focus_to)) or not (
            "20130101" <= focus_from <= focus_to < cutoff):
        raise ValueError("freshness-period-invalid")
    path = Path(db_path).resolve(strict=True)
    started = time.perf_counter()
    before = _physical_source(path)
    pins = shared.source_hashes()
    raw_metadata = defaultdict(list)
    raw_date_counts = Counter()
    raw_year_counts = Counter()
    raw_runner_year_counts = Counter()
    metadata_digest, runner_digest = hashlib.sha256(), hashlib.sha256()
    selected = {}
    source_audit = shared._audit()
    quality = {}
    with shared._database(path) as (connection, tables):
        transaction = {"query_only": connection.execute("PRAGMA query_only").fetchone()[0],
            "in_transaction": connection.in_transaction,
            "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
            "schema_version": connection.execute("PRAGMA schema_version").fetchone()[0]}
        counts = {name: connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                  for name in ("races_ultimate", "race_results_ultimate")}
        for rid, payload in connection.execute("SELECT race_id,data FROM races_ultimate"):
            rid = str(rid)
            metadata_digest.update(json.dumps((rid, payload), separators=(",", ":")).encode() + b"\n")
            raw = _json_object(payload)
            raw_metadata[rid].append(raw)
            if len(rid) == 12 and "2013" <= rid[:4] <= cutoff[:4]:
                raw_year_counts[rid[:4]] += 1
            day = canonical._date(canonical._first(raw or {}, "date", "race_date"))
            if day:
                raw_date_counts[day] += 1
            meta, reason = canonical._race_metadata(rid, raw) if raw is not None else (None, "race_json_invalid")
            if reason:
                source_audit["metadata_scope_exclusions"][reason] += 1
            elif meta["race_date"] < cutoff:
                selected[rid] = meta
        for rid in list(selected):
            if len(raw_metadata[rid]) != 1:
                source_audit["quarantined_races"][rid] = "race_metadata_duplicate_or_missing"
                del selected[rid]
        eligible, eligibility_receipt = shared._load_complete(connection, tables, selected, source_audit)
        if "scrape_race_acquisition_state" in tables:
            for rid, lifecycle, status, excluded in connection.execute(
                "SELECT race_id,lifecycle,quality_status,excluded_from_standard FROM scrape_race_acquisition_state"):
                if str(rid) in quality:
                    raise ValueError("freshness-quality-ledger-duplicate")
                quality[str(rid)] = (str(lifecycle or ""), str(status or ""), bool(excluded))
        focus_ids = {rid for rid, values in raw_metadata.items()
            if any((day := canonical._date(canonical._first(raw or {}, "date", "race_date"))) is not None
                   and focus_from <= day <= focus_to for raw in values)}
        current = []
        current_source = None
        if current_capture is not None:
            current_path = Path(current_capture).resolve(strict=True) / "sources" / "canonical_inputs.json"
            body = json.loads(current_path.read_text(encoding="utf-8"))
            current = body["current"]
            if not isinstance(current, list) or any(row.get("race_date") != cutoff for row in current):
                raise ValueError("freshness-current-capture-date-invalid")
            current_source = {"path": str(current_path), "sha256": sha(current_path),
                "purpose": "identity_only_prior_saved_capture_not_current_quote_or_capture_timing_evidence"}
        horses = {canonical._text(row.get("horse_id")) for row in current} - {None}
        raw_runner_rows = defaultdict(list)
        for rid, payload in connection.execute(
                "SELECT race_id,data FROM race_results_ultimate WHERE substr(race_id,1,4) BETWEEN '2013' AND ?",
                (cutoff[:4],)):
            rid = str(rid)
            raw_runner_year_counts[rid[:4]] += 1
            runner_digest.update(json.dumps((rid, payload), separators=(",", ":")).encode() + b"\n")
            raw = _json_object(payload)
            if rid in focus_ids or (raw is not None and canonical._text(raw.get("horse_id")) in horses):
                raw_runner_rows[rid].append(raw)
        eligible_ids = set(eligible.race_id)
        details = [_race_detail(rid, raw_metadata[rid], raw_runner_rows[rid], quality, eligible_ids)
                   for rid in sorted(focus_ids)]
        target_audit = target_history_audit(current, raw_metadata, raw_runner_rows, eligible, cutoff)
        target_latest = {item["horse_id"]: item["eligible_latest_prior_date"] for item in target_audit}
        newer_ids = set()
        for rid, rows in raw_runner_rows.items():
            metadata = raw_metadata.get(rid, [])
            raw = metadata[0] if len(metadata) == 1 else None
            day = canonical._date(canonical._first(raw or {}, "date", "race_date"))
            if not day or day >= cutoff:
                continue
            for row in rows:
                horse = canonical._text((row or {}).get("horse_id"))
                if horse in target_latest and (target_latest[horse] is None or day > target_latest[horse]):
                    newer_ids.add(rid)
        target_excluded_details = []
        for batch in shared._batches(sorted(newer_ids)):
            complete_rows = defaultdict(list)
            marks = ",".join("?" for _ in batch)
            for rid, payload in connection.execute(
                    f"SELECT race_id,data FROM race_results_ultimate WHERE race_id IN ({marks})", batch):
                complete_rows[str(rid)].append(_json_object(payload))
            for rid in batch:
                detail = _race_detail(rid, raw_metadata[rid], complete_rows[rid], quality, eligible_ids)
                detail["present_current_horse_ids"] = sorted({canonical._text((row or {}).get("horse_id"))
                    for row in complete_rows[rid] if canonical._text((row or {}).get("horse_id")) in horses})
                detail["affected_current_horse_ids"] = [horse for horse in detail["present_current_horse_ids"]
                    if target_latest[horse] is None or detail["raw_date"] > target_latest[horse]]
                target_excluded_details.append(detail)
        eligible_rows = eligible.groupby(eligible.race_date.str[:4]).size().to_dict()
        eligible_races = eligible.groupby(eligible.race_date.str[:4]).race_id.nunique().to_dict()
        eligible_dates = set(eligible.race_date)
        identity_digest = hashlib.sha256()
        for item in eligible[["race_id", "horse_number", "race_date"]].sort_values(
                ["race_id", "horse_number"], kind="mergesort").itertuples(index=False, name=None):
            identity_digest.update(json.dumps(item, separators=(",", ":")).encode() + b"\n")
    after = _physical_source(path)
    if shared.source_hashes() != pins:
        raise ValueError("freshness-formula-source-drift")
    yearly = [{"year": year, "raw_metadata_rows_by_race_id_year": raw_year_counts[str(year)],
               "raw_runner_rows_by_race_id_year": raw_runner_year_counts[str(year)],
               "eligible_complete_history_races": int(eligible_races.get(str(year), 0)),
               "eligible_complete_history_runner_rows": int(eligible_rows.get(str(year), 0))}
              for year in range(2013, int(cutoff[:4]) + 1)]
    daily = []
    day = date(int(focus_from[:4]), int(focus_from[4:6]), int(focus_from[6:]))
    end = date(int(focus_to[:4]), int(focus_to[4:6]), int(focus_to[6:]))
    while day <= end:
        compact = day.strftime("%Y%m%d")
        races = [item for item in details if item["raw_date"] == compact]
        reasons = Counter(reason for item in races for reason in item["exclusion_reasons"])
        daily.append({"race_date": compact, "saved_metadata_races": len(races),
            "eligible_complete_history_races": sum(item["eligible"] for item in races),
            "raw_runner_rows": sum(item["raw_runner_summary"]["rows"] for item in races),
            "stage_counts": dict(Counter(item["stage"] for item in races)),
            "nonexclusive_exclusion_reason_counts": dict(reasons),
            "no_saved_races_is_not_proof_of_no_meeting": len(races) == 0})
        day += timedelta(days=1)
    return {"schema": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
        "source_database": str(path), "source_file_before": before, "source_file_after": after,
        "source_file_changed_during_read_transaction": before != after,
        "read_transaction": transaction, "source_hashes": pins,
        "auditor_sha256": sha(__file__), "tables": counts,
        "cutoff_exclusive": cutoff, "focus_period": {"from": focus_from, "to": focus_to},
        "raw_metadata_date_range_all_saved_domains": _range(raw_date_counts),
        "eligible_complete_history_date_range": _range(eligible_dates),
        "eligible_complete_history_races": len(eligible_ids), "eligible_complete_history_runner_rows": len(eligible),
        "eligible_identity_sha256": identity_digest.hexdigest(),
        "logical_transaction_source_sha256": {"all_races_ultimate_in_scan_order": metadata_digest.hexdigest(),
            "race_results_ultimate_2013_through_cutoff_year_in_scan_order": runner_digest.hexdigest()},
        "eligibility_receipt": eligibility_receipt,
        "metadata_scope_exclusions": dict(source_audit["metadata_scope_exclusions"]),
        "canonical_runner_or_metadata_quarantine_count": len(source_audit["quarantined_races"]),
        "canonical_quarantine_reason_counts": dict(Counter(source_audit["quarantined_races"].values())),
        "yearly": yearly, "daily": daily, "recent_races": details,
        "current_identity_source": current_source, "target_horse_history": target_audit,
        "target_horse_excluded_newer_races": target_excluded_details,
        "network_requests": 0, "database_writes": 0, "formula_changes": False,
        "training_started": False, "production_or_forward_changes": False,
        "complete_career_coverage_verified": False, "prestart_capture_provenance_verified": False,
        "interpretation": {"unknown_saved_dates_not_imputed": True,
            "nonfinishers_are_structural_only_when_explicit_label_present": True,
            "quality_or_partial_race_exclusions_not_relaxed": True,
            "later_profile_totals_or_cached_prev_fields_consumed": False},
        "elapsed_seconds": time.perf_counter() - started}


def persist_audit(receipt, store):
    directory = Path(store) / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "audit.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    manifest = {"schema": SCHEMA, "receipt_sha256": object_sha(receipt),
        "files": {"audit.json": {"sha256": sha(path), "bytes": path.stat().st_size}},
        "source_hashes": receipt["source_hashes"], "database_writes": 0,
        "network_requests": 0, "created_at": datetime.now(timezone.utc).isoformat()}
    with (directory / "artifact_manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=ROOT / "keiba/data/keiba_ultimate.db")
    parser.add_argument("--cutoff", default="20261012")
    parser.add_argument("--focus-from", default="20260914")
    parser.add_argument("--focus-to", default="20261011")
    parser.add_argument("--current-capture", type=Path)
    parser.add_argument("--store", type=Path, default=ROOT / "reports/generated/db-shared-history-freshness")
    args = parser.parse_args()
    receipt = audit_database(args.db_path, cutoff=args.cutoff, focus_from=args.focus_from,
        focus_to=args.focus_to, current_capture=args.current_capture)
    directory = persist_audit(receipt, args.store)
    print(json.dumps({"status": "completed", "directory": str(directory),
        "latest_eligible_history": receipt["eligible_complete_history_date_range"]["to"],
        "recent_stage_counts": dict(Counter(item["stage"] for item in receipt["recent_races"])),
        "database_writes": 0, "network_requests": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()

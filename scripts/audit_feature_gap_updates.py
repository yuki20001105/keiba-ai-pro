"""Read-only multi-batch preservation audit for explicit feature-gap repairs.

The original audit/snapshot and repair reports are never modified. Journal
hashes, global cross-batch continuity and field-level permissions are separate
requirements; a valid hash alone does not authorize a source-field change.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "python-api", ROOT / "scripts"):
    sys.path.insert(0, str(directory))

import audit_quality_repair_after_v2 as previous
from scraping.acquisition_scope import is_jra_race_id, normalized_date
from scraping.last3f_evidence import classify_last3f, _url_race_id
from scraping.pedigree_gap_repair import ALIASES, FIELDS, checked_run_directory
from scraping.saved_horse_reuse import _name, _present

SCHEMA = "feature-gap-multi-batch-after-audit-v1"
ORIGINAL_BATCH = "20261017T033200Z"
PERIOD = ("20160101", "20261016")
CLOCK_FIELDS = frozenset({"last_3f_time", "last_3f"})
HEX = re.compile(r"[a-f0-9]{64}")


def exact(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(exact(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(exact(a, b) for a, b in zip(left, right))
    return left == right


def changed_keys(before, after):
    return {key for key in before.keys() | after.keys()
            if key not in before or key not in after or not exact(before[key], after[key])}


def _utc_timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def _strict_name(value):
    name = _name(value)
    return name is not None and re.search(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]", name) is not None


def validate_feature_entry(entry):
    """Return independent field-permission failures, without touching a DB."""
    kind, problems = entry["kind"], []
    if kind == "original":
        return problems
    if entry["table"] != "race_results_ultimate" or entry["before"] is None:
        return ["feature_repair_must_update_existing_runner"]
    try:
        before, after = json.loads(entry["before"]), json.loads(entry["after"])
        evidence = entry["evidence"]
        if not isinstance(before, dict) or not isinstance(after, dict) or not isinstance(evidence, dict):
            return ["feature_repair_json_object_required"]
    except (TypeError, ValueError):
        return ["feature_repair_invalid_json"]
    changed = changed_keys(before, after)
    if before.get("race_id") is not None and str(before["race_id"]) != str(entry["race_id"]):
        problems.append("embedded_runner_race_id_conflict")
    if not changed:
        problems.append("feature_repair_no_change")
    if kind == "pedigree":
        if changed - set(FIELDS):
            problems.append("pedigree_unrelated_field_changed")
        for field in changed & set(FIELDS):
            if _present(before.get(field)) or not _strict_name(after.get(field)) or after.get(field) != _name(after.get(field)):
                problems.append("pedigree_not_missing_to_strict_name")
        for field in FIELDS:
            if not _strict_name(after.get(field)):
                problems.append("pedigree_final_triple_incomplete_or_ambiguous")
            for alias in ALIASES[field]:
                if _present(before.get(alias)) and _name(before[alias]) != _name(after.get(field)):
                    problems.append("pedigree_original_alias_conflict")
        if (evidence.get("repair_kind") != "static_pedigree_gap"
                or evidence.get("source_fact_kind") != "static_pedigree_from_saved_evidence"
                or evidence.get("historical_capture_time_verified") is not False
                or evidence.get("not_prestart_evidence") is not True
                or sorted(evidence.get("filled_fields", [])) != sorted(changed)
                or not HEX.fullmatch(str(evidence.get("plan_sha256", "")))
                or not HEX.fullmatch(str(evidence.get("consensus_sha256", "")))
                or not _utc_timestamp(evidence.get("observed_at_utc"))):
            problems.append("pedigree_reconstruction_evidence_invalid")
        start, end = evidence.get("source_revision_before"), evidence.get("source_revision_after")
        if (not isinstance(start, list) or not isinstance(end, list) or len(start) != 3 or len(end) != 3
                or start[0] != end[0] or type(start[1]) is not int or type(end[1]) is not int or end[1] != start[1] + 1):
            problems.append("pedigree_source_revision_invalid")
    elif kind == "last3f":
        allowed = CLOCK_FIELDS | {"_last3f_gap_repair"}
        if changed - allowed or not changed & CLOCK_FIELDS:
            problems.append("last3f_unrelated_or_no_clock_change")
        numbers = set()
        for field in CLOCK_FIELDS:
            old_state, old_number = classify_last3f(before.get(field))
            new_state, new_number = classify_last3f(after.get(field))
            if field in changed and (old_state not in {"missing", "masked"} or new_state != "valid"):
                problems.append("last3f_not_missing_or_masked_to_valid")
            if old_state == "valid" and not exact(before.get(field), after.get(field)):
                problems.append("last3f_original_valid_alias_changed")
            if new_state == "valid":
                numbers.add(new_number)
        if len(numbers) != 1:
            problems.append("last3f_final_numeric_alias_conflict")
        source, metadata = evidence.get("source", {}), after.get("_last3f_gap_repair", {})
        if not isinstance(source, dict) or not isinstance(metadata, dict):
            return problems + ["last3f_evidence_object_required"]
        if (evidence.get("repair_kind") != "last3f_clock" or evidence.get("normal_values_overwritten") is not False
                or evidence.get("zero_fill") is not False or source.get("http_status") != 200
                or source.get("all_runner_identity_verified") is not True
                or source.get("visible_race_date_verified") is not True
                or source.get("historical_capture_time_proven") is not False
                or source.get("raw_digest_verification") != "verified_against_bytes"
                or not HEX.fullmatch(str(source.get("raw_body_sha256", "")))
                or not _utc_timestamp(source.get("observed_at_utc"))):
            problems.append("last3f_source_evidence_invalid")
        try:
            if _url_race_id(source.get("source_url", "")) != entry["race_id"]:
                problems.append("last3f_source_url_race_mismatch")
        except (TypeError, ValueError):
            problems.append("last3f_source_url_invalid")
        expected_raw = {field: before.get(field) for field in CLOCK_FIELDS}
        if (metadata.get("version") != "last3f-gap-repair-v1" or metadata.get("batch_id") != entry["batch_id"]
                or not exact(metadata.get("raw_values"), expected_raw)
                or metadata.get("source_body_sha256") != source.get("raw_body_sha256")
                or metadata.get("source_url") != source.get("source_url")
                or metadata.get("source_race_date") != evidence.get("source_race_date")
                or metadata.get("source_observed_at_utc") != source.get("observed_at_utc")
                or metadata.get("historical_prestart_capture_proven") is not False
                or metadata.get("legacy_last3f_rank_recomputed") is not False
                or not _utc_timestamp(metadata.get("repaired_at_utc"))
                or "history_race_date < target_race_date" not in str(metadata.get("usage", ""))):
            problems.append("last3f_repair_metadata_invalid")
    else:
        problems.append("unknown_feature_repair_kind")
    return sorted(set(problems))


def read_selected_journal(connection, batches, *, yield_every=1024, pause_seconds=.003):
    if not batches or any(kind not in {"original", "pedigree", "last3f"} for kind in batches.values()):
        raise ValueError("feature-audit-explicit-batch-map-required")
    marks = ",".join("?" for _ in batches)
    query = ("SELECT id,batch_id,table_name,row_key,race_id,before_data,after_data,before_sha256,after_sha256,evidence_json "
             f"FROM data_quality_repair_journal_v1 WHERE batch_id IN ({marks}) ORDER BY id")
    chains, issues, counts, examples = defaultdict(list), Counter(), Counter(), []
    for identifier, batch, table, key, rid, before, after, bh, ah, raw_evidence in connection.execute(query, tuple(batches)):
        counts[batch] += 1
        failures = []
        if table not in {"races_ultimate", "race_results_ultimate", "return_tables_ultimate"}:
            failures.append("unexpected_journal_table")
        if (before is not None and (not isinstance(before, str) or previous.sha_text(before) != bh)) or (before is None and bh is not None):
            failures.append("before_hash_mismatch")
        if not isinstance(after, str) or previous.sha_text(after) != ah:
            failures.append("after_hash_mismatch")
        try:
            evidence = json.loads(raw_evidence)
        except (TypeError, ValueError):
            evidence = None
            failures.append("journal_evidence_invalid_json")
        entry = {"id": identifier, "batch_id": batch, "kind": batches[batch], "table": table,
                 "race_id": rid, "before": before, "after": after, "evidence": evidence}
        chain = chains[(table, str(key))]
        if chain and chain[-1]["after"] != before:
            failures.append("global_cross_batch_journal_discontinuity")
        failures.extend(validate_feature_entry(entry))
        chain.append(entry)
        issues.update(failures)
        if failures and len(examples) < 30:
            examples.append({"journal_id": identifier, "batch_id": batch, "table": table, "row_key": key, "issues": failures})
        if yield_every and sum(counts.values()) % yield_every == 0:
            time.sleep(pause_seconds)
    return dict(chains), {"issues": dict(issues), "issues_examples": examples,
        "entries_by_batch": {batch: counts[batch] for batch in batches},
        "empty_selected_batches": [batch for batch in batches if not counts[batch]],
        "entry_count": sum(counts.values()), "global_id_ordered": True}


def audit(before_path, current_path, *, batches, output_dir, yield_every=1024, pause_seconds=.003):
    started = time.perf_counter()
    before_path, current_path, output_dir = Path(before_path).resolve(strict=True), Path(current_path).resolve(strict=True), Path(output_dir)
    if not 1 <= yield_every <= 10000 or not .001 <= pause_seconds <= 1:
        raise ValueError("feature-audit-yield-budget-invalid")
    stat_before, outside_before = current_path.stat(), previous.external_epoch(current_path)
    accumulator, comparisons, used = previous.QualityAccumulator(), [], set()
    with closing(previous.open_ro(before_path, immutable=True)) as before, closing(previous.open_ro(current_path)) as current:
        version_before = current.execute("PRAGMA data_version").fetchone()[0]
        epoch_before = previous.epoch(current)
        chains, journal = read_selected_journal(current, batches, yield_every=yield_every, pause_seconds=pause_seconds)
        for table in previous.TABLES:
            callback = accumulator.add_metadata if table == "races_ultimate" else accumulator.add_runner if table == "race_results_ultimate" else None
            result, covered = previous.stream_compare(before, current, table, chains, on_current=callback,
                yield_every=yield_every, pause_seconds=pause_seconds)
            comparisons.append(result)
            used.update(covered)
        epoch_after = previous.epoch(current)
        current.rollback()
        version_after = current.execute("PRAGMA data_version").fetchone()[0]
    outside_after, stat_after = previous.external_epoch(current_path), current_path.stat()
    scope_issues, scope_examples = Counter(), []
    for chain in chains.values():
        for entry in chain:
            if entry["kind"] == "original":
                continue
            rid = entry["race_id"]
            metadata = accumulator.metadata.get(str(rid), {})
            day = normalized_date(metadata.get("date"))
            if not day or not is_jra_race_id(rid, day) or not PERIOD[0] <= day <= PERIOD[1]:
                scope_issues["feature_repair_outside_jra_period"] += 1
                if len(scope_examples) < 20:
                    scope_examples.append({"journal_id": entry["id"], "batch_id": entry["batch_id"], "race_id": rid, "race_date": day})
            evidence = entry["evidence"] if isinstance(entry["evidence"], dict) else {}
            if entry["kind"] == "last3f" and evidence.get("source_race_date") != day:
                scope_issues["last3f_source_current_date_conflict"] += 1
    unused = sorted([list(key) for key in set(chains) - used])
    journal.update(chain_count=len(chains), used_chain_count=len(used), unused_chains=unused,
                   scope_issues=dict(scope_issues), scope_issues_examples=scope_examples)
    source_stable = epoch_before is not None and epoch_before == epoch_after == outside_before == outside_after
    version_stable = version_before == version_after
    identity_stable = (stat_before.st_dev, stat_before.st_ino) == (stat_after.st_dev, stat_after.st_ino)
    preservation = (all(item.get("passed") for item in comparisons) and not journal["issues"] and not scope_issues and not unused)
    result = {"schema": SCHEMA, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "before_snapshot": str(before_path), "current_database": str(current_path), "selected_batches": batches,
        "normal_records_preserved_and_only_authorized_journaled_updates": preservation,
        "audit_pass": preservation and source_stable and version_stable and identity_stable,
        "comparison": comparisons, "journal": journal, "quality": accumulator.finish(),
        "source_revision": {"outside_before": outside_before, "transaction_before": epoch_before,
            "transaction_after": epoch_after, "outside_after": outside_after, "source_stable": source_stable,
            "data_version_before": version_before, "data_version_after": version_after,
            "database_write_free_during_read": version_stable, "database_file_identity_stable": identity_stable,
            "stable_and_latest": source_stable and version_stable and identity_stable},
        "execution": {"read_only": True, "network_requests": 0, "database_copy_or_physical_hash": False,
            "quick_check": False, "yield_every_rows": yield_every, "yield_seconds": pause_seconds,
            "duration_seconds": time.perf_counter() - started},
        "interpretation": "Source-field recovery only; not historical prestart capture, model accuracy or return validation.",
        "source_code_sha256": {name: previous.small_file_sha(ROOT / name) for name in (
            "scripts/audit_feature_gap_updates.py", "scripts/audit_quality_repair_after_v2.py",
            "scripts/audit_current_runner_quality.py", "python-api/experiments/dataset.py",
            "python-api/scraping/acquisition_scope.py", "python-api/scraping/last3f_evidence.py",
            "python-api/scraping/saved_horse_reuse.py")}}
    path = output_dir / "feature_gap_after_quality.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return result, path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--original-batch", default=ORIGINAL_BATCH, choices=[ORIGINAL_BATCH])
    parser.add_argument("--pedigree-batch", action="append", default=[])
    parser.add_argument("--last3f-batch", action="append", default=[])
    parser.add_argument("--before-snapshot", type=Path, default=ROOT / "reports/generated/data-quality/current-audit/20261017T024204Z/snapshot.sqlite3")
    parser.add_argument("--current-db", type=Path, default=ROOT / "keiba/data/keiba_ultimate.db")
    parser.add_argument("--repairs-completed", action="store_true", required=True)
    args = parser.parse_args(argv)
    if not args.pedigree_batch and not args.last3f_batch:
        raise ValueError("feature-audit-new-batch-required")
    labels = [(args.original_batch, "original"), *((batch, "pedigree") for batch in args.pedigree_batch),
              *((batch, "last3f") for batch in args.last3f_batch)]
    if len({batch for batch, _kind in labels}) != len(labels) or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", batch) for batch, _kind in labels):
        raise ValueError("feature-audit-duplicate-or-invalid-batch")
    original_dir = ROOT / "reports/generated/data-quality/repair" / ORIGINAL_BATCH
    original_plan = json.loads((original_dir / "plan.json").read_text(encoding="utf-8"))
    original_receipt = json.loads((original_dir / "plan_receipt.json").read_text(encoding="utf-8"))
    if original_receipt.get("plan_sha256") != previous.small_file_sha(original_dir / "plan.json"):
        raise ValueError("feature-audit-original-plan-changed")
    source_audit = Path(original_plan["source_audit"])
    snapshot_receipt = json.loads((source_audit / "snapshot_receipt.json").read_text(encoding="utf-8-sig"))
    if (args.before_snapshot.resolve(strict=True) != Path(snapshot_receipt["snapshot_path"]).resolve(strict=True)
            or snapshot_receipt.get("quick_check") != "ok" or not snapshot_receipt.get("snapshot_sha256")
            or args.current_db.resolve(strict=True) != Path(original_plan["database"]).resolve(strict=True)):
        raise ValueError("feature-audit-original-source-binding-invalid")
    priority = previous.lower_priority()
    output = checked_run_directory(ROOT, args.output_dir, create=True)
    result, path = audit(args.before_snapshot, args.current_db, batches=dict(labels), output_dir=output)
    print(json.dumps({"output": str(path), "sha256": previous.small_file_sha(path), "priority": priority,
        "preservation_pass": result["normal_records_preserved_and_only_authorized_journaled_updates"],
        "stable_and_latest": result["source_revision"]["stable_and_latest"],
        "journal_issues": result["journal"]["issues"], "duration_seconds": result["execution"]["duration_seconds"]}, ensure_ascii=False))
    return 0 if result["audit_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

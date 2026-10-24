"""Collector-friendly, read-only logical after-audit; no full-file hashes/copy.

Run ONLY after the repair executor has stopped. Existing reports and both
databases are read-only. This scans each protected source table once per DB,
compares original SQLite row IDs, and validates raw journal chains. It never
trains models, requests network data, or certifies original pre-race capture.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "python-api", ROOT / "scripts"):
    sys.path.insert(0, str(directory))
import audit_current_runner_quality as raw_audit
from experiments import dataset as canonical
from services import prediction_source_revision as revisions

TABLES = ("races_ultimate", "race_results_ultimate", "return_tables_ultimate", "training_data", "speed_figures")
SCHEMA = "quality-repair-after-audit-v2"


def sha_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def small_file_sha(path):
    if Path(path).stat().st_size > 16 * 1024 * 1024:
        raise ValueError("only small code/report files may be physically hashed")
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def lower_priority():
    try:
        if os.name == "nt":
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel.SetPriorityClass.restype = ctypes.c_int
            if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00004000):
                raise OSError("SetPriorityClass failed")
            return "Windows BELOW_NORMAL_PRIORITY_CLASS"
        os.nice(10)
        return "nice +10"
    except (OSError, AttributeError):
        return "priority-unavailable; cooperative yield remains enabled"


def open_ro(path, *, immutable=False):
    connection = sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro" +
        ("&immutable=1" if immutable else ""), uri=True, timeout=3, isolation_level=None)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA cache_size=-4096")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("BEGIN")
    return connection


def epoch(connection):
    value = revisions._validated_state(connection)
    return list(value) if value is not None else None


def external_epoch(path):
    with closing(open_ro(path)) as connection:
        return epoch(connection)


def read_journal(connection, batch_id):
    chains, issues = defaultdict(list), Counter()
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "data_quality_repair_journal_v1" not in tables:
        return {}, {"journal_table_missing": 1}
    query = ("SELECT id,table_name,row_key,race_id,before_data,after_data,before_sha256,after_sha256 "
             "FROM data_quality_repair_journal_v1 WHERE batch_id=? ORDER BY id")
    for identifier, table, key, race_id, before, after, before_hash, after_hash in connection.execute(query, (batch_id,)):
        if table not in {"races_ultimate", "race_results_ultimate", "return_tables_ultimate"}:
            issues["unexpected_journal_table"] += 1
        if (before is not None and sha_text(before) != before_hash) or (before is None and before_hash is not None):
            issues["before_hash_mismatch"] += 1
        if not isinstance(after, str) or sha_text(after) != after_hash:
            issues["after_hash_mismatch"] += 1
        chain = chains[(table, str(key))]
        if chain and chain[-1]["after"] != before:
            issues["journal_chain_discontinuity"] += 1
        chain.append({"id": identifier, "race_id": race_id, "before": before, "after": after})
    return dict(chains), dict(issues)


def sql_row_equal(left, right):
    return len(left) == len(right) and all(type(a) is type(b) and a == b for a, b in zip(left, right))


def payout_matches(row, columns, journal_value, journal_race_id):
    value = json.loads(journal_value)
    actual = dict(zip(columns, row))
    amount = canonical._number(value.get("payout"))
    return (str(journal_race_id) == str(actual.get("race_id"))
            and (value.get("race_id") is None or str(value["race_id"]) == str(journal_race_id))
            and value.get("bet_type") == actual.get("bet_type")
            and str(value.get("combinations")) == actual.get("combinations")
            and amount > 0 and amount.is_integer() and int(amount) == actual.get("payout")
            and value.get("popularity") == actual.get("popularity"))


def journal_covers(table, old, new, columns, chains, used):
    if new is None:
        return False
    values = dict(zip(columns, new))
    key = str(values["race_id"]) if table == "races_ultimate" else str(new[0])
    chain = chains.get((table, key))
    if not chain:
        return False
    if any(str(entry["race_id"]) != str(values.get("race_id")) for entry in chain):
        return False
    if old is None:
        if chain[0]["before"] is not None:
            return False
    elif table in {"races_ultimate", "race_results_ultimate"}:
        old_values = dict(zip(columns, old))
        if chain[0]["before"] != old_values["data"]:
            return False
        # Journaling JSON is not permission to mutate acquisition identity/time.
        if old_values["race_id"] != values["race_id"] or old_values.get("created_at") != values.get("created_at"):
            return False
    else:
        return False  # Existing payouts/auxiliary rows must never be updated.
    try:
        matches = (chain[-1]["after"] == values["data"] if table in {"races_ultimate", "race_results_ultimate"}
                   else payout_matches(new, columns, chain[-1]["after"], chain[-1]["race_id"]))
    except (TypeError, ValueError, KeyError):
        matches = False
    if matches:
        used.add((table, key))
    return matches


def stream_compare(before, after, table, chains, *, on_current=None, yield_every=1024, pause_seconds=.003):
    old_columns = [row[1] for row in before.execute(f"PRAGMA table_info({table})")]
    new_columns = [row[1] for row in after.execute(f"PRAGMA table_info({table})")]
    if old_columns != new_columns or not old_columns:
        return {"table": table, "schema_unchanged": False, "pass": False}, set()
    query = f"SELECT rowid,* FROM {table} ORDER BY rowid"
    left, right = before.execute(query), after.execute(query)
    columns = ["_rowid", *old_columns]
    a, b = left.fetchone(), right.fetchone()
    counts = Counter()
    used, examples = set(), []
    iterations = 0
    while a is not None or b is not None:
        if a is None or (b is not None and b[0] < a[0]):
            old, new = None, b
            counts["inserted"] += 1
            b = right.fetchone()
        elif b is None or a[0] < b[0]:
            old, new = a, None
            counts["deleted"] += 1
            a = left.fetchone()
        else:
            old, new = a, b
            counts["unchanged" if sql_row_equal(a, b) else "updated"] += 1
            a, b = left.fetchone(), right.fetchone()
        counts["before_rows"] += old is not None
        counts["after_rows"] += new is not None
        if new is not None and on_current:
            on_current(dict(zip(columns, new)))
        changed = old is None or new is None or not sql_row_equal(old, new)
        if changed:
            covered = journal_covers(table, old, new, columns, chains, used)
            counts["journal_covered_changes" if covered else "unauthorized_changes"] += 1
            if not covered and len(examples) < 20:
                examples.append({"old_rowid": old[0] if old else None, "new_rowid": new[0] if new else None})
        iterations += 1
        if yield_every and iterations % yield_every == 0:
            time.sleep(pause_seconds)
    result = {key: counts[key] for key in ("before_rows", "after_rows", "unchanged", "updated", "inserted",
              "deleted", "journal_covered_changes", "unauthorized_changes")}
    result.update(table=table, schema_unchanged=True, unauthorized_examples=examples,
                  passed=not counts["deleted"] and not counts["unauthorized_changes"])
    return result, used


def new_bucket():
    return {"runner_rows": 0, "json_object_rows": 0, "fields": defaultdict(Counter),
            "alias_conflicts": Counter(), "time_missing_by_finish": Counter()}


class QualityAccumulator:
    def __init__(self):
        self.metadata = {}
        self.metadata_issues = Counter()
        self.races = defaultdict(lambda: {"rows": 0, "numbers": set(), "horses": set(), "issues": Counter(),
            "settled": 0, "nonfinish": 0, "time_missing": 0, "card_rows": 0, "annotation_rows": 0})
        self.scopes = {name: new_bucket() for name in ("all_domains", "jra_flat_201601_202609", "jra_flat_october_before_today")}
        self.yearly = {name: defaultdict(new_bucket) for name in self.scopes}

    def add_metadata(self, row):
        rid = str(row["race_id"])
        try:
            info = json.loads(row["data"])
            if not isinstance(info, dict):
                raise ValueError("not-object")
        except (ValueError, TypeError):
            self.metadata_issues["invalid_json_or_nonobject"] += 1
            info = {}
        canonical_meta, reason = canonical._race_metadata(rid, info)
        if reason:
            self.metadata_issues[reason] += 1
        self.metadata[rid] = {"info": info, "canonical": canonical_meta,
            "date": canonical._date(canonical._first(info, "date", "race_date"))}
        day = self.metadata[rid]["date"]
        distance = canonical._number(info.get("distance"))
        valid = bool(day and len(rid) == 12 and rid.isdigit() and rid[:4] == day[:4]
                     and distance > 0 and distance != float("inf")
                     and raw_audit.canonical._text(info.get("venue")))
        if rid[4:6] in canonical.JRA_VENUES:
            valid = valid and canonical._venue(info.get("venue")) == canonical.JRA_VENUES[rid[4:6]]
        self.metadata[rid]["structural_valid"] = valid

    def scope_names(self, rid):
        meta = self.metadata.get(rid)
        names = ["all_domains"]
        if meta and meta["canonical"]:
            if "20160101" <= meta["date"] <= "20260930":
                names.append("jra_flat_201601_202609")
            if "20261001" <= meta["date"] <= "20261016":
                names.append("jra_flat_october_before_today")
        return names

    def add_runner(self, row):
        rid = str(row["race_id"])
        meta = self.metadata.get(rid)
        race = self.races[rid]
        race["rows"] += 1
        year = (meta["date"][:4] if meta and meta["date"] else rid[:4])
        buckets = [bucket for name in self.scope_names(rid) for bucket in (self.scopes[name], self.yearly[name][year])]
        for bucket in buckets:
            bucket["runner_rows"] += 1
        try:
            horse = json.loads(row["data"])
            if not isinstance(horse, dict):
                raise ValueError("not-object")
        except (ValueError, TypeError):
            race["issues"]["invalid_json_or_nonobject"] += 1
            return
        count = raw_audit.normalize("horse_number", (meta or {}).get("info", {}).get("num_horses"))[1]
        fields, conflicts = {}, []
        for field, aliases in raw_audit.ALIASES.items():
            value = raw_audit.selected(horse, aliases)
            status, parsed = raw_audit.normalize(field, value, count)
            if field == "finish" and status == "invalid" and re.fullmatch(r"[1-9]\d*[（(]降[）)]", str(value)):
                status = "adjudication_suffix_unparsed"
            fields[field] = (status, parsed)
            supplied = {parsed_value for name in aliases if raw_audit.present(horse.get(name))
                for supplied_status, parsed_value in [raw_audit.normalize(field, horse[name], count)] if supplied_status == "valid"}
            if len(supplied) > 1:
                conflicts.append(field)
        sex_age = re.fullmatch(r"(牡|牝|セ|セン)(\d+)", str(horse.get("sex_age") or ""))
        if sex_age:
            for field, value in (("sex", sex_age[1]), ("age", sex_age[2])):
                if fields[field][0] == "missing":
                    fields[field] = raw_audit.normalize(field, value)
        if meta and meta["date"]:
            fields["race_date"] = ("valid", meta["date"])
        going = raw_audit.canonical._text((meta or {}).get("info", {}).get("field_condition"))
        fields["field_condition"] = (("valid", going) if going in {"良", "稍重", "重", "不良"} else
            ("recognized_abbreviation", going) if going in {"稍", "不"} else ("missing", None) if not going else ("invalid", None))
        corner, corner_conflict, _ = raw_audit.corner_state(horse, count)
        fields["corner_positions"] = (corner, None)
        if corner_conflict:
            conflicts.append("corner_text_vs_list")
        for field, collection in (("horse_number", "numbers"), ("horse_id", "horses")):
            status, value = fields[field]
            if status != "valid":
                race["issues"][field + "_missing_or_invalid"] += 1
            elif value in race[collection]:
                race["issues"][field + "_duplicate"] += 1
            else:
                race[collection].add(value)
        finish = fields["finish"][0]
        if finish in {"valid", "legitimate_nonfinish", "adjudication_suffix_unparsed"}:
            race["settled"] += 1
        if finish == "adjudication_suffix_unparsed":
            race["annotation_rows"] += 1
        if finish == "legitimate_nonfinish":
            race["nonfinish"] += 1
        elif fields["time_seconds"][0] != "valid":
            race["time_missing"] += 1
        race["card_rows"] += bool(horse.get("_shutuba") or horse.get("_provisional_result"))
        if horse.get("race_id") is not None and str(horse["race_id"]) != rid:
            race["issues"]["embedded_race_id_conflict"] += 1
        if not meta:
            race["issues"]["orphan_runner"] += 1
        for conflict in conflicts:
            race["issues"]["alias_conflict:" + conflict] += 1
        for bucket in buckets:
            bucket["json_object_rows"] += 1
            for field, (status, _) in fields.items():
                bucket["fields"][field][status] += 1
            bucket["alias_conflicts"].update(conflicts)
            if fields["time_seconds"][0] != "valid":
                bucket["time_missing_by_finish"][finish] += 1

    def race_status(self, rid):
        meta, state = self.metadata.get(rid), self.races.get(rid)
        if meta is None:
            return "missing_header"
        if not meta["structural_valid"]:
            return "metadata_invalid"
        if not state or not state["rows"]:
            return "missing_runner_rows"
        declared = raw_audit.normalize("horse_number", meta["info"].get("num_horses"))[1]
        if declared != state["rows"]:
            return "runner_count_mismatch"
        if state["issues"]:
            return "identity_json_or_alias_issue"
        info = meta["info"]
        if (state["settled"] != state["rows"] or state["card_rows"] or info.get("_shutuba")
                or info.get("_provisional_result") or info.get("_provisional")
                or info.get("result_status") in {"pending", "provisional", "unsettled"}):
            return "results_pending"
        if state["time_missing"]:
            return "mandatory_time_missing"
        return "mandatory_complete"

    def finish(self):
        def finalized(bucket):
            return {"runner_rows": bucket["runner_rows"], "json_object_rows": bucket["json_object_rows"],
                "fields": {field: {"counts": dict(counts), "valid_pct": round(100 * counts["valid"] / bucket["json_object_rows"], 5)}
                    for field, counts in bucket["fields"].items()}, "alias_conflicts": dict(bucket["alias_conflicts"]),
                    "time_missing_by_finish": dict(bucket["time_missing_by_finish"])}
        mismatches, annotations, statuses, issues = [], [], Counter(), Counter()
        for rid, meta in self.metadata.items():
            state = self.races.get(rid, {})
            statuses[self.race_status(rid)] += 1
            declared = raw_audit.normalize("horse_number", meta["info"].get("num_horses"))[1]
            if declared != state.get("rows", 0):
                mismatches.append({"race_id": rid, "race_date": meta["date"], "declared": declared,
                    "saved": state.get("rows", 0), "scope": "jra" if rid[4:6] in canonical.JRA_VENUES else "non_jra"})
            if state.get("annotation_rows"):
                annotations.append({"race_id": rid, "rows": state["annotation_rows"]})
            issues.update(state.get("issues", {}))
        target_annotations = [item for item in annotations if item["race_id"][4:6] in canonical.JRA_VENUES
            and self.metadata[item["race_id"]]["date"] is not None
            and "20160101" <= self.metadata[item["race_id"]]["date"] <= "20261016"]
        return {"scope_definitions": {"jra_flat_201601_202609": "metadata-valid flat turf/dirt, not complete eligible training cohort",
                "jra_flat_october_before_today": "same, actual header dates 20261001..20261016"},
            "counts_are_not_capture_or_official_completeness_proof": True,
            "metadata_rows": len(self.metadata), "metadata_issues": dict(self.metadata_issues),
            "raw_race_status": dict(statuses), "runner_issues": dict(issues),
            "declared_runner_count_mismatches": mismatches, "remaining_unparsed_demotion_races": annotations,
            "remaining_target_jra_demotion_annotations": target_annotations,
            "remaining_outside_target_demotion_annotations": [item for item in annotations if item not in target_annotations],
            "scopes": {name: finalized(bucket) for name, bucket in self.scopes.items()},
            "by_year": {scope: {year: finalized(bucket) for year, bucket in sorted(groups.items())}
                        for scope, groups in self.yearly.items()}}


def audit(before_path, current_path, *, batch_id, plan, baseline_coverage, output_dir, discovery=None, yield_every=1024, pause_seconds=.003):
    started = time.perf_counter()
    before_path, current_path = Path(before_path).resolve(strict=True), Path(current_path).resolve(strict=True)
    file_before = current_path.stat()
    outside_before = external_epoch(current_path)
    accumulator, comparisons, used = QualityAccumulator(), [], set()
    with closing(open_ro(before_path, immutable=True)) as before, closing(open_ro(current_path)) as current:
        database_version_before = current.execute("PRAGMA data_version").fetchone()[0]
        transaction_before = epoch(current)
        chains, journal_issues = read_journal(current, batch_id)
        for table in TABLES:
            callback = accumulator.add_metadata if table == "races_ultimate" else accumulator.add_runner if table == "race_results_ultimate" else None
            compared, covered = stream_compare(before, current, table, chains, on_current=callback,
                                               yield_every=yield_every, pause_seconds=pause_seconds)
            comparisons.append(compared)
            used.update(covered)
        transaction_after = epoch(current)
        current.rollback()
        database_version_after = current.execute("PRAGMA data_version").fetchone()[0]
    outside_after = external_epoch(current_path)
    file_after = current_path.stat()
    quality = accumulator.finish()
    absent_ids = baseline_coverage.get("jra_expected_id_audit_201601_202609", {}).get("raw_absent_expected_race_ids", [])
    pending_ids = [item["race_id"] for item in plan.get("targets", [])
                   if set(item.get("reasons", [])) & {"results_not_saved", "recent_results_not_saved"}]
    discovery_ids = sorted({rid for item in (discovery or []) for rid in item.get("race_ids", [])})
    original_ids = [item["race_id"] for item in plan.get("targets", [])]
    def target_status(ids):
        states = {rid: accumulator.race_status(rid) for rid in ids}
        return {"target_count": len(states), "counts": dict(Counter(states.values())), "race_status": states}
    journal_unused = sorted([list(key) for key in set(chains) - used])
    core_stable = transaction_before is not None and transaction_before == transaction_after == outside_before == outside_after
    database_stable = database_version_before == database_version_after
    identity_stable = (file_before.st_dev, file_before.st_ino) == (file_after.st_dev, file_after.st_ino)
    stable = core_stable and database_stable and identity_stable
    preservation_pass = (all(item.get("passed") for item in comparisons) and not journal_issues and not journal_unused)
    result = {"schema": SCHEMA, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "batch_id": batch_id, "before_snapshot": str(before_path), "current_database": str(current_path),
        "physical_db_hash_or_copy_performed": False, "quick_check_performed": False,
        "consistent_read_transaction": True, "source_revision": {"outside_before": outside_before,
            "transaction_before": transaction_before, "transaction_after": transaction_after, "outside_after": outside_after,
            "core_generation_stable": core_stable, "database_data_version_before": database_version_before,
            "database_data_version_after": database_version_after, "database_write_free_during_read": database_stable,
            "database_file_identity_stable": identity_stable,
            "stable_and_latest": stable, "warning": None if stable else "Concurrent source changes or unavailable epoch; results describe the bound read transaction only."},
        "comparison": comparisons, "journal": {"chain_count": len(chains), "used_chain_count": len(used),
            "issues": journal_issues, "unused_chains": journal_unused},
        "normal_records_preserved_and_only_journaled_additions_or_updates": preservation_pass,
        "quality": quality, "original_186_expected_ids": target_status(absent_ids),
        "original_75_uncollected_results": target_status(pending_ids),
        "all_plan_targets": target_status([item["race_id"] for item in plan.get("targets", [])]),
        "discovery_48_targets": target_status(discovery_ids),
        "all_repair_targets_including_discovery": target_status(sorted(set(original_ids) | set(discovery_ids))),
        "execution": {"read_only": True, "yield_every_rows": yield_every, "yield_seconds": pause_seconds,
            "duration_seconds": time.perf_counter() - started, "network": False, "repairs": False, "training": False,
            "provenance_note": "Field availability and repair journal are not original pre-race capture proof."},
        "source_code_sha256": {path: small_file_sha(ROOT / path) for path in (
            "scripts/audit_quality_repair_after_v2.py", "scripts/audit_current_runner_quality.py", "python-api/experiments/dataset.py")},
        "v1_difference": "Payout JSON can omit race_id; the independently journaled race_id is then required to match the actual row. Present embedded race_id still must match. All payout columns and journal hashes remain strict; discovery targets and structural time-missing context are explicit."}
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "after_quality.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return result, path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--before-snapshot", type=Path, required=True)
    parser.add_argument("--current-db", type=Path, required=True)
    parser.add_argument("--repair-completed", action="store_true", required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve(strict=True)
    if not run_dir.is_relative_to((ROOT / "reports/generated/data-quality/repair").resolve(strict=True)):
        raise ValueError("unexpected repair run directory")
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    if plan["batch_id"] != run_dir.name or Path(plan["database"]).resolve() != args.current_db.resolve(strict=True):
        raise ValueError("plan/current database binding mismatch")
    source_audit = Path(plan["source_audit"]).resolve(strict=True)
    receipt_path = source_audit / "snapshot_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    if (args.before_snapshot.resolve(strict=True) != Path(receipt["snapshot_path"]).resolve(strict=True)
            or receipt.get("quick_check") != "ok" or not receipt.get("snapshot_sha256")):
        raise ValueError("before snapshot receipt binding invalid")
    coverage = json.loads((Path(plan["source_audit"]) / "acquisition_coverage.json").read_text(encoding="utf-8"))
    priority = lower_priority()
    discovery = json.loads((run_dir / "discovery.json").read_text(encoding="utf-8"))
    result, path = audit(args.before_snapshot, args.current_db, batch_id=run_dir.name, plan=plan,
        baseline_coverage=coverage, output_dir=run_dir / "post-audit-v2", discovery=discovery)
    print(json.dumps({"output": str(path), "sha256": small_file_sha(path), "priority": priority,
        "preservation_pass": result["normal_records_preserved_and_only_journaled_additions_or_updates"],
        "latest": result["source_revision"]["stable_and_latest"], "seconds": result["execution"]["duration_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

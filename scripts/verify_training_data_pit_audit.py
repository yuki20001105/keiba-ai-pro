"""Independent SQL/stdlib recount of a completed training-data PIT audit.

No training-audit implementation is imported. PyArrow reads cohort KEYS only;
training dates are event dates, and DB insertion dates never prove availability.
Source DBs are immutable/read-only; the final verification receipt is exclusive.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strict_date(value, *, training=False):
    # Accept exactly the audit's three source formats, validating the calendar
    # independently through datetime instead of pandas.
    if not isinstance(value, str):
        return None
    text = value.strip()
    if re.fullmatch(r"[0-9]{8}|[0-9]{4}/[0-9]{2}/[0-9]{2}|[0-9]{4}-[0-9]{2}-[0-9]{2}", text) is None:
        return None
    compact = text.replace("/", "").replace("-", "")
    try:
        return datetime(int(compact[:4]), int(compact[4:6]), int(compact[6:8])).date()
    except ValueError:
        return None


def event_class(training_date, race_date):
    workout = strict_date(training_date, training=True)
    race = strict_date(race_date)
    if workout is None or race is None:
        return "D_DATE_OR_RACE_UNKNOWN"
    if workout < race:
        return "A_BEFORE_RACE"
    return "B_SAME_DATE_TIME_UNKNOWN" if workout == race else "C_AFTER_RACE"


def _created_day(value):
    try:
        return datetime.fromisoformat(value).date() if isinstance(value, str) else None
    except ValueError:
        return None


def cohort_keys(path, expected_sha256):
    require(sha256(path) == expected_sha256, "cohort hash mismatch before key read")
    import pyarrow.parquet as pq
    keys = set()
    for batch in pq.ParquetFile(path).iter_batches(columns=["race_id", "horse_number"]):
        columns = batch.to_pydict()
        for race, number in zip(columns["race_id"], columns["horse_number"]):
            require(isinstance(race, str) and re.fullmatch(r"[0-9]{12}", race), "invalid cohort race")
            require(not isinstance(number, bool) and number is not None and int(number) == number
                    and 1 <= int(number) <= 18, "invalid cohort horse number")
            key = (race, int(number))
            require(key not in keys, "duplicate cohort key")
            keys.add(key)
    require(sha256(path) == expected_sha256, "cohort changed while reading")
    return keys


def recount(snapshot, expected_sha256, keys):
    """Count every workout once, with independent indexed runner lookups."""
    snapshot = Path(snapshot).resolve()
    require(sha256(snapshot) == expected_sha256, "snapshot hash mismatch")
    wal = Path(str(snapshot) + "-wal")
    require(not wal.exists() or wal.stat().st_size == 0, "pending WAL in snapshot")
    db = sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    totals, classes, years = Counter(), Counter(), defaultdict(Counter)
    race_sets, runner_sets = defaultdict(set), defaultdict(set)
    all_races, all_keys, matched_races, matched_keys = set(), set(), set(), set()
    records = {}
    cohort_races = {key[0] for key in keys}
    outside_cohort = Counter()
    prefix_years, prefix_races, prefix_keys = defaultdict(Counter), defaultdict(set), defaultdict(set)
    try:
        db.execute("PRAGMA query_only=ON")
        require(db.execute("SELECT count(*) FROM (SELECT race_id FROM races_ultimate GROUP BY race_id HAVING count(*)>1)").fetchone()[0] == 0,
                "ambiguous canonical race keys")
        schema = [row[1] for row in db.execute("PRAGMA table_info(training_data)")]
        capture_columns = sorted(set(schema) & {"source_time", "source_timestamp", "source_observed_at", "available_at", "received_at"})
        require(not capture_columns, "new source-availability schema requires independent evidence review")
        query = """SELECT t.id,t.race_id,t.horse_number,t.training_date,t.created_at,r.race_id AS joined_race_id,r.race_date,
            (SELECT count(*) FROM race_results_ultimate rr
             WHERE rr.race_id=t.race_id AND rr.horse_number=t.horse_number) AS number_match_rows
            FROM training_data t LEFT JOIN races_ultimate r ON r.race_id=t.race_id ORDER BY t.id"""
        for row in db.execute(query):
            item = dict(row)
            ident = item["id"]
            require(ident not in records, "duplicate source training id")
            rid, number = item["race_id"], item["horse_number"]
            race = strict_date(item["race_date"])
            workout = strict_date(item["training_date"], training=True)
            year = str(race.year) if race else "unknown"
            relation = event_class(item["training_date"], item["race_date"])
            item["expected_event_date_class"] = relation
            records[ident] = item
            labels = ["rows"]
            labels.append("race_join_matched" if item["joined_race_id"] is not None else "race_join_missing")
            labels.append("number_join_exact" if item["number_match_rows"] == 1 else
                          "number_join_missing" if item["number_match_rows"] == 0 else "number_join_ambiguous")
            if race is None:
                labels.append("race_date_invalid_or_unknown")
            if workout is None:
                labels.append("training_date_invalid_or_unknown")
            if race and workout and race.year != workout.year:
                labels.append("training_year_differs_from_race_year")
            created = _created_day(item["created_at"])
            registration = "unknown"
            if race and created:
                registration = "before" if created < race else "same" if created == race else "after"
            labels.append("db_insertion_day_" + registration)
            totals.update(labels)
            years[year].update(labels)
            years[year][relation] += 1
            prefix = rid[:4] if isinstance(rid, str) and re.fullmatch(r"[0-9]{12}", rid) else "unknown"
            prefix_years[prefix]["rows"] += 1
            prefix_races[prefix].add(rid)
            prefix_keys[prefix].add((rid, number))
            classes[relation] += 1
            race_sets[year].add(rid)
            runner_sets[year].add((rid, number))
            all_races.add(rid)
            all_keys.add((rid, number))
            if rid in cohort_races:
                totals["target_race_records"] += 1
            if (rid, number) in keys:
                totals["target_runner_records"] += 1
                matched_races.add(rid)
                matched_keys.add((rid, number))
            elif race and race.year in (2021, 2022):
                outside_cohort[rid] += 1
        for name, group in (("duplicate_workout_key_rows", "race_id,horse_number,training_date,course"),
                            ("repeated_race_runner_rows", "race_id,horse_number")):
            totals[name] = db.execute("SELECT COALESCE(sum(n),0) FROM (SELECT count(*) n FROM training_data GROUP BY " + group + " HAVING count(*)>1)").fetchone()[0]
        registry_rows = db.execute("SELECT count(*) FROM races").fetchone()[0]
    finally:
        db.close()
    require(sha256(snapshot) == expected_sha256, "snapshot changed during recount")
    classes = {name: classes[name] for name in ("A_BEFORE_RACE", "B_SAME_DATE_TIME_UNKNOWN", "C_AFTER_RACE", "D_DATE_OR_RACE_UNKNOWN")}
    result = {"rows": totals["rows"], "distinct_races": len(all_races), "distinct_race_runner_keys": len(all_keys),
        "totals": dict(totals), "event_date_classes": classes,
        "year_inventory": {year: {**dict(value), "distinct_races": len(race_sets[year]),
                                    "distinct_race_runner_keys": len(runner_sets[year])} for year, value in sorted(years.items())},
        "race_id_prefix_year_inventory": {year: {"rows": value["rows"], "distinct_races": len(prefix_races[year]),
            "distinct_race_runner_keys": len(prefix_keys[year])} for year, value in sorted(prefix_years.items())},
        "released_clean_cohort": {"cohort_races": len(cohort_races), "cohort_runners": len(keys),
            "training_records": totals["target_runner_records"], "races_with_training_records": len(matched_races),
            "runners_with_training_records": len(matched_keys), "target_race_records": totals["target_race_records"]},
        "raw_2021_22_records_outside_released_cohort": dict(sorted(outside_cohort.items())),
        "legacy_registry_rows": registry_rows, "source_availability_columns": capture_columns,
        "safe_point_in_time_records": 0,
        "availability_limit": "Zero records have proven contemporaneous source capture; a prior event or DB insertion day is not such proof."}
    return result, records


def compare_outputs(output, expected, records):
    output = Path(output)
    inventory_path = output / "training_data_inventory.json"
    audit_path = output / "training_point_in_time_audit.csv"
    fingerprints = {path.name: sha256(path) for path in (inventory_path, audit_path)}
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    require(inventory["rows"] == expected["rows"], "inventory row count differs")
    require(inventory["safe_point_in_time_records"] == 0 and inventory["temporal_feature_gate"] == "NO",
            "unproven capture admitted for features")
    require(set(inventory["event_date_classes"]) <= set(expected["event_date_classes"]), "unknown event class")
    require({key: inventory["event_date_classes"].get(key, 0) for key in expected["event_date_classes"]}
            == expected["event_date_classes"], "event date counts differ")
    years = {str(row["year"]): row for row in inventory["year_inventory"]}
    require(len(years) == len(inventory["year_inventory"]), "duplicate yearly summary")
    for year, stats in expected["race_id_prefix_year_inventory"].items():
        require(year in years, "missing yearly summary " + year)
        for key in ("rows", "distinct_races", "distinct_race_runner_keys"):
            require(years[year][key] == stats[key], "yearly count differs " + year + "/" + key)
    for year, stats in years.items():
        if year not in expected["race_id_prefix_year_inventory"]:
            require(stats["rows"] == 0 and stats["distinct_races"] == 0, "unexpected nonempty summary year")
    target = inventory["cohort_reproduction"]["released_clean"]
    for key in ("cohort_races", "cohort_runners", "training_records", "races_with_training_records", "runners_with_training_records"):
        require(target[key] == expected["released_clean_cohort"][key], "clean cohort count differs " + key)
    for key in ("duplicate_workout_key_rows", "repeated_race_runner_rows"):
        require(inventory[key] == expected["totals"][key], "duplicate count differs " + key)
    seen = set()
    with audit_path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            ident = int(row["training_id"])
            require(ident in records and ident not in seen, "unknown/duplicate audited training id")
            seen.add(ident)
            source = records[ident]
            require(row["race_id"] == source["race_id"], "audit race key differs")
            number = row["horse_number"]
            require((not number and source["horse_number"] is None) or
                    (number and float(number) == source["horse_number"]), "audit horse key differs")
            require(row["training_date_raw"] == (source["training_date"] or ""), "audit training date differs")
            require(row["event_date_class"] == source["expected_event_date_class"], "per-record event class differs")
            for flag in ("source_capture_time_verified", "safe_point_in_time", "registration_is_availability_proof"):
                require(row[flag].lower() in ("false", "0"), "unproven per-record capture")
    require(seen == set(records), "missing audited training records")
    require(all(sha256(output / name) == value for name, value in fingerprints.items()), "audit output changed during verification")
    return fingerprints


def verify_completed_run(output, expected_result_sha256, snapshot_sha, cohort_sha):
    output = Path(output).resolve()
    result_path, plan_path = output / "training-audit-result.json", output / "audit_plan.json"
    require(sha256(result_path) == expected_result_sha256, "completed result hash mismatch")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    require(result["status"] == "completed" and result["audit_plan_sha256"] == sha256(plan_path), "run not completed under frozen plan")
    require(result["snapshot_sha256"] == plan["snapshot_sha256"] == snapshot_sha, "snapshot binding differs")
    require(plan["cohorts"]["released_clean"]["sha256"] == cohort_sha, "cohort binding differs")
    require(result["safe_point_in_time_records"] == 0 and result["temporal_feature_gate"] == "NO"
            and result["race_outcome_columns_loaded"] == [], "result overstates PIT permission")
    for flag in ("performance_metrics_computed", "model_training_performed", "source_mutated"):
        require(result[flag] is False, "forbidden audit action declared " + flag)
    require(sha256(output / "training_source_audit.json") == plan["source_audit_sha256"], "source audit changed")
    require(result["source_sha256"] == plan["source_sha256"], "source code declaration differs")
    repo = Path(__file__).resolve().parents[1]
    for name, digest in result["source_sha256"].items():
        path = (repo / name).resolve()
        require(path.is_relative_to(repo) and sha256(path) == digest, "auditor source changed " + name)
    require({"training_data_inventory.json", "training_point_in_time_audit.csv"} <= set(result["files"]), "required report missing")
    for name, digest in result["files"].items():
        path = (output / name).resolve()
        require(path.is_relative_to(output) and sha256(path) == digest, "audit artifact changed " + name)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--snapshot-sha256", required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--cohort-sha256", required=True)
    parser.add_argument("--expected-result-sha256", required=True)
    args = parser.parse_args()
    receipt_path = args.output / "independent-verification.json"
    require(not receipt_path.exists(), "verification receipt already exists")
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "2"
    import psutil
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
    from snapshot_feature_pit_audit import protected
    before = protected()
    completed = verify_completed_run(args.output, args.expected_result_sha256, args.snapshot_sha256, args.cohort_sha256)
    keys = cohort_keys(args.cohort, args.cohort_sha256)
    result, records = recount(args.snapshot, args.snapshot_sha256, keys)
    fingerprints = compare_outputs(args.output, result, records)
    after = protected()
    require(verify_completed_run(args.output, args.expected_result_sha256, args.snapshot_sha256, args.cohort_sha256) == completed,
            "completed run changed during verification")
    require(sha256(args.snapshot) == args.snapshot_sha256 and sha256(args.cohort) == args.cohort_sha256,
            "source changed before receipt")
    receipt = {"schema": "independent-training-pit-verification-v1", "verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(), "source_sha256": args.snapshot_sha256,
        "cohort_sha256": args.cohort_sha256, "verifier_sha256": sha256(Path(__file__)),
        "verified_result_sha256": args.expected_result_sha256, "completed_artifacts_checked": len(completed["files"]),
        "output_sha256": fingerprints, "independent_counts": result, "protected_before": before,
        "protected_after": after, "model_fits": 0, "network_requests": 0, "issues": []}
    with receipt_path.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"verified": True, "rows": result["rows"], "receipt": str(receipt_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Read-only full runner-quality inventory of a separately receipted snapshot.

This is descriptive raw-data auditing, not eligibility, leakage certification,
repair, acquisition, training, publication, or a source-completeness claim.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))
from experiments import dataset as canonical

SCHEMA = "current-runner-quality-v1"
ALIASES = {
    "finish": ("finish", "finish_position"),
    "time_seconds": ("time_seconds", "finish_time", "time"),
    "odds": ("odds",), "popularity": ("popularity",),
    "horse_id": ("horse_id",), "horse_number": ("horse_number",),
    "bracket_number": ("bracket_number",), "age": ("age",),
    "sex": ("sex",), "burden_weight": ("burden_weight", "jockey_weight"),
    "horse_weight": ("horse_weight", "weight_kg"),
    "horse_weight_change": ("horse_weight_change", "weight_change", "weight_diff"),
    "last_3f_time": ("last_3f_time", "last_3f"),
    "jockey_id": ("jockey_id",), "trainer_id": ("trainer_id",),
    "jockey_name": ("jockey_name",), "trainer_name": ("trainer_name",),
    "sire": ("sire", "sire_name"),
    "damsire": ("damsire", "dam_sire_name"),
    "race_date": ("race_date", "date"),
}
NONFINISH = {"取", "消", "中", "除", "失", "止", "取消", "中止", "除外", "失格",
             "出走取消", "競走中止", "発走除外", "競走除外"}
STATES = ("valid", "missing", "masked", "invalid", "legitimate_nonfinish", "unavailable_placeholder")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def present(value):
    return canonical._text(value) is not None


def selected(row, names):
    return canonical._first(row, *names)


def normalize(field, value, runner_count=None):
    text = canonical._text(value)
    if text is None:
        return "missing", None
    if "*" in text or "＊" in text:
        return "masked", None
    if isinstance(value, bool):
        return "invalid", None
    if field == "finish" and text in NONFINISH:
        return "legitimate_nonfinish", text
    if field in {"horse_id", "jockey_id", "trainer_id"}:
        valid = re.fullmatch(r"[A-Za-z0-9_-]+", text) is not None
        return ("valid", text) if valid else ("invalid", None)
    if field in {"jockey_name", "trainer_name", "sire", "damsire"}:
        valid = len(text) <= 200 and not any(ord(char) < 32 for char in text)
        return ("valid", text) if valid else ("invalid", None)
    if field == "sex":
        normalized = {"セン": "セ", "騸": "セ"}.get(text, text)
        return ("valid", normalized) if normalized in {"牡", "牝", "セ"} else ("invalid", None)
    if field == "race_date":
        result = canonical._date(value)
        return ("valid", result) if result else ("invalid", None)
    if field == "time_seconds":
        if text in NONFINISH or text in {"計不", "計測不能"}:
            return "unavailable_placeholder", None
        number = canonical._time(value)
    else:
        number = canonical._number(value)
    if not math.isfinite(number):
        return "invalid", None
    if number == 0 and field in {"finish", "time_seconds", "odds", "popularity", "horse_weight"}:
        return "unavailable_placeholder", None
    if field in {"finish", "popularity", "horse_number", "bracket_number", "age"}:
        if number != int(number) or number < 1:
            return "invalid", None
        # Program numbers can exceed actual starter count after withdrawals.
        maximum = {"bracket_number": 8, "age": 30, "horse_number": 18}.get(field, runner_count or 100)
        if number > maximum:
            return "invalid", None
    elif field == "odds" and number < 1:
        return "invalid", None
    elif field == "horse_weight" and not 200 <= number <= 1500:
        return "invalid", None
    elif field == "horse_weight_change" and not -200 <= number <= 200:
        return "invalid", None
    elif field == "burden_weight" and not 1 <= number <= 1000:
        return "invalid", None
    elif field == "last_3f_time" and not 10 < number < 100:
        return "invalid", None
    elif field == "time_seconds" and not 0 < number < 3600:
        return "invalid", None
    return "valid", number


def corner_state(row, count):
    supplied = []
    for name in ("corner_positions_list", "corner_positions"):
        raw = row.get(name)
        if raw in (None, "", []):
            continue
        if isinstance(raw, str) and ("*" in raw or "＊" in raw):
            supplied.append((name, "masked", None))
            continue
        values = raw if isinstance(raw, list) else (
            [int(value) for value in raw.split("-")]
            if isinstance(raw, str) and re.fullmatch(r"\d+(?:-\d+)*", raw.strip()) else None)
        ok = bool(values) and all(isinstance(value, int) and not isinstance(value, bool)
                                  and 1 <= value <= (count or 100) for value in values)
        supplied.append((name, "valid" if ok else "invalid", tuple(values) if ok else None))
    valid = [value for _, status, value in supplied if status == "valid"]
    state = "valid" if valid else ("masked" if any(s == "masked" for _, s, _ in supplied)
                                    else "invalid" if supplied else "missing")
    return state, bool(len(set(valid)) > 1), [name for name, status, _ in supplied if status == "valid"]


def fresh_bucket():
    return {"source_rows": 0, "json_object_rows": 0, "races": set(),
            "fields": defaultdict(Counter), "issues": Counter(), "alias_conflicts": Counter(),
            "outcome_context": Counter(), "corner_sources": Counter()}


def finish_bucket(value):
    result = dict(value)
    result["races"] = len(result["races"])
    denominator = result["json_object_rows"]
    result["fields"] = {field: {**{state: counts[state] for state in STATES},
        "valid_pct_of_json_objects": round(100 * counts["valid"] / denominator, 5) if denominator else None}
        for field, counts in sorted(value["fields"].items())}
    for key in ("issues", "alias_conflicts", "outcome_context", "corner_sources"):
        result[key] = dict(result[key])
    return result


def self_test():
    assert normalize("horse_weight_change", 0) == ("valid", 0.0)
    assert normalize("last_3f_time", "**")[0] == "masked"
    assert normalize("finish", "取消")[0] == "legitimate_nonfinish"
    assert normalize("odds", 0)[0] == "unavailable_placeholder"
    assert normalize("time_seconds", "1:34.9")[1] == 94.9
    assert normalize("race_date", "2026/10/17")[1] == "20261017"
    assert normalize("race_date", "20260230")[0] == "invalid"
    assert normalize("horse_number", True)[0] == "missing"
    assert corner_state({"corner_positions": "4-2"}, 10)[0] == "valid"
    assert corner_state({"corner_positions": "4-20"}, 10)[0] == "invalid"
    assert corner_state({"corner_positions": "4-2", "corner_positions_list": [3, 2]}, 10)[1]
    return {"assertions": 11, "passed": True}


def audit(snapshot, receipt):
    started = time.perf_counter()
    db = sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA query_only=ON")
    metadata, metadata_seen, raw_metadata_issues = {}, Counter(), Counter()
    for race_id, raw in db.execute("SELECT race_id,data FROM races_ultimate"):
        race_id = str(race_id)
        metadata_seen[race_id] += 1
        try:
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError("not-object")
        except (ValueError, TypeError):
            raw_metadata_issues["invalid_json_or_not_object"] += 1
            metadata[race_id] = {"date": None, "year": race_id[:4], "canonical": None,
                                 "count": None, "going": None, "metadata_error": True}
            continue
        compact, reason = canonical._race_metadata(race_id, row)
        day = canonical._date(selected(row, ("date", "race_date")))
        n = canonical._number(row.get("num_horses"))
        n = int(n) if math.isfinite(n) and n > 0 and n == int(n) else None
        if reason:
            raw_metadata_issues["canonical_scope_reason:" + reason] += 1
        if len({canonical._date(row[k]) for k in ("date", "race_date") if present(row.get(k))}) > 1:
            raw_metadata_issues["date_alias_conflict"] += 1
        metadata[race_id] = {"date": day, "year": day[:4] if day else race_id[:4],
                             "canonical": compact, "count": n, "going": row.get("field_condition"),
                             "metadata_error": False}
    duplicate_metadata = {key for key, count in metadata_seen.items() if count > 1}
    totals = {name: fresh_bucket() for name in ("all_domains", "metadata_valid_jra_flat", "jra_flat_201601_202609")}
    yearly = {name: defaultdict(fresh_bucket) for name in totals}
    orphan_races, json_errors = set(), Counter()
    examples = defaultdict(list)
    pair_numbers, pair_horses, current_race = Counter(), Counter(), None
    duplicate_summary = {"race_horse_number": Counter(), "race_horse_id": Counter()}

    def finish_duplicate_group(rid):
        if rid is None:
            return
        for name, counts in (("race_horse_number", pair_numbers), ("race_horse_id", pair_horses)):
            duplicated = [(key, count) for key, count in counts.items() if count > 1]
            if duplicated:
                duplicate_summary[name]["races"] += 1
                duplicate_summary[name]["duplicate_keys"] += len(duplicated)
                duplicate_summary[name]["extra_rows"] += sum(count - 1 for _, count in duplicated)
                if len(examples[name]) < 15:
                    examples[name].append({"race_id": rid, "keys": duplicated[:5]})
        pair_numbers.clear()
        pair_horses.clear()

    for identifier, race_id, raw in db.execute("SELECT id,race_id,data FROM race_results_ultimate ORDER BY race_id,id"):
        race_id = str(race_id)
        if current_race != race_id:
            finish_duplicate_group(current_race)
            current_race = race_id
        meta = metadata.get(race_id)
        year = meta["year"] if meta else race_id[:4]
        year = year if re.fullmatch(r"\d{4}", year) else "UNKNOWN"
        scopes = ["all_domains"]
        if meta and meta["canonical"] and race_id not in duplicate_metadata:
            scopes.append("metadata_valid_jra_flat")
            if "20160101" <= meta["date"] <= "20260930":
                scopes.append("jra_flat_201601_202609")
        buckets = [bucket for scope in scopes for bucket in (totals[scope], yearly[scope][year])]
        for bucket in buckets:
            bucket["source_rows"] += 1
            bucket["races"].add(race_id)
        try:
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError("not-object")
        except (ValueError, TypeError):
            json_errors["runner_json_invalid_or_nonobject"] += 1
            for bucket in buckets:
                bucket["issues"]["json_invalid_or_nonobject"] += 1
            continue
        n = meta["count"] if meta else None
        outcomes, conflicts, issues = {}, [], []
        for field, aliases in ALIASES.items():
            supplied = [(name, normalize(field, row[name], n)) for name in aliases if present(row.get(name))]
            outcomes[field] = normalize(field, selected(row, aliases), n)
            comparable = {value for _, (status, value) in supplied if status == "valid"}
            if len(comparable) > 1:
                conflicts.append(field)
        sex_age = re.fullmatch(r"(牡|牝|セ|セン)(\d+)", str(row.get("sex_age") or ""))
        if sex_age:
            for field, value in (("sex", sex_age[1]), ("age", sex_age[2])):
                inferred = normalize(field, value, n)
                if outcomes[field][0] == "missing":
                    outcomes[field] = inferred
                elif outcomes[field][0] == "valid" and inferred[0] == "valid" and outcomes[field][1] != inferred[1]:
                    conflicts.append(field + "_vs_sex_age")
        # Model metadata is authoritative; absent runner dates are not missing if header is valid.
        runner_day = outcomes["race_date"]
        if runner_day[0] in {"invalid", "masked"}:
            issues.append("runner_date_field_invalid")
        if meta and meta["date"]:
            if runner_day[0] == "valid" and runner_day[1] != meta["date"]:
                issues.append("runner_date_vs_metadata_conflict")
            outcomes["race_date"] = ("valid", meta["date"])
        going = canonical._text(meta["going"]) if meta else None
        outcomes["field_condition"] = ("missing", None) if not going else (
            ("valid", going) if going in {"良", "稍重", "重", "不良"} else ("invalid", going))
        if meta and present(row.get("field_condition")) and canonical._text(row["field_condition"]) != going:
            issues.append("runner_going_vs_metadata_conflict")
        corner, corner_conflict, corner_sources = corner_state(row, n)
        outcomes["corner_positions"] = (corner, None)
        if corner_conflict:
            conflicts.append("corner_text_vs_list")
        if present(row.get("race_id")) and str(row["race_id"]).strip() != race_id:
            issues.append("embedded_race_id_conflict")
        if not meta:
            orphan_races.add(race_id)
            issues.append("orphan_runner_no_race_header")
        if race_id in duplicate_metadata:
            issues.append("duplicate_race_metadata_key")
        if outcomes["horse_number"][0] == "valid":
            pair_numbers[int(outcomes["horse_number"][1])] += 1
        if outcomes["horse_id"][0] == "valid":
            pair_horses[outcomes["horse_id"][1]] += 1
        outcome_context = "explicit_nonfinish" if outcomes["finish"][0] == "legitimate_nonfinish" else (
            "numeric_finish" if outcomes["finish"][0] == "valid" else "missing_or_unsettled_or_invalid_finish")
        if row.get("_shutuba"):
            outcome_context += ":card_flag"
        for bucket in buckets:
            bucket["json_object_rows"] += 1
            for field, (status, _) in outcomes.items():
                bucket["fields"][field][status] += 1
            bucket["issues"].update(issues)
            bucket["alias_conflicts"].update(conflicts)
            bucket["outcome_context"][outcome_context] += 1
            bucket["corner_sources"].update(corner_sources)
        for field in conflicts:
            if len(examples["alias_conflict:" + field]) < 5:
                examples["alias_conflict:" + field].append({"row_id": identifier, "race_id": race_id})
    finish_duplicate_group(current_race)
    counts = {table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
              for table in ("races_ultimate", "race_results_ultimate", "training_data", "speed_figures")}
    db.close()
    return {"schema": SCHEMA, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_path": str(snapshot), "snapshot_sha256": receipt["snapshot_sha256"],
        "script_sha256": digest(__file__), "counts": counts,
        "cohort_definitions": {"all_domains": "Every stored runner row, including local/ban-ei/jump and unsettled cards.",
            "metadata_valid_jra_flat": "Canonical metadata-valid JRA flat turf/dirt, before complete-race/outcome training eligibility.",
            "jra_flat_201601_202609": "Same metadata-valid JRA flat cohort with validated header date 20160101..20260930."},
        "year_basis": "Validated header race date; otherwise race-ID prefix as explicitly fallback UNKNOWN/date-unverified.",
        "denominator": "Field-state counts use valid JSON objects only; source rows include JSON failures. No official race completeness denominator inferred.",
        "aliases": ALIASES, "states": list(STATES),
        "validity_notes": ["** is masked, not zero and not a numeric value.",
            "Weight change zero is valid; zero odds/time/finish/popularity/body weight is unavailable placeholder.",
            "Nonfinish statuses are legitimate race events; missing finish/time is not automatically scrape failure.",
            "All-domain body-weight/burden limits include ban-ei horses; JRA-specific fitness claims are not inferred.",
            "Parent-name availability is not canonical identity or historical capture proof.",
            "Corner sequence is first/final REPORTED position, not certified numbered corner or pace.",
            "Source field availability is neither pre-race timing proof nor complete-career proof."],
        "totals": {name: finish_bucket(value) for name, value in totals.items()},
        "by_year": {name: {year: finish_bucket(value) for year, value in sorted(groups.items())} for name, groups in yearly.items()},
        "metadata": {"unique_outer_keys": len(metadata_seen), "duplicate_outer_key_count": len(duplicate_metadata),
            "duplicate_extra_rows": sum(value - 1 for value in metadata_seen.values() if value > 1),
            "issues": dict(raw_metadata_issues)},
        "runner_json_errors": dict(json_errors), "orphan_distinct_race_ids": len(orphan_races),
        "duplicate_summary": {name: dict(value) for name, value in duplicate_summary.items()},
        "bounded_identifier_examples": dict(examples),
        "source_code_sha256": {path: digest(ROOT / path) for path in (
            "python-api/experiments/dataset.py", "python-api/training/db_shared_features.py", "python-api/training/eligibility.py")},
        "duration_seconds": time.perf_counter() - started,
        "actions": {"network_requests": 0, "source_db_writes": 0, "training": False,
                    "model_change": False, "forward_change": False, "repairs": False}}


def clarify_existing(snapshot, output):
    """Preserve frozen v1, then distinguish source notation from bad values.

    No normalization is written to the DB or supplied to any model. The raw v1
    receipt remains unchanged. This second pass explains unsupported tokens,
    rather than counting every parser rejection as a source acquisition error.
    """
    raw_path = output.with_name("runner_quality_raw_v1.json")
    value = json.loads(raw_path.read_text(encoding="utf-8"))
    if value["snapshot_sha256"] != digest(snapshot):
        raise ValueError("clarification snapshot mismatch")
    db = sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA query_only=ON")
    metadata = {}
    for rid, payload in db.execute("SELECT race_id,data FROM races_ultimate"):
        row = json.loads(payload)
        compact, _ = canonical._race_metadata(str(rid), row)
        day = canonical._date(selected(row, ("date", "race_date")))
        metadata[str(rid)] = (compact, day, canonical._text(row.get("field_condition")))
    extra = {scope: {"going_abbreviations": Counter(), "finish_suffix": Counter(),
        "time_missing_by_finish": Counter(), "race_context": Counter()} for scope in value["totals"]}
    yearly_extra = {scope: defaultdict(lambda: {"going_abbreviations": Counter(), "finish_suffix": Counter(),
        "time_missing_by_finish": Counter(), "race_context": Counter()}) for scope in extra}
    race_states = defaultdict(Counter)
    examples = {"last3f_out_of_definition": [], "corner_out_of_definition": []}
    for rid, payload in db.execute("SELECT race_id,data FROM race_results_ultimate"):
        rid = str(rid)
        row = json.loads(payload)
        compact, day, going = metadata.get(rid, (None, None, None))
        year = day[:4] if day else rid[:4]
        scopes = ["all_domains"]
        if compact:
            scopes.append("metadata_valid_jra_flat")
            if "20160101" <= day <= "20260930":
                scopes.append("jra_flat_201601_202609")
        finish = selected(row, ALIASES["finish"])
        suffix = bool(re.fullmatch(r"\d+\(降\)", str(finish or "")))
        finish_status = normalize("finish", finish)[0]
        context = "adjudication_suffix" if suffix else (
            "explicit_nonfinish" if finish_status == "legitimate_nonfinish" else
            "positive_numeric_finish" if finish_status == "valid" else "missing_or_unsettled_finish")
        race_states[rid][context] += 1
        clock_status = normalize("time_seconds", selected(row, ALIASES["time_seconds"]))[0]
        for scope in scopes:
            for bucket in (extra[scope], yearly_extra[scope][year]):
                if going in {"稍", "不"}:
                    bucket["going_abbreviations"][going] += 1
                if suffix:
                    bucket["finish_suffix"]["demotion_annotation"] += 1
                if clock_status != "valid":
                    bucket["time_missing_by_finish"][context + ":" + clock_status] += 1
        if normalize("last_3f_time", selected(row, ALIASES["last_3f_time"]))[0] == "invalid" and len(examples["last3f_out_of_definition"]) < 10:
            examples["last3f_out_of_definition"].append({"race_id": rid, "raw_value": selected(row, ALIASES["last_3f_time"])})
    for rid, states in race_states.items():
        compact, day, _ = metadata.get(rid, (None, None, None))
        year = day[:4] if day else rid[:4]
        scopes = ["all_domains"]
        if compact:
            scopes.append("metadata_valid_jra_flat")
            if "20160101" <= day <= "20260930":
                scopes.append("jra_flat_201601_202609")
        if states["explicit_nonfinish"]:
            for scope in scopes:
                for bucket in (extra[scope], yearly_extra[scope][year]):
                    bucket["race_context"]["races_with_explicit_nonfinish"] += 1
                    if states["positive_numeric_finish"]:
                        bucket["race_context"]["races_with_both_numeric_finish_and_explicit_nonfinish"] += 1
    db.close()
    new_states = ("recognized_abbreviation", "adjudication_suffix_unparsed")
    for scope, total in value["totals"].items():
        groups = [(total, extra[scope])] + [(bucket, yearly_extra[scope][year])
            for year, bucket in value["by_year"][scope].items()]
        for bucket, observed in groups:
            for field_counts in bucket["fields"].values():
                for name in new_states:
                    field_counts[name] = 0
            abbreviations = sum(observed["going_abbreviations"].values())
            demotions = sum(observed["finish_suffix"].values())
            assert bucket["fields"]["field_condition"]["invalid"] >= abbreviations
            assert bucket["fields"]["finish"]["invalid"] >= demotions
            bucket["fields"]["field_condition"]["invalid"] -= abbreviations
            bucket["fields"]["field_condition"]["recognized_abbreviation"] = abbreviations
            bucket["fields"]["finish"]["invalid"] -= demotions
            bucket["fields"]["finish"]["adjudication_suffix_unparsed"] = demotions
            bucket["semantic_context"] = {key: dict(counts) for key, counts in observed.items()}
    value["states"] += list(new_states)
    value["semantic_clarification"] = {"schema": "raw-parser-rejection-clarification-v1",
        "frozen_raw_report": raw_path.name, "frozen_raw_report_sha256": digest(raw_path),
        "frozen_raw_auditor_sha256": value["script_sha256"], "clarification_script_sha256": digest(__file__),
        "going": "稍 and 不 are stored shorthand tokens. They are NOT scrape corruption; the current four-category helper does not accept them. No model or DB normalization was performed.",
        "finish": "n(降) is a demotion annotation, not a malformed source result or a finish exceeding field size. The canonical numeric parser does not currently parse it. No finish order was silently substituted.",
        "percentages": "valid_pct_of_json_objects remains strict frozen-parser valid coverage. Recognized notation is shown separately, not silently admitted to training.",
        "other_invalid": "Out-of-definition values need source/context checks, not automatic labeling as acquisition errors.",
        "examples": examples}
    with output.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"output": str(output), "sha256": digest(output), "semantic_counts": extra}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--clarify-existing", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test()))
        return
    if args.snapshot is None or args.output is None:
        parser.error("--snapshot and --output required")
    snapshot = args.snapshot.resolve(strict=True)
    root = (ROOT / "reports/generated/data-quality/current-audit").resolve(strict=True)
    if not snapshot.is_relative_to(root) or snapshot.name != "snapshot.sqlite3":
        raise ValueError("only separately receipted current-audit snapshots may be read")
    output = args.output.resolve()
    if output.parent != snapshot.parent or output.name != "runner_quality.json" or (output.exists() and not args.clarify_existing):
        raise ValueError("new sibling runner_quality.json required; overwrite forbidden")
    receipt_path = snapshot.parent / "snapshot_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    if Path(receipt["snapshot_path"]).resolve() != snapshot or receipt.get("quick_check") != "ok":
        raise ValueError("snapshot receipt invalid")
    if digest(snapshot) != receipt["snapshot_sha256"]:
        raise ValueError("snapshot changed after receipt")
    if args.clarify_existing:
        print(json.dumps(clarify_existing(snapshot, output), ensure_ascii=False))
        return
    result = audit(snapshot, receipt)
    result["self_test"] = self_test()
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(output), "sha256": digest(output), "counts": result["counts"],
                      "duration_seconds": result["duration_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

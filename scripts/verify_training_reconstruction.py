"""Independent label-free verification of the fixed reconstruction inventory.

Does not import the normalizer, exception auditor, or probe parser. No HTTP,
training, repairs, outcome metrics, or writes outside the exclusive receipt.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata

for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "python-api"), str(ROOT / "keiba")]
from training_reconstruction_common import protection, invariant_projection

SLOTS = ("time_6f", "time_5f", "time_4f", "time_3f", "time_1f", "lap_6f_5f", "lap_5f_4f", "lap_4f_3f", "lap_3f_1f", "lap_1f_g")
CANDIDATES = {"training_time_5f", "training_time_4f", "training_time_3f", "training_time_1f", "final_lap", "pace", "grade", "is_last_training",
    "last_1f_zscore", "4f_zscore", "5f_zscore", "acceleration_score", "final_lap_rank", "course_relative_time", "condition_relative_time",
    "training_days_before_race", "current_vs_previous_training", "current_vs_horse_average", "best_recent_training", "training_trend"}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def canonical(value):
    return unicodedata.normalize("NFKC", str(value)).strip() or None if value is not None else None


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}|\d{4}/\d{2}/\d{2}|\d{4}-\d{2}-\d{2}", value.strip()):
        return None
    try:
        return datetime.strptime(value.strip().replace("/", "").replace("-", ""), "%Y%m%d").date()
    except ValueError:
        return None


def number(value):
    if isinstance(value, bool):
        return None
    try:
        x = float(value)
        return int(x) if math.isfinite(x) and x.is_integer() and 1 <= x <= 99 else None
    except (ValueError, TypeError, OverflowError):
        return None


def file_map(base, files):
    for name, item in files.items():
        path = (base / name).resolve()
        require(path.is_relative_to(base.resolve()), "manifest traversal: " + name)
        require(sha(path) == (item["sha256"] if isinstance(item, dict) else item), "artifact hash: " + name)
        if isinstance(item, dict) and "bytes" in item:
            require(path.stat().st_size == item["bytes"], "artifact size: " + name)
    return len(files)


def parquet_rows(path):
    import pyarrow.parquet as pq
    for batch in pq.ParquetFile(path).iter_batches(batch_size=1024, use_threads=False):
        yield from batch.to_pylist()


def compare_record(raw, event, clocks, race_date, verified_horse_id, snapshot_sha):
    """Pure independent row/cell contract, also used by corruption fixtures."""
    eid = "legacy-training-" + str(raw["id"])
    training_day, target = day(raw["training_date"]), day(race_date)
    relation = "invalid" if training_day is None or target is None else "training_date_before_race" if training_day < target else "same_day_unknown" if training_day == target else "after_race"
    identity = verified_horse_id is not None
    reasons = ([] if identity else ["horse_identity_unverified"]) + ([] if relation == "training_date_before_race" else [relation])
    expected = {"event_id": eid, "source_training_id": raw["id"], "race_id": raw["race_id"], "horse_number": raw["horse_number"],
        "race_date": target.strftime("%Y%m%d") if target else None,
        "training_date": training_day.strftime("%Y%m%d") if training_day else None,
        "identity_verified": identity, "horse_id": verified_horse_id,
        "event_validity": relation, "quarantined": bool(reasons), "source_db_sha256": snapshot_sha,
        "source_record_sha256": hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
        "legacy_pit_class": "D_NOT_AVAILABLE" if not identity else "B_SOURCE_DATE_VALID_BUT_NOT_CAPTURE_TIME_VERIFIED" if relation == "training_date_before_race" else "C_JOINABLE_BUT_TIME_UNVERIFIED"}
    for source, dest in {"horse_name": "horse_name", "training_date": "training_date_raw", "course": "course_raw", "track_condition": "track_condition_raw",
        "rider": "rider", "position": "position", "pace": "pace_raw", "grade": "grade_raw", "comment": "comment_raw", "created_at": "source_db_created_at",
        "is_last_training": "legacy_is_last_training"}.items():
        expected[dest] = raw[source]
    for source, dest in {"course": "course_normalized", "track_condition": "track_condition", "pace": "pace_category", "grade": "grade_category"}.items():
        expected[dest] = canonical(raw[source])
    for key, value in expected.items():
        require(event[key] == value, f"event {raw['id']} {key}")
    require(json.loads(event["quarantine_reasons"]) == reasons, "quarantine reasons")
    require(event["pit_class"] == "C_INVALID_OR_UNKNOWN" and event["capture_provenance"] == "capture_time_unknown", "unproven PIT upgrade")
    for key in ("source_url", "source_hash", "retrieved_at", "historical_backfill"):
        require(event[key] is None, "invented provenance: " + key)
    for key in ("raw_original_available", "distance_mapping_verified", "model_input_allowed", "is_last_training_verified"):
        require(event[key] is False, "unverified admission: " + key)
    require(len(clocks) == 10, "clock slot count")
    missing = []
    for index, (slot, clock) in enumerate(zip(SLOTS, clocks)):
        original = raw[slot]
        require(clock["source_training_id"] == raw["id"] and clock["event_id"] == eid and clock["original_slot"] == slot, "clock identity/order")
        require(clock["measurement_kind"] == ("cumulative" if index < 5 else "lap") and clock["original_slot_index"] == index % 5, "shifted original slot")
        require(clock["original_value"] == json.dumps(original, ensure_ascii=False, separators=(",", ":"), allow_nan=False), "changed original clock")
        kind = "null" if original is None else "boolean" if isinstance(original, bool) else "integer" if isinstance(original, int) else "real" if isinstance(original, float) else "text"
        require(clock["original_value_type"] == kind, "changed clock SQL type")
        absent = original is None or isinstance(original, str) and original.strip().lower() in {"", "-", "--", "---", "nan", "none", "null", "n/a", "―", "—", "－"}
        numeric = not isinstance(original, bool) and isinstance(original, (int, float)) and math.isfinite(original) and original > 0
        status = "MISSING" if absent else "PRESENT_NUMERIC_DISTANCE_UNVERIFIED" if numeric else "INVALID_VALUE"
        require(clock["time_seconds"] == (float(original) if numeric else None) and clock["value_status"] == status, "clock value/imputation")
        require(clock["missing_reason"] == ("UNKNOWN" if absent else None if numeric else "INVALID_VALUE_UNRESOLVED"), "unsupported missing classification")
        for key in ("measurement_distance", "lap_from_distance", "lap_to_distance"):
            require(clock[key] is None, "guessed measurement distance")
        require(clock["distance_mapping_verified"] is False and clock["model_input_allowed"] is False, "clock admission")
        for key in ("race_id", "horse_id", "horse_number", "training_date", "course_normalized", "track_condition", "pit_class", "quarantined"):
            require(clock[key] == event[key], "clock/event binding: " + key)
        if absent:
            missing.append(clock)
    return relation, reasons, missing


def verify_probe(output, db, plan):
    from bs4 import BeautifulSoup
    result, review = read(output / "probe_result.json"), read(output / "probe_receipt_review.json")
    require(result["plan_sha256"] == sha(output / "experiment_plan.json"), "probe plan binding")
    require(result["local_search_sha256"] == sha(output / "training_source_inventory.json"), "local search binding")
    require(review["executed_source_sha256"] == sha(output / "provenance/executed_probe_training_reconstruction.py"), "executed source binding")
    existing = {r[0] for r in db.execute("SELECT DISTINCT race_id FROM training_data")}
    candidates = [dict(r) for r in db.execute("SELECT race_id,race_date,num_horses FROM races_ultimate ORDER BY race_date,race_id")
        if len(r["race_id"]) == 12 and "01" <= r["race_id"][4:6] <= "10" and len(r["race_date"]) == 8
        and r["race_id"][:4] == r["race_date"][:4] and r["race_id"] not in existing]
    expected, counts = [], {}
    for year in range(2019, 2025):
        subset = [r for r in candidates if r["race_date"].startswith(str(year))]
        require(len(subset) >= 3, "sample candidates")
        counts[str(year)] = len(subset)
        for q in (.2, .5, .8):
            row = subset[int((len(subset) - 1) * q)]
            expected.append({**row, "year": year, "selection_quantile": q,
                "url": "https://race.netkeiba.com/race/oikiri.html?race_id=" + row["race_id"]})
    require(expected == plan["probe_races"] and counts == plan["uncollected_JRA_races_by_year"], "fixed sample differs")
    with (output / "historical_recovery_probe.csv").open(encoding="utf-8-sig", newline="") as stream:
        probes = list(csv.DictReader(stream))
    require(len(probes) == len(expected) == len(review["responses"]) == 18, "probe count")
    require({p.name for p in (output / "probe_raw").iterdir()} == {r["race_id"] + ".html" for r in expected}, "raw file set")
    observed_total, public_grades, started_times = 0, 0, []
    for selected, row, evidence in zip(expected, probes, review["responses"]):
        rid = selected["race_id"]
        require(row["race_id"] == evidence["race_id"] == rid and row["url"] == evidence["source_url"] == selected["url"], "probe identity")
        path = output / "probe_raw" / (rid + ".html")
        require(sha(path) == row["body_sha256"] == evidence["source_hash"], "raw hash")
        raw = path.read_bytes()
        require(len(raw) == int(row["response_bytes"]) <= 2097152 and row["attempted"] == "True" and int(row["http_status"]) == evidence["http_status"] == 200, "receipt shape/status")
        encoding = re.search(rb'charset\s*=\s*["\']?([A-Za-z0-9_-]+)', raw[:5000], re.I)
        require(encoding is not None, "unknown actual raw charset")
        text = raw.decode(encoding.group(1).decode("ascii"), errors="strict")
        soup = BeautifulSoup(text, "lxml")
        table = soup.select_one("table.OikiriTable")
        require(table is not None and re.search(r"['\"]memberRank['\"]\s*:\s*['\"]NotLogin['\"]", text), "anonymous table expected")
        title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
        denied = any(s in title for s in ("access denied", "just a moment", "attention required", "アクセス制限")) or any(s in text.lower() for s in ("cf-chl-", "verify you are human", "アクセスが制限されています", "アクセス制限がかかっています", "ただいまアクセスが集中しております", "captcha"))
        require(not denied and evidence["page_access_restriction_detected"] is False, "access restriction in raw")
        for selector in (".Training_Day", "ul.TrainingTimeDataList", ".TrainingLoad", ".TrainingReview_Cell"):
            require(not table.select(selector), "unexpected actual workout values")
        actual = []
        for tr in table.select('tr[id^="tr_"]'):
            num, anchor = tr.select_one("td.Umaban"), tr.select_one(".Horse_Name a")
            require(num is not None and anchor is not None, "runner header missing identity")
            match = re.search(r"/horse/(\d+)(?:/|$)", anchor.get("href", ""))
            require(match is not None, "horse id link")
            actual.append((int(num.get_text(strip=True)), match.group(1), canonical(anchor.get_text(strip=True))))
        truth = [(int(r[0]), str(r[1]), canonical(r[2])) for r in db.execute("SELECT horse_number,horse_id,horse_name FROM race_results_ultimate WHERE race_id=?", (rid,))]
        require(len(actual) == len(set(actual)) and sorted(actual) == sorted(truth), "probe runner key/source mismatch")
        require(len(actual) == evidence["runner_headers"] == evidence["runner_headers_matching_snapshot"] == evidence["snapshot_runner_count"], "runner coverage claim")
        require(evidence["response_received_at"] is None and evidence["request_started_at"] == row["retrieved_at"], "invented response time")
        require(evidence["strict_pit_usable_records"] == evidence["event_valid_backfill_records"] == 0, "probe PIT upgrade")
        started_times.append(datetime.fromisoformat(evidence["request_started_at"]))
        observed_total += len(actual)
        public_grades += len(table.select('td[class^="Rank_"]'))
    require(datetime.fromisoformat(plan["frozen_at"]) < min(started_times), "plan not before HTTP")
    require(min((b-a).total_seconds() for a,b in zip(started_times,started_times[1:])) >= 2.9, "request pacing receipt violation")
    require(result["requests_attempted"] == result["raw_responses_saved"] == 18 and result["responses_with_workout_values"] == 0, "probe result totals")
    require(result["HISTORICAL_TRAINING_RECOVERABLE"] == "UNKNOWN" and result["strict_pit_rows_added"] == result["normalized_backfill_rows_added"] == 0, "recovery overclaim")
    require(not result["authentication_attempted"] and not result["bulk_acquisition_started"] and result["models_fitted"] == 0, "probe scope")
    return {"responses_checked": 18, "runner_headers_exact": observed_total, "public_rank_cells": public_grades,
        "workout_values": 0, "access_restrictions": 0, "received_at_unknown": 18, "historical_recoverability": "UNKNOWN"}


def verify_coverage(output, headers, runner_numbers, presence, valid, cohort):
    groups = defaultdict(list)
    for rid, rows in headers.items():
        if len(rows) != 1:
            continue
        r = rows[0]
        date = day(r["race_date"])
        if r["data_json_valid"] != 1 or r["inner_race_id"] != rid or date is None:
            continue
        nums = {n for n,c in runner_numbers.get(rid, {}).items() if n is not None and c == 1}
        declared = number(r["num_horses"])
        denominator = declared if declared is not None and declared >= len(nums) else None
        groups[("all_canonical",date.year)].append((len(nums), denominator,
            sum((rid,n) in presence for n in nums), sum((rid,n) in valid for n in nums)))
    for rid, items in cohort.items():
        nums = {number(r["horse_number"]) for r in items}
        require(None not in nums and len(nums) == len(items), "cohort duplicate/missing number")
        years = {day(r["race_date"]).year for r in items}
        require(len(years) == 1 and years <= {2021,2022}, "cohort dates")
        groups[("market_clean_2021_2022", years.pop())].append((len(nums),len(nums),sum((rid,n) in presence for n in nums),sum((rid,n) in valid for n in nums)))
    with (output / "yearly_coverage_after_reconstruction.csv").open(encoding="utf-8-sig",newline="") as stream:
        rows = list(csv.DictReader(stream))
    expected_keys = {(s,y) for s in ("all_canonical","market_clean_2021_2022") for y in range(2013,2027)}
    require(len(rows) == 28 and {(r["scope"],int(r["year"])) for r in rows} == expected_keys, "coverage key set")
    for row in rows:
        values = groups[(row["scope"],int(row["year"]))]
        known = [v for v in values if v[1] is not None]
        denominator = sum(v[1] for v in known)
        expected = {"total_races": len(values), "known_runner_key_rows": sum(v[0] for v in values), "denominator_runner_count": denominator,
            "denominator_unknown_races": len(values)-len(known), "complete_saved_runner_races": sum(v[0]==v[1] for v in values)}
        for prefix, col in (("stored_presence",2),("event_valid_identity",3)):
            expected.update({prefix+"_races":sum(v[col]>0 for v in values),prefix+"_runner_count":sum(v[col] for v in values),
                prefix+"_known_denominator_runner_count":sum(v[col] for v in known),prefix+"_ge50pct_races":sum(v[col]/v[1]>=.5 for v in known),
                prefix+"_ge80pct_races":sum(v[col]/v[1]>=.8 for v in known),prefix+"_100pct_races":sum(v[col]==v[1] for v in known)})
            rate = row[prefix+"_runner_coverage_percent"]
            require(math.isclose(float(rate),sum(v[col] for v in known)/denominator*100,abs_tol=1e-10) if denominator else rate == "", "coverage rate")
        for key,value in expected.items(): require(int(row[key]) == value,"coverage "+str((row["scope"],row["year"],key)))
        for key in ("strict_pit_runner_count","strict_pit_races","strict_pit_ge50pct_races","strict_pit_ge80pct_races","strict_pit_100pct_races"):
            require(int(row[key]) == 0,"strict coverage upgrade")
        require(row["model_input_gate"] == "NO" and row["data_reconstructed"] == "False", "reconstructed coverage overclaim")
    return {"year_scope_rows":28,"market_races":len(cohort),"market_runners":sum(map(len,cohort.values())),
        "market_event_valid_runners":sum((rid,number(r["horse_number"])) in valid for rid,items in cohort.items() for r in items),
        "market_event_valid_races":sum(any((rid,number(r["horse_number"])) in valid for r in items) for rid,items in cohort.items())}


def execute(output, snapshot, expected_plan_sha):
    output, snapshot = Path(output).resolve(strict=True), Path(snapshot).resolve(strict=True)
    require(not (output / "independent-verification.json").exists(), "exclusive verification receipt already exists")
    require(sha(output / "experiment_plan.json") == expected_plan_sha, "expected plan SHA mismatch")
    plan = read(output / "experiment_plan.json")
    require(sha(snapshot) == plan["input_snapshot_sha256"], "snapshot hash")
    wal = Path(str(snapshot)+"-wal")
    require(not wal.exists() or wal.stat().st_size == 0, "immutable snapshot WAL")
    before = protection()
    require(invariant_projection(before) == invariant_projection(plan["protected_before"]), "protected state differs from plan")
    normal, exceptions = read(output / "normalization-result.json"), read(output / "exceptions-result.json")
    require(normal["status"] == exceptions["status"] == "completed", "incomplete stage")
    file_map(output,normal["files"]); file_map(output,exceptions["files"])
    file_map(ROOT,normal["source_sha256"]); file_map(ROOT,exceptions["imported_frozen_code_sha256"])
    require(exceptions["code_sha256"] == sha(ROOT / "scripts/audit_training_reconstruction_exceptions.py"), "exception source")
    sources = read(output / "provenance/training-parser-source-manifest.json")
    file_map(output,sources["files"])
    require(sources["snapshot_sha256"] == normal["source_snapshot_sha256"] == exceptions["snapshot_sha256"] == sha(snapshot), "input chain")
    registry = read(output / "feature_candidate_registry.json")
    require(len(registry["candidates"]) == 20 and {x["feature"] for x in registry["candidates"]} == CANDIDATES,"candidate set")
    for item in registry["candidates"]:
        require(item["status"] == ("REJECT" if item["feature"] == "is_last_training" else "HOLD") and item["model_input_allowed"] is False,"candidate admitted")
    require(registry["comment_policy"]["status"] == "EXCLUDED" and registry["TRAINING_FEATURE_RESEARCH_READY"] == "NO","feature gate")
    inventory = read(output / "training_source_inventory.json")
    require(inventory["original_html_race_matches"] == inventory["strict_pit_records_proven"] == 0,"unexpected original evidence")
    for record in csv.DictReader((output / "training_distance_mapping.csv").open(encoding="utf-8-sig",newline="")):
        require(record["distance_mapping_verified"] == "False" and not record["verified_furlongs"],"distance mapping admitted")
    events, measures = iter(parquet_rows(output / "normalized_training_events.parquet")), iter(parquet_rows(output / "normalized_training_measurements.parquet"))
    counts, relations, missing_slots = Counter(),Counter(),Counter()
    exception_rows, presence, valid = {},set(),set()
    with ExitStack() as stack:
        db = stack.enter_context(sqlite3.connect(snapshot.as_uri()+"?mode=ro&immutable=1",uri=True))
        db.row_factory = sqlite3.Row; db.execute("PRAGMA query_only=ON")
        headers = defaultdict(list)
        for row in db.execute("SELECT race_id,inner_race_id,data_json_valid,race_date,num_horses FROM races_ultimate"):
            headers[str(row["race_id"])].append(dict(row))
        training_races = {r[0] for r in db.execute("SELECT DISTINCT race_id FROM training_data")}
        roster, runner_numbers = defaultdict(list),defaultdict(Counter)
        for row in db.execute("SELECT race_id,inner_race_id,data_json_valid,horse_number,horse_name,horse_id FROM race_results_ultimate"):
            rid, num = str(row["race_id"]),number(row["horse_number"])
            runner_numbers[rid][num] += 1
            if rid in training_races: roster[(rid,num)].append(dict(row))
        pit = iter(csv.DictReader(stack.enter_context((output / "training_pit_classification.csv").open(encoding="utf-8-sig",newline=""))))
        missing_csv = iter(csv.DictReader(stack.enter_context((output / "training_missingness_classification.csv").open(encoding="utf-8-sig",newline=""))))
        for source in db.execute("SELECT * FROM training_data ORDER BY id"):
            raw = dict(source); rid = raw["race_id"]; num = number(raw["horse_number"])
            h = headers.get(rid,[]); candidates = roster.get((rid,num),[])
            target = h[0]["race_date"] if len(h)==1 and h[0]["inner_race_id"]==rid and h[0]["data_json_valid"]==1 else None
            horse_id = str(candidates[0]["horse_id"]) if target and len(candidates)==1 and candidates[0]["horse_id"] and candidates[0]["inner_race_id"]==rid and candidates[0]["data_json_valid"]==1 and canonical(raw["horse_name"]) and canonical(raw["horse_name"])==canonical(candidates[0]["horse_name"]) else None
            event = next(events,None); clocks = [next(measures,None) for _ in range(10)]
            require(event is not None and all(c is not None for c in clocks),"normalized missing rows")
            relation,reasons,missing = compare_record(raw,event,clocks,target,horse_id,plan["input_snapshot_sha256"])
            p = next(pit,None); require(p is not None,"PIT CSV missing row")
            for key,value in p.items():
                require(value == ("" if event[key] is None else str(event[key])),"PIT CSV/event mismatch: "+key)
            for clock in missing:
                row = next(missing_csv,None); require(row is not None,"missingness CSV row missing")
                for key,value in row.items(): require(value == ("" if clock[key] is None else str(clock[key])),"missing CSV mismatch")
                missing_slots[clock["original_slot"]] += 1
            counts.update(events=1,measurements=10,quarantined=int(bool(reasons)),missing=len(missing))
            relations[relation] += 1; presence.add((rid,num))
            if not reasons: valid.add((rid,num))
            if reasons: exception_rows[raw["id"]] = (raw,relation,horse_id)
        require(next(events,None) is None and next(measures,None) is None and next(pit,None) is None and next(missing_csv,None) is None,"extra normalized rows")
        probes = verify_probe(output,db,plan)
        import pyarrow.parquet as pq
        cohort_path = ROOT / "reports/generated/data-quality/market-h04-clean-v2/clean_runners.parquet"
        require(sha(cohort_path) == exceptions["cohort_sha256"],"cohort hash")
        cohort = defaultdict(list)
        for row in pq.read_table(cohort_path,columns=["race_id","race_date","horse_number"],filters=[("race_date",">=","20210101"),("race_date","<=","20221231")],use_threads=False).to_pylist():
            cohort[row["race_id"]].append(row)
        coverage = verify_coverage(output,headers,runner_numbers,presence,valid,cohort)
    for name, reason in (("post_race_date_anomalies.csv","after_race"),("unmatched_runner_records.csv","identity")):
        rows=list(csv.DictReader((output/name).open(encoding="utf-8-sig",newline="")))
        truth={i:r for i,r in exception_rows.items() if r[1]==reason or reason=="identity" and r[2] is None}
        require(len(rows)==len(truth) and {int(r["id"]) for r in rows}==set(truth),"exception exact row set")
        for row in rows:
            raw = truth[int(row["id"])][0]
            for key,value in raw.items(): require(row[key] == ("" if value is None else str(value)),"exception original value changed")
            require(row["quarantine"]=="True" and row["correction_applied"]=="False" and row["cause"]=="UNKNOWN_WITHOUT_ORIGINAL_SOURCE","exception repair/overclaim")
    require(counts["events"]==85040 and counts["measurements"]==850400 and counts["quarantined"]==36,"whole normalization counts")
    require(relations=={"training_date_before_race":85023,"after_race":17} and len(valid)==85004,"event counts")
    require(normal["counts"]["events"]==counts["events"] and normal["counts"]["measurements"]==counts["measurements"] and normal["counts"]["quarantined_events"]==36,"normalization result counts")
    require(normal["missing_by_original_slot"]==dict(missing_slots) and normal["missingness_class_counts"]=={"STRUCTURAL_NOT_REPORTED":0,"SOURCE_MISSING":0,"PARSER_MISSING":0,"UNKNOWN":counts["missing"]},"missingness result")
    require(normal["strict_pit_events"]==normal["verified_historical_backfill_events"]==normal["distance_mapping_verified_measurements"]==normal["features_generated"]==normal["model_fits"]==0,"normalization overclaim")
    require(coverage["market_races"]==6109 and coverage["market_runners"]==84128 and coverage["market_event_valid_runners"]==201 and coverage["market_event_valid_races"]==13,"market cohort coverage")
    after=protection(); require(invariant_projection(before)==invariant_projection(after),"protected state changed")
    require(sha(snapshot)==plan["input_snapshot_sha256"],"snapshot changed")
    files={str(p.relative_to(output)):sha(p) for p in output.rglob("*") if p.is_file() and p.name not in {"artifact_manifest.json","independent-verification.json"}}
    result={"schema":"independent-training-reconstruction-verification-v1","verified":True,"status":"PASS","created_at":datetime.now(timezone.utc).isoformat(),
        "plan_sha256":expected_plan_sha,"snapshot_sha256":sha(snapshot),"verifier_sha256":sha(Path(__file__)),"files_verified_sha256":files,
        "normalization":dict(counts),"event_validity":dict(relations),"missing_by_slot":dict(missing_slots),"probe":probes,"coverage":coverage,
        "strict_pit_events":0,"verified_historical_backfill_events":0,"distance_verified_measurements":0,"features_generated":0,
        "feature_candidates":{"READY":0,"HOLD":19,"REJECT":1},"TRAINING_FEATURE_RESEARCH_READY":"NO",
        "protected_before":before,"protected_after":after,"new_network_requests":0,"model_fits":0,"issues":[]}
    with (output/"independent-verification.json").open("x",encoding="utf-8") as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({"verified":True,"normalization":dict(counts),"coverage":coverage,"probe":probes,"receipt_sha256":sha(output/"independent-verification.json")},ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--snapshot",type=Path,required=True)
    parser.add_argument("--expected-plan-sha256",required=True)
    args=parser.parse_args()
    if os.name=="nt":
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    execute(args.output,args.snapshot,args.expected_plan_sha256)

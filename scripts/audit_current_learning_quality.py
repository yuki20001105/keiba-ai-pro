"""Fresh-snapshot learning-cohort and all-row premium inventory, read-only.

Uses the frozen eligibility/freshness auditor unchanged. No acquisition, repair,
training, cached-matrix substitution, capture-authority upgrade or DB mutation.
Outputs JSON only to the supplied new audit directory; existing files fail.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time

for environment_name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[environment_name] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba"), str(ROOT / "scripts")]

import numpy as np
import pandas as pd
from audit_db_shared_history_freshness import audit_database, sha
from feature_provenance import training as training_audit


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def records(frame):
    return json.loads(frame.to_json(orient="records", force_ascii=False))


def frame_sha(frame):
    digest = hashlib.sha256()
    for row in frame.itertuples(index=False, name=None):
        values = [None if value is None or (isinstance(value, float) and math.isnan(value)) else value for value in row]
        digest.update(json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode() + b"\n")
    return digest.hexdigest()


def value_range(series, *, strict_calendar=False):
    converted = training_audit.dates(series) if strict_calendar else pd.to_datetime(series, format="mixed", errors="coerce")
    valid = converted.dropna()
    return {"valid": int(converted.notna().sum()), "missing_or_invalid": int(converted.isna().sum()),
            "from": valid.min().isoformat() if len(valid) else None,
            "to": valid.max().isoformat() if len(valid) else None}


def project_sources(connection, ids):
    races = pd.read_sql_query(
        "SELECT race_id,json_extract(data,'$.race_id') inner_race_id,json_valid(data) data_json_valid,"
        "coalesce(json_extract(data,'$.date'),json_extract(data,'$.race_date')) race_date,"
        "json_extract(data,'$.num_horses') num_horses,json_extract(data,'$.race_name') race_name,"
        "json_extract(data,'$.venue') venue,json_extract(data,'$.source_host') source_host,created_at "
        "FROM races_ultimate WHERE json_valid(data)", connection)
    pieces = []
    ordered = sorted(ids)
    for offset in range(0, len(ordered), 400):
        batch = ordered[offset:offset+400]
        marks = ",".join("?" for _ in batch)
        pieces.append(pd.read_sql_query(
            "SELECT id source_row_id,race_id,json_extract(data,'$.race_id') inner_race_id,"
            "json_valid(data) data_json_valid,json_extract(data,'$.horse_id') horse_id,"
            "json_extract(data,'$.horse_number') horse_number,json_extract(data,'$.horse_name') horse_name,created_at "
            f"FROM race_results_ultimate WHERE json_valid(data) AND race_id IN ({marks})", connection, params=batch))
    runners = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=[
        "source_row_id", "race_id", "inner_race_id", "data_json_valid", "horse_id", "horse_number", "horse_name", "created_at"])
    return races, runners


def yearly(frame, joined, *, with_training_date):
    parsed = training_audit.dates(joined.race_date)
    work = training_audit.dates(frame.training_date) if with_training_date else None
    created = pd.to_datetime(frame.created_at, format="mixed", errors="coerce")
    axes = {"race_id_prefix_not_calendar_proof": pd.to_numeric(frame.race_id.astype("string").str[:4], errors="coerce"),
            "canonical_race_date": parsed.dt.year,
            "created_at_registration": created.dt.year}
    if with_training_date:
        axes["training_event_date"] = work.dt.year
    output = []
    for name, years in axes.items():
        for year in range(2013, 2027):
            mask = years.eq(year).fillna(False)
            subset, links = frame.loc[mask], joined.loc[mask]
            identities = links.loc[links.identity_verified, "resolved_horse_id"].dropna()
            output.append({"year_basis": name, "year": year, "rows": len(subset),
                "distinct_race_ids": int(subset.race_id.nunique()),
                "distinct_verified_horses": int(identities.nunique()),
                "number_joinable_rows": int(links.number_joinable.sum()),
                "identity_verified_rows": int(links.identity_verified.sum())})
    return output


def join_summary(frame, joined, race_join, race_ids):
    ids = set(frame.race_id.astype(str))
    date = training_audit.dates(joined.race_date)
    historical = date.dt.year.between(2021, 2022).fillna(False)
    return {"rows": len(frame), "distinct_race_ids": len(ids),
        "outer_race_id_matched_distinct": len(ids & race_ids),
        "outer_race_id_unmatched_distinct": len(ids-race_ids),
        "outer_race_id_match_percent": len(ids & race_ids)/len(ids)*100 if ids else None,
        "canonical_race_join_status_rows": {str(key): int(value) for key,value in joined.race_join_status.value_counts().items()},
        "race_join_status_distinct_races": {str(key): int(value) for key,value in race_join.race_join_status.value_counts().items()},
        "number_joinable_rows": int(joined.number_joinable.sum()),
        "identity_verified_rows": int(joined.identity_verified.sum()),
        "identity_verified_distinct_horses": int(joined.loc[joined.identity_verified,"resolved_horse_id"].nunique()),
        "runner_join_status_rows": {str(key): int(value) for key,value in joined.runner_join_status.value_counts().items()},
        "canonical_2021_2022": {"rows": int(historical.sum()),
            "races": int(joined.loc[historical,"race_id"].nunique()),
            "identity_verified_rows": int(joined.loc[historical,"identity_verified"].sum()),
            "distinct_verified_horses": int(joined.loc[historical & joined.identity_verified,"resolved_horse_id"].nunique())},
        "created_at_is_prestart_availability_proof": False}


def premium_inventory(snapshot):
    started = time.perf_counter()
    connection = sqlite3.connect(snapshot.as_uri()+"?mode=ro&immutable=1",uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        schema = {name: [list(row) for row in connection.execute(f"PRAGMA table_info({name})")]
                  for name in ("training_data", "speed_figures")}
        training = pd.read_sql_query("SELECT * FROM training_data ORDER BY id",connection)
        speed = pd.read_sql_query("SELECT * FROM speed_figures ORDER BY id",connection)
        all_premium_ids = set(training.race_id.astype(str)) | set(speed.race_id.astype(str))
        races, runners = project_sources(connection, all_premium_ids)
        race_ids = set(races.race_id.astype(str))
        # Exact previous audit identity semantics, without its CSV publishing
        # or unsupported flattened-snapshot SQL. Legacy-only fallback is not
        # promoted to a canonical join in this new inventory.
        legacy = pd.DataFrame(columns=["race_id","kaisai_date"])
        training_join, training_races, _ = training_audit._join(training,races,runners,legacy)
        speed_join, speed_races, _ = training_audit._join(speed,races,runners,legacy)
        workout = training_audit.dates(training.training_date)
        race_day = training_audit.dates(training_join.race_date)
        both = workout.notna() & race_day.notna()
        event = pd.Series(np.select([both & workout.lt(race_day),both & workout.eq(race_day),both & workout.gt(race_day)],
            ["A_BEFORE_RACE","B_SAME_DAY_TIME_UNVERIFIED","C_AFTER_RACE"],default="D_DATE_OR_RACE_UNKNOWN"))
        missing = training_audit.missingness(training)
        courses = training.course.map(training_audit.course_group)
        course_missing = []
        for course in ("栗坂","美坂","美W","CW","OTHER","MISSING"):
            part = training.loc[courses.eq(course)]
            course_missing.extend(records(training_audit.missingness(part,course)))
        times = training[training_audit.TIMES].apply(pd.to_numeric,errors="coerce")
        laps = training[training_audit.LAPS].apply(pd.to_numeric,errors="coerce")
        valid_times = np.isfinite(times) & times.gt(0)
        valid_laps = np.isfinite(laps) & laps.gt(0)
        speed_fields = ["max_index","avg_5_index","dist_max_index","course_max_index","index_3ago","index_2ago","index_last"]
        speed_missing = []
        for field in speed_fields:
            values = speed[field]
            number = pd.to_numeric(values,errors="coerce")
            null, blank, sentinel = training_audit._missing_masks(values)
            absent = null | blank | sentinel
            valid = ~absent & np.isfinite(number) & number.mod(1).eq(0)
            speed_missing.append({"field":field,"rows":len(speed),"missing":int(absent.sum()),
                "invalid_nonmissing":int((~absent & ~valid).sum()),"valid_integer":int(valid.sum()),
                "negative_valid":int((valid & number.lt(0)).sum()),"minimum":float(number.loc[valid].min()) if valid.any() else None,
                "maximum":float(number.loc[valid].max()) if valid.any() else None,
                "storage_types":dict(Counter(connection.execute(f"SELECT typeof({field}) FROM speed_figures").fetchall()))})
            speed_missing[-1]["storage_types"] = {str(key[0]):int(value) for key,value in speed_missing[-1]["storage_types"].items()}
        aux_names = ["races","entries","results","horse_details","past_performances","race_lap_times",
                     "holding_times_cache","jockey_details","trainer_details"]
        auxiliary = {name: int(connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]) if name in tables else None for name in aux_names}
        training_report = {"rows":len(training),"columns":len(training.columns),"schema":schema["training_data"],
            "full_table_projection_sha256_order_by_id":frame_sha(training),
            "ranges":{"training_date":value_range(training.training_date,strict_calendar=True),
                "joined_race_date":value_range(training_join.race_date,strict_calendar=True),
                "created_at_registration":value_range(training.created_at)},
            "yearly":yearly(training,training_join,with_training_date=True),
            "join":join_summary(training,training_join,training_races,race_ids),
            "event_date_classes":dict(Counter(event)),
            "identity_verified_event_before_race_rows":int((training_join.identity_verified & event.eq("A_BEFORE_RACE")).sum()),
            "capture_time_verified_rows":0,
            "rows_with_any_valid_positive_clock":int(valid_times.any(axis=1).sum()),
            "rows_with_any_valid_positive_lap":int(valid_laps.any(axis=1).sum()),
            "missingness":records(missing),"course_missingness":course_missing,
            "course_group_rows":{str(key):int(value) for key,value in courses.value_counts().items()},
            "pace_categories":{str(key):int(value) for key,value in training.pace.value_counts(dropna=False).items()},
            "grade_categories":{str(key):int(value) for key,value in training.grade.value_counts(dropna=False).items()},
            "duplicate_race_number_rows_not_automatically_duplicate_workouts":int(training.duplicated(["race_id","horse_number"],keep=False).sum()),
            "duplicate_race_number_date_course_rows":int(training.duplicated(["race_id","horse_number","training_date","course"],keep=False).sum()),
            "structural_missingness":"A slot absent on a course can be structural. Stored slot mapping/source timing remain unverified; absence is not automatically acquisition failure, and no clock is filled with zero.",
            "source_date_is_capture_time":False,"comment_or_grade_approved_for_model":False}
        speed_report = {"rows":len(speed),"columns":len(speed.columns),"schema":schema["speed_figures"],
            "full_table_projection_sha256_order_by_id":frame_sha(speed),
            "ranges":{"joined_race_date":value_range(speed_join.race_date,strict_calendar=True),
                "created_at_registration":value_range(speed.created_at)},
            "yearly":yearly(speed,speed_join,with_training_date=False),
            "join":join_summary(speed,speed_join,speed_races,race_ids),"fields":speed_missing,
            "duplicate_race_number_rows":int(speed.duplicated(["race_id","horse_number"],keep=False).sum()),
            "point_in_time_reconstruction_validated":False,
            "reason":"Stored external aggregate index values do not prove their per-start source, formula or historical capture boundary; current rows are not joined back into past years."}
        return {"schema":"current-premium-inventory-v1","created_at_utc":datetime.now(timezone.utc).isoformat(),
            "snapshot":str(snapshot),"training_data":training_report,"speed_figures":speed_report,
            "auxiliary_table_counts":auxiliary,
            "model_input_scope":{"canonical_tables":["races_ultimate","race_results_ultimate"],
                "46_features":"canonical current card and strictly earlier eligible saved results",
                "80_features":"same canonical rows plus versioned PIT context; no direct training_data/speed_figures input",
                "auxiliary_profiles_or_premium_directly_joined":False,
                "premium_missingness_is_not_46_80_missingness":True},
            "network_requests":0,"database_writes":0,"training_started":False,
            "formula_or_gate_changed":False,"elapsed_seconds":time.perf_counter()-started}
    finally:
        connection.close()


def execute(directory):
    directory = Path(directory).resolve(strict=True)
    receipt_path = directory/"snapshot_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    snapshot = Path(receipt["snapshot_path"]).resolve(strict=True)
    if snapshot.parent != directory or receipt.get("quick_check") != "ok" or sha(snapshot) != receipt["snapshot_sha256"]:
        raise ValueError("fresh-audit-snapshot-receipt-not-verified")
    if any((directory/name).exists() for name in ("model_quality.json","premium_inventory.json","learning_quality_manifest.json")):
        raise FileExistsError("learning-quality-output-already-exists")
    print(json.dumps({"stage":"frozen_eligibility_audit_started","snapshot_sha256":receipt["snapshot_sha256"]}),flush=True)
    model = audit_database(snapshot,cutoff="20261017",focus_from="20260914",focus_to="20261016")
    details = model["recent_races"]
    explicit = [row for row in details if row["partial_complete_contract_exclusion_with_explicit_nonfinisher"]]
    model["current_interpretation"] = {"old_pinned_history_latest":"20260913",
        "current_eligible_latest":model["eligible_complete_history_date_range"]["to"],
        "saved_eligible_extends_beyond_old_pinned_history":bool(model["eligible_complete_history_date_range"]["to"] and model["eligible_complete_history_date_range"]["to"]>"20260913"),
        "2016_2026_eligible_races":sum(row["eligible_complete_history_races"] for row in model["yearly"] if row["year"]>=2016),
        "2016_2026_eligible_runner_rows":sum(row["eligible_complete_history_runner_rows"] for row in model["yearly"] if row["year"]>=2016),
        "recent_stage_counts":dict(Counter(row["stage"] for row in details)),
        "recent_complete_race_exclusions_with_explicit_nonfinisher":len(explicit),
        "recent_explicit_nonfinisher_labels":dict(sum((Counter(row["raw_runner_summary"]["explicit_nonfinisher_labels"]) for row in details),Counter())),
        "nonfinisher_exclusion_is_not_automatically_acquisition_error":True,
        "remaining_exclusions_need_reason_specific_investigation_not_gate_relaxation":True,
        "cached_feature_missingness_not_reused":"No historical cached matrix was claimed current without an exact current snapshot binding.",
        "premium_missingness_is_separate_from_model_input_missingness":True}
    write(directory/"model_quality.json",model)
    print(json.dumps({"stage":"model_quality_complete","latest_eligible":model["eligible_complete_history_date_range"]["to"],
          "eligible_races":model["eligible_complete_history_races"],"eligible_runners":model["eligible_complete_history_runner_rows"]}),flush=True)
    premium = premium_inventory(snapshot)
    write(directory/"premium_inventory.json",premium)
    if sha(snapshot) != receipt["snapshot_sha256"]:
        raise ValueError("fresh-quality-snapshot-mutated")
    write(directory/"learning_quality_manifest.json",{"schema":"current-learning-quality-manifest-v1",
        "snapshot_sha256":receipt["snapshot_sha256"],"auditor_sha256":sha(__file__),
        "frozen_auditor_sha256":sha(ROOT/"scripts/audit_db_shared_history_freshness.py"),
        "files":{name:sha(directory/name) for name in ("model_quality.json","premium_inventory.json")},
        "snapshot_unchanged":True,"network_requests":0,"database_writes":0,"training_started":False})
    print(json.dumps({"stage":"completed","directory":str(directory),
        "training_rows":premium["training_data"]["rows"],"valid_clock_rows":premium["training_data"]["rows_with_any_valid_positive_clock"],
        "speed_rows":premium["speed_figures"]["rows"]}),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-directory",type=Path,required=True)
    parser.add_argument("--run",action="store_true")
    args=parser.parse_args()
    if not args.run: parser.error("explicit --run required")
    execute(args.audit_directory)

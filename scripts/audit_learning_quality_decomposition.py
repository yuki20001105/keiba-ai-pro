"""Additional RO decomposition and independent SQLite workout-clock check.

The fixed canonical parser and eligibility decision are called unchanged.
Explicit non-finisher observations are separated from unexplained target
absence; neither type is silently repaired, imputed or admitted to training.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import sys

for variable in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS"):
    os.environ[variable]="2"
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"python-api"),str(ROOT/"keiba"),str(ROOT/"scripts")]
import pandas as pd
from audit_db_shared_history_freshness import sha
from experiments import dataset as canonical
from training import db_shared_features as shared
from training import eligibility


def save(path,value):
    with path.open("x",encoding="utf-8") as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.write("\n")


def explicit_nonfinisher(value):
    text=canonical._text(value)
    return bool(text and (text in {"取","除","中","失"} or any(
        marker in text for marker in ("取消","除外","中止","失格"))))


def quality_decomposition(snapshot,previous):
    audit=shared._audit()
    values,flags,labels=[],[],[]
    with shared._database(snapshot) as (connection,tables):
        metadata=shared._target_metadata(connection,"20130101","20261016",audit)
        counts=Counter()
        for batch in shared._batches(sorted(metadata)):
            marks=",".join("?" for _ in batch)
            for rid,payload in connection.execute(f"SELECT race_id,data FROM race_results_ultimate WHERE race_id IN ({marks})",batch):
                counts[rid]+=1
                try:
                    raw=shared._object(payload)
                    parsed,reason=canonical._runner(rid,shared._weight_alias(raw),metadata[rid])
                except (ValueError,TypeError):
                    raw,parsed,reason={},None,"runner_json_or_alias_invalid"
                if reason:
                    audit["quarantined_races"][rid]=reason
                else:
                    label=canonical._first(raw,"finish","finish_position")
                    values.append(parsed)
                    flags.append(explicit_nonfinisher(label))
                    labels.append(canonical._text(label))
        frame=pd.DataFrame(values,columns=canonical.SOURCE_COLUMNS)
        auxiliary=pd.DataFrame({"explicit_nonfinisher":flags,"raw_finish_label":labels},index=frame.index)
        keep=~frame.race_id.isin(audit["quarantined_races"])
        frame,auxiliary=frame.loc[keep].reset_index(drop=True),auxiliary.loc[keep].reset_index(drop=True)
        quality={str(rid):(str(lifecycle or ""),str(status or ""),bool(excluded))
            for rid,lifecycle,status,excluded in connection.execute(
                "SELECT race_id,lifecycle,quality_status,excluded_from_standard FROM scrape_race_acquisition_state")}
        selected=eligibility.select_training_eligible_rows(frame,target="speed_deviation",recorded_quality_states=quality)
    # This call exactly reproduces the frozen policy; the booleans below only
    # explain the stored observations. They never replace the selection mask.
    valid_target=eligibility._target_valid_mask(frame,"speed_deviation")
    raw=frame[["race_id","race_date"]].copy()
    raw["explicit_nonfinisher"]=auxiliary.explicit_nonfinisher
    raw["target_missing"]=~valid_target
    raw["target_missing_without_explicit_nonfinisher"]=(~valid_target)&~auxiliary.explicit_nonfinisher
    by_race=raw.groupby("race_id",sort=False).agg(race_date=("race_date","first"),source_rows=("race_date","size"),
        explicit_nonfinisher_rows=("explicit_nonfinisher","sum"),target_missing_rows=("target_missing","sum"),
        unexplained_target_missing_rows=("target_missing_without_explicit_nonfinisher","sum"))
    selected_counts=selected.frame.groupby("race_id").size()
    by_race["initial_selected_rows"]=selected_counts.reindex(by_race.index,fill_value=0)
    by_race["stage"]="no_initial_eligible_runner"
    partial=by_race.initial_selected_rows.gt(0)&by_race.initial_selected_rows.lt(by_race.source_rows)
    complete=by_race.initial_selected_rows.eq(by_race.source_rows)
    by_race.loc[complete,"stage"]="eligible_complete"
    by_race.loc[partial&by_race.unexplained_target_missing_rows.eq(0)&by_race.explicit_nonfinisher_rows.gt(0),"stage"]="complete_contract_exclusion_only_explicit_nonfinishers"
    by_race.loc[partial&~(by_race.unexplained_target_missing_rows.eq(0)&by_race.explicit_nonfinisher_rows.gt(0)),"stage"]="complete_contract_exclusion_with_unexplained_missing_target"
    periods=[]
    for name,lower,upper in (("all_history","20130101","20261016"),
        ("requested_2016_through_2026_09","20160101","20260930"),
        ("recent_focus","20260914","20261016")):
        scope=by_race.loc[by_race.race_date.between(lower,upper)]
        source=raw.loc[raw.race_date.between(lower,upper)]
        stage=[]
        for label,part in scope.groupby("stage",sort=True):
            stage.append({"stage":label,"races":len(part),"all_source_runner_rows":int(part.source_rows.sum()),
                "initial_selected_runner_rows":int(part.initial_selected_rows.sum()),
                "explicit_nonfinisher_rows":int(part.explicit_nonfinisher_rows.sum()),
                "target_missing_rows":int(part.target_missing_rows.sum()),
                "unexplained_target_missing_rows":int(part.unexplained_target_missing_rows.sum())})
        scope_auxiliary=auxiliary.loc[source.index]
        explicit_labels=scope_auxiliary.loc[scope_auxiliary.explicit_nonfinisher,"raw_finish_label"]
        periods.append({"scope":name,"from":lower,"to":upper,"canonical_flat_candidate_races":len(scope),
            "canonical_flat_candidate_runner_rows":len(source),"race_partition":stage,
            "exclusive_race_partition_counts_sum":sum(item["races"] for item in stage),
            "explicit_nonfinisher_labels":dict(Counter(explicit_labels))})
    full=periods[0]
    eligible_count=int(by_race.loc[by_race.stage.eq("eligible_complete"),"source_rows"].sum())
    if (int(by_race.stage.eq("eligible_complete").sum())!=previous["eligible_complete_history_races"]
        or eligible_count!=previous["eligible_complete_history_runner_rows"]
        or selected.manifest.as_dict()!=previous["eligibility_receipt"]["eligibility"]):
        raise ValueError("decomposition-not-exactly-reproducing-frozen-audit")
    return {"exact_frozen_selection_reproduction":True,"periods":periods,
        "parser_quarantine_reason_counts":dict(Counter(audit["quarantined_races"].values())),
        "initial_policy_nonexclusive_reasons":selected.manifest.as_dict()["exclusion_reason_counts"],
        "outside_model_domains_from_main_inventory":previous["metadata_scope_exclusions"],
        "scope_note":"The race partition is within canonical JRA flat candidates before the exclusive 20261017 cutoff. Domain exclusions are separate, not acquisition defects.",
        "legitimate_nonfinishers_still_excluded_by_unchanged_complete_race_policy":True,
        "unexplained_missing_target_is_not_automatic_proof_of_acquisition_failure":True}


def clock_sql(snapshot,prior):
    connection=sqlite3.connect(snapshot.as_uri()+"?mode=ro&immutable=1",uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        fields=["time_6f","time_5f","time_4f","time_3f","time_1f"]
        laps=["lap_6f_5f","lap_5f_4f","lap_4f_3f","lap_3f_1f","lap_1f_g"]
        predicate=lambda field:f"(typeof({field}) IN ('real','integer') AND {field}>0)"
        count=int(connection.execute("SELECT count(*) FROM training_data").fetchone()[0])
        valid=int(connection.execute("SELECT sum(CASE WHEN "+" OR ".join(map(predicate,fields))+" THEN 1 ELSE 0 END) FROM training_data").fetchone()[0])
        valid_laps=int(connection.execute("SELECT sum(CASE WHEN "+" OR ".join(map(predicate,laps))+" THEN 1 ELSE 0 END) FROM training_data").fetchone()[0])
        details=[]
        for field in fields+laps:
            types={str(kind):int(rows) for kind,rows in connection.execute(f"SELECT typeof({field}),count(*) FROM training_data GROUP BY typeof({field})")}
            minimum,maximum=connection.execute(f"SELECT min({field}),max({field}) FROM training_data WHERE {predicate(field)}").fetchone()
            positive,zero,negative=connection.execute(f"SELECT sum(CASE WHEN {predicate(field)} THEN 1 ELSE 0 END),"
                f"sum(CASE WHEN typeof({field}) IN ('real','integer') AND {field}=0 THEN 1 ELSE 0 END),"
                f"sum(CASE WHEN typeof({field}) IN ('real','integer') AND {field}<0 THEN 1 ELSE 0 END) FROM training_data").fetchone()
            details.append({"field":field,"sqlite_storage_types":types,"stored_positive_numeric":int(positive),
                "zero":int(zero),"negative":int(negative),"minimum_positive_stored":minimum,"maximum_positive_stored":maximum})
        if (count!=prior["rows"] or valid!=prior["rows_with_any_valid_positive_clock"]
            or valid_laps!=prior["rows_with_any_valid_positive_lap"]):
            raise ValueError("independent-workout-clock-SQL-check-disagrees")
        return {"all_rows":count,"any_stored_positive_numeric_clock":valid,"any_stored_positive_numeric_lap":valid_laps,
            "independent_sql_matches_pandas_all_row_audit":True,"fields":details,
            "meaning":"Positive SQLite numbers are present, not converted comments, blanks, zero-fill or synthetic reconstruction. This is numerical presence, NOT proof that legacy 6f/5f/4f/3f/1f slots correspond to the actual workout distance.",
            "slot_mapping_or_units_source_verified":False,"prestart_capture_verified":False,
            "course_structural_missingness_not_imputed":True}
    finally:
        connection.close()


def execute(directory):
    directory=directory.resolve(strict=True)
    receipt=json.loads((directory/"snapshot_receipt.json").read_text(encoding="utf-8"))
    model=json.loads((directory/"model_quality.json").read_text(encoding="utf-8"))
    premium=json.loads((directory/"premium_inventory.json").read_text(encoding="utf-8"))
    snapshot=Path(receipt["snapshot_path"]).resolve(strict=True)
    if snapshot.parent!=directory or sha(snapshot)!=receipt["snapshot_sha256"]:
        raise ValueError("decomposition-snapshot-not-verified")
    print(json.dumps({"stage":"independent_clock_SQL_started"}),flush=True)
    clocks=clock_sql(snapshot,premium["training_data"])
    print(json.dumps({"stage":"clock_SQL_verified","positive_clock_rows":clocks["any_stored_positive_numeric_clock"]}),flush=True)
    decomposition=quality_decomposition(snapshot,model)
    before,after=model["source_file_before"],model["source_file_after"]
    physical={"frozen_auditor_physical_change_flag":model["source_file_changed_during_read_transaction"],
        "database_body_sha256_unchanged":before["sha256"]==after["sha256"]==receipt["snapshot_sha256"],
        "database_body_bytes_and_mtime_unchanged":(before["bytes"],before["mtime_ns"])==(after["bytes"],after["mtime_ns"]),
        "sidecars_before":before["data_sidecars"],"sidecars_after":after["data_sidecars"],
        "explanation":"The unchanged frozen auditor includes sidecar presence. Opening a WAL-mode snapshot read-only created an empty (zero-byte) -wal and shared-memory coordination file. Snapshot body SHA/size/mtime are unchanged; no result rows, source data or quality gates were written."}
    if sha(snapshot)!=receipt["snapshot_sha256"] or shared.source_hashes()!=model["source_hashes"]:
        raise ValueError("decomposition-source-or-formula-changed")
    output={"schema":"current-learning-quality-decomposition-v1","created_at_utc":datetime.now(timezone.utc).isoformat(),
        "snapshot_sha256":receipt["snapshot_sha256"],"decomposition":decomposition,"independent_workout_clock_SQL":clocks,
        "physical_snapshot_interpretation":physical,"no_training_or_repair":True,"network_requests":0,"database_writes":0,
        "source_formula_hashes":shared.source_hashes(),"auditor_sha256":sha(__file__)}
    save(directory/"learning_quality_decomposition.json",output)
    save(directory/"learning_quality_decomposition_manifest.json",{"schema":"current-learning-quality-decomposition-manifest-v1",
        "files":{"learning_quality_decomposition.json":sha(directory/"learning_quality_decomposition.json")},
        "auditor_sha256":sha(__file__),"snapshot_sha256":receipt["snapshot_sha256"]})
    print(json.dumps({"stage":"completed","periods":decomposition["periods"]},ensure_ascii=False),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-directory",type=Path,required=True)
    parser.add_argument("--run",action="store_true")
    args=parser.parse_args()
    if not args.run:parser.error("explicit --run required")
    execute(args.audit_directory)

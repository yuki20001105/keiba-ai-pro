"""Explicit, bounded JRA-only quality repair; preserves normal saved rows.

No training, activation, H04 edits, missed-window replay, or UI purchases.
Run plan first, then explicitly authorize execute/network. Every changed data
row has an atomic before-image in data_quality_repair_journal_v1.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))
sys.path.insert(0, str(ROOT / "scripts"))

from scraping.acquisition_scope import (is_jra_race_id, normalized_date, validate_scoped_payload,
    record_scoped_expectation, run_scoped_date_repair_audit, invalidate_scoped_no_race_dates)
from scraping.quality import classify_race_quality, record_race_quality, invalidate_verified_no_race_dates
from scraping.safe_repair import normalize_saved_going, save_result_repair, load_saved, RepairConflict
from evaluate_db_shared_venues import preservation_receipt
from repair_source_verification import source_date, validate_result_source

DB_PATH = ROOT / "keiba/data/keiba_ultimate.db"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def ro(path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.execute("PRAGMA query_only=ON")
    return connection


def plan(audit_dir, run_dir):
    coverage = json.loads((audit_dir / "acquisition_coverage.json").read_text(encoding="utf-8"))
    clarification = json.loads((audit_dir / "acquisition_result_status_clarification.json").read_text(encoding="utf-8"))
    targets = {}
    with ro(DB_PATH) as db:
        date_by_id = {}
        for day, raw in db.execute("SELECT race_date,expected_race_ids_json FROM scrape_scoped_date_completeness WHERE race_scope='jra'"):
            for race_id in json.loads(raw):
                if race_id in date_by_id and date_by_id[race_id] != day:
                    raise ValueError("repair-expectation-date-conflict:" + race_id)
                date_by_id[race_id] = day

        def add(race_id, day, reason):
            if not is_jra_race_id(race_id, day) or not ("20160101" <= day <= "20261016"):
                return
            row = targets.setdefault(race_id, {"race_id":race_id,"race_date":day,"reasons":[]})
            if row["race_date"] != day:
                raise ValueError("repair-date-conflict")
            row["reasons"].append(reason)

        for race_id in coverage["jra_expected_id_audit_201601_202609"]["raw_absent_expected_race_ids"]:
            add(race_id,date_by_id[race_id],"expected_id_missing")
        for item in clarification["historical_23_race_details"]:
            reason = "finish_annotation_source_verification" if item["classification"].startswith("settled_result_annotation") else "results_not_saved"
            add(item["race_id"],item["race_date"],reason)
        for item in coverage["declared_runner_count_mismatches"]:
            if item["scope"] == "jra" and item.get("race_date"):
                add(item["race_id"],item["race_date"],"declared_runner_count_mismatch")
        for race_id, raw in db.execute("SELECT race_id,data FROM races_ultimate WHERE race_id BETWEEN '2026' AND '2027'"):
            info = json.loads(raw)
            day = normalized_date(info.get("date"))
            if day and "20261010" <= day <= "20261012" and is_jra_race_id(race_id):
                data = load_saved(db,race_id)
                if any(h.get("finish_position") is None for h in data["horses"]):
                    add(race_id,day,"recent_results_not_saved")
    result = {"schema":"jra-quality-repair-plan-v1", "created_at_utc":datetime.now(timezone.utc).isoformat(),
        "batch_id":run_dir.name, "database":str(DB_PATH), "source_audit":str(audit_dir),
        "audit_files":{name:sha(audit_dir/name) for name in ("acquisition_coverage.json","acquisition_result_status_clarification.json")},
        "normalization_period":{"from":"20160101","to":"20261016"},
        "targets":sorted(targets.values(),key=lambda item:(item["race_date"],item["race_id"])),
        "discovery_dates":["20261003","20261004"],
        "protected_before":preservation_receipt(), "normal_data_overwrite":False,
        "http_minimum_interval_seconds":1.0, "scrape_workers":2,
        "note":"Expected ID absence is a repair candidate, not source proof. No writes/network until explicit execute."}
    write(run_dir/"plan.json",result)
    write(run_dir/"plan_receipt.json",{"plan_sha256":sha(run_dir/"plan.json"),"batch_id":run_dir.name})
    return result


def validate_plan(data, run_dir):
    receipt=json.loads((run_dir/"plan_receipt.json").read_text(encoding="utf-8"))
    if (data.get("schema")!="jra-quality-repair-plan-v1" or Path(data.get("database","")).resolve()!=DB_PATH.resolve()
            or receipt.get("plan_sha256")!=sha(run_dir/"plan.json") or data.get("batch_id")!=run_dir.name):
        raise ValueError("repair-plan-binding-invalid")
    audit_dir=Path(data["source_audit"])
    if any(sha(audit_dir/name)!=expected for name,expected in data["audit_files"].items()):
        raise ValueError("repair-original-audit-changed")
    seen=set()
    for item in data["targets"]:
        rid,day=item["race_id"],item["race_date"]
        if rid in seen or not is_jra_race_id(rid,day) or normalized_date(day)!=day or not "20160101"<=day<="20261016":
            raise ValueError("repair-plan-target-invalid")
        seen.add(rid)
    if data.get("discovery_dates")!=["20261003","20261004"]:
        raise ValueError("repair-plan-discovery-scope-invalid")
    return receipt["plan_sha256"]


async def execute(plan_data, run_dir, *, limit=None, reuse_fetched_sources=False):
    import aiohttp
    from scraping import race, fetch_pipeline
    from scraping.jra_listing import fetch_jra_race_ids
    from scraping.constants import get_random_headers

    plan_sha=validate_plan(plan_data,run_dir)
    before = preservation_receipt()
    for key,value in plan_data["protected_before"].items():
        if key != "forward_processes" and before[key] != value:
            raise ValueError("repair-protected-source-or-model-changed")
    statuses_path = run_dir/"events.jsonl"
    def emit(event):
        event["plan_sha256"]=plan_sha
        event["recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
        with statuses_path.open("a",encoding="utf-8") as stream:
            stream.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+"\n")
        print(json.dumps(event,ensure_ascii=False),flush=True)
    completed = set()
    prior_pending = set()
    if statuses_path.exists():
        for line in statuses_path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item.get("plan_sha256")!=plan_sha:
                raise ValueError("repair-resume-event-plan-mismatch")
            if item.get("status") == "saved": completed.add(item["race_id"])
            if item.get("status") == "pending": prior_pending.add(item["race_id"])

    # Tiny transactions; no full DB backup/long read-lock during live collection.
    normalization_file = run_dir/"going_normalization.json"
    if not normalization_file.exists():
        changed = await asyncio.to_thread(normalize_saved_going,DB_PATH,batch_id=plan_data["batch_id"])
        write(normalization_file,{"changed_races":len(changed),"race_ids":changed,"raw_preserved":True,"journal_in_same_transaction":True})
        emit({"stage":"going_normalization","changed_races":len(changed)})

    targets = {item["race_id"]:item for item in plan_data["targets"]}
    discovery_file = run_dir/"discovery.json"
    previous=json.loads(discovery_file.read_text(encoding="utf-8")) if discovery_file.exists() else []
    previous_by_day={row["race_date"]:row for row in previous if row.get("race_ids")}
    discoveries=[]
    discovery_network_metrics=[]
    for day in plan_data["discovery_dates"]:
        if day in previous_by_day:
            discoveries.append(previous_by_day[day])
        else:
            try:
                with fetch_pipeline.fetch_context(job_id=plan_data["batch_id"]+"-discovery") as lookup_metrics:
                    ids,status = await fetch_jra_race_ids(day,force_refresh=True)
                discovery_network_metrics.append(dict(lookup_metrics))
                discoveries.append({"race_date":day,"race_ids":ids,"status":status})
            except Exception as error:
                discoveries.append({"race_date":day,"race_ids":[],"status":"unverified","error":str(error)[:300]})
    write(discovery_file,discoveries)
    for item in discoveries:
        if item.get("race_date") not in plan_data["discovery_dates"] or any(
                not is_jra_race_id(rid,item["race_date"]) for rid in item.get("race_ids",[])):
            raise ValueError("repair-discovery-scope-invalid")
        if item["race_ids"]:
            with ro(DB_PATH) as db:
                prior=db.execute("SELECT expected_race_ids_json FROM scrape_scoped_date_completeness WHERE race_scope='jra' AND race_date=?",(item["race_date"],)).fetchone()
                if prior: item["race_ids"]=sorted(set(item["race_ids"])|set(json.loads(prior[0])))
            await asyncio.to_thread(record_scoped_expectation,DB_PATH,item["race_date"],item["race_ids"])
        for race_id in item["race_ids"]:
            targets.setdefault(race_id,{"race_id":race_id,"race_date":item["race_date"],"reasons":["calendar_day_without_saved_races"]})

    selected = [item for item in sorted(targets.values(),key=lambda item:(item["race_date"],item["race_id"])) if item["race_id"] not in completed]
    if limit is not None: selected = selected[:limit]
    outcomes = []
    bodies_dir = run_dir/"sources"
    bodies_dir.mkdir(exist_ok=True)
    observed = {}
    original_fetch = race.fetch_text
    halt_reason = None
    async def observe_fetch(*args,**kwargs):
        response,html = await original_fetch(*args,**kwargs)
        match = re.search(r"/race/(\d{12})/", response.normalized_url)
        if match and response.status == 200 and html:
            rid=match.group(1); body_sha=hashlib.sha256(response.body).hexdigest()
            filename=f"{rid}-{body_sha}.html"
            body_path=bodies_dir/filename
            if not body_path.exists(): body_path.write_bytes(response.body)
            decoded_sha=hashlib.sha256(html.encode("utf-8")).hexdigest()
            decoded_path=bodies_dir/f"{rid}-{decoded_sha}.decoded.html"
            if not decoded_path.exists(): decoded_path.write_bytes(html.encode("utf-8"))
            observed.setdefault(rid,[]).append({"url":response.normalized_url,"status":response.status,
                "body_sha256":body_sha,"html_path":str(body_path),"parsed_source_date":source_date(html),
                "decoded_html_sha256":decoded_sha,"decoded_html_path":str(decoded_path),
                "fetch_source":response.source,"cache_snapshot":response.cache_snapshot,
                "observed_at_utc":datetime.now(timezone.utc).isoformat()})
        return response,html
    race.fetch_text = observe_fetch
    semaphore=asyncio.Semaphore(2)
    async def worker(item,session):
        nonlocal halt_reason
        rid,day=item["race_id"],item["race_date"]
        async with semaphore:
            control=fetch_pipeline.get_fetch_control_status()
            if control.get("blocked_reason"):
                halt_reason="provider_access_paused:"+str(control["blocked_reason"])
            if halt_reason:
                outcome={"race_id":rid,"race_date":day,"status":"deferred","reason":halt_reason}
                emit(outcome);return outcome
            with ro(DB_PATH) as db:
                excluded=db.execute("SELECT excluded_from_standard FROM scrape_race_acquisition_state WHERE race_id=?",(rid,)).fetchone()
                if excluded and excluded[0]:
                    outcome={"race_id":rid,"race_date":day,"status":"quarantined_not_reset"}
                    emit(outcome);return outcome
            try:
                reusing = reuse_fetched_sources and rid in prior_pending and any(bodies_dir.glob(rid+"-*.decoded.html"))
                candidate=await asyncio.wait_for(race.scrape_race_full(session,rid,date_hint=day,
                    quick_mode=True,force_refresh=not reusing,enrich_horse_details=False),timeout=150)
                validate_scoped_payload(candidate,rid,day,"jra")
                receipt=validate_result_source(candidate,observed.get(rid,[]),rid,day)
                receipts=[receipt]
                report=classify_race_quality(candidate)
                if not report.valid_for_date_completion:
                    raise RepairConflict("repair-source-quality:"+";".join(report.required_errors))
                evidence={"kind":"historical-result-repair","receipts":receipts,
                    "not_prestart_capture":True,"reasons":item["reasons"]}
                changed,merged=await asyncio.to_thread(save_result_repair,DB_PATH,candidate,
                    batch_id=plan_data["batch_id"],evidence=evidence)
                report=classify_race_quality(merged)
                await asyncio.to_thread(record_race_quality,DB_PATH,race_date=day,race_id=rid,report=report,
                    source_status="verified_result_repair",race_data=merged,record_observation=False)
                outcome={"race_id":rid,"race_date":day,"status":"saved","changes":changed,
                    "source_cache_reuse_requested":bool(reusing),
                    "field_states":report.field_states,"source_body_sha256":receipts[-1]["body_sha256"]}
            except Exception as error:
                outcome={"race_id":rid,"race_date":day,"status":"pending","reason":str(error)[:500]}
            control=fetch_pipeline.get_fetch_control_status()
            if (control.get("blocked_reason") or metrics.get("status_403",0)>0 or metrics.get("status_429",0)>0):
                halt_reason="provider_restriction_observed_no_further_work_started"
            emit(outcome)
            return outcome
    started=time.monotonic()
    try:
        with fetch_pipeline.fetch_context(job_id=plan_data["batch_id"]) as metrics:
            async with aiohttp.ClientSession(headers=get_random_headers(),
                    connector=aiohttp.TCPConnector(limit=2,limit_per_host=1),
                    timeout=aiohttp.ClientTimeout(total=100)) as session:
                outcomes=await asyncio.gather(*(worker(item,session) for item in selected))
            metrics=dict(metrics)
    finally:
        race.fetch_text=original_fetch
    touched=sorted({item["race_date"] for item in outcomes if item["status"]=="saved"})
    date_audits=[]
    for day in touched:
        with ro(DB_PATH) as db:
            row=db.execute("SELECT expected_race_ids_json FROM scrape_scoped_date_completeness WHERE race_scope='jra' AND race_date=?",(day,)).fetchone()
            ids=set(json.loads(row[0])) if row else set()
            for rid,raw in db.execute("SELECT race_id,data FROM races_ultimate WHERE race_id BETWEEN ? AND ?",(day[:4],str(int(day[:4])+1))):
                if is_jra_race_id(rid) and normalized_date(json.loads(raw).get("date"))==day: ids.add(rid)
        await asyncio.to_thread(invalidate_scoped_no_race_dates,DB_PATH,[day])
        await asyncio.to_thread(invalidate_verified_no_race_dates,DB_PATH,[day])
        result=await asyncio.to_thread(run_scoped_date_repair_audit,DB_PATH,day,sorted(ids))
        date_audits.append(result)
    after=preservation_receipt()
    protected_ok=all(after[key]==value for key,value in before.items() if key!="forward_processes")
    report={"schema":"jra-quality-repair-execution-v1","finished_at_utc":datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds":time.monotonic()-started,"selected":len(selected),"total_plan_with_discovery":len(targets),
        "reuse_fetched_sources":reuse_fetched_sources,
        "outcome_counts":dict(Counter(item["status"] for item in outcomes)),"outcomes":outcomes,
        "fetch_metrics":metrics,"fetch_metrics_scope":"result_repair_only", "discovery_fetch_metrics":discovery_network_metrics,
        "date_audits":date_audits,"protected_files_unchanged":protected_ok,
        "protected_before":before,"protected_after":after,"training_started":False,"ui_purchase_or_activation":False}
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    write(run_dir/f"execution-{stamp}.json",report)
    print(json.dumps({"stage":"finished","outcome_counts":report["outcome_counts"],"protected_files_unchanged":protected_ok,"fetch_metrics":metrics},ensure_ascii=False),flush=True)
    if not protected_ok: raise RuntimeError("repair-protected-files-changed")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir",required=True,type=Path)
    parser.add_argument("--run-dir",required=True,type=Path)
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--allow-network",action="store_true")
    parser.add_argument("--limit",type=int)
    parser.add_argument("--reuse-fetched-sources",action="store_true")
    args=parser.parse_args()
    run_dir=args.run_dir.resolve()
    intended=(ROOT/"reports/generated/data-quality/repair").resolve()
    if not run_dir.is_relative_to(intended): raise ValueError("repair-output-outside-intended-directory")
    run_dir.mkdir(parents=True,exist_ok=True)
    data=json.loads((run_dir/"plan.json").read_text(encoding="utf-8")) if (run_dir/"plan.json").exists() else plan(args.audit_dir.resolve(),run_dir)
    if args.execute:
        if not args.allow_network: parser.error("--allow-network is required for explicit repair")
        if args.limit is not None and args.limit<1: parser.error("positive --limit required")
        asyncio.run(execute(data,run_dir,limit=args.limit,reuse_fetched_sources=args.reuse_fetched_sources))
    else:
        print(json.dumps({"plan":str(run_dir/"plan.json"),"targets":len(data["targets"]),"reason_counts":dict(Counter(reason for item in data["targets"] for reason in item["reasons"])),"network_requests":0,"production_writes":0},ensure_ascii=False))


if __name__=="__main__":
    main()

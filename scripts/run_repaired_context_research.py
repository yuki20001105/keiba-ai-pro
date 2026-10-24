"""One finite, repair-gated, isolated retrospective feature study.

Never fetches a page, edits live data, stops another process, publishes a model,
or modifies the H04 collector. An exhausted independently audited repair pass
is required; source-unavailable clocks stay missing, never invented or zero.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import html
import json
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
import unicodedata
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / p) for p in ("scripts", "python-api", "keiba")]
OUTPUT = ROOT / "reports/generated/context-repaired-v2"
BULK = ROOT / "reports/generated/data-quality/feature-gap/20261017-last3f-network-full-v1"
REPAIR_PLAN = ROOT / "reports/generated/data-quality/feature-gap/20261017-last3f-v2"
REPAIR_PLAN_SHA = "71b36328587ceff9a80272994c1fff479661a51a94b77e9c3811477cb1898675"
LIVE_DB = ROOT / "keiba/data/keiba_ultimate.db"
SCHEMA = "repair-gated-context-research-v2"
ALIASES = {"良":"良", "稍":"稍重", "稍重":"稍重", "重":"重", "不":"不良", "不良":"不良"}
WAIT_SECONDS = 30
MAX_WAIT_SECONDS = 24 * 60 * 60


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def exclusive(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def status(directory, stage, **extra):
    value = {"schema":SCHEMA,"stage":stage,"observed_at_utc":now(),**extra}
    pending = directory / (".status-"+uuid.uuid4().hex+".tmp")
    exclusive(pending,value)
    os.replace(pending,directory/"status.json")
    print(json.dumps(value,ensure_ascii=False),flush=True)


def protections():
    from evaluate_db_shared_venues import preservation_receipt
    return preservation_receipt()


def core(value):
    return {k:v for k,v in value.items() if k != "forward_processes"}


def guard(plan):
    current = protections()
    if core(current) != core(plan["protected_before"]):
        raise ValueError("active-model-or-h04-protected-state-changed")
    for reference in plan.get('service_processes',[]):
        if not process_alive(reference):
            raise ValueError('protected-api-or-forward-process-exited-or-replaced')
    for relative,expected in plan["code_sha256"].items():
        if sha(ROOT/relative)!=expected:
            raise ValueError("research-code-changed:"+relative)
    return current


def safe_directory(path, *, new=False):
    path=Path(path).absolute()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("research-path-link-forbidden")
    resolved=path.resolve()
    if resolved.parent!=OUTPUT.resolve() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}",resolved.name):
        raise ValueError("fixed-new-research-output-required")
    if new and resolved.exists():
        raise FileExistsError(resolved)
    return resolved


def normalize_going(value):
    """Only source-defined aliases; unknowns remain missing, never good going."""
    if value is None:
        return None,"missing"
    try:
        import pandas as pd
        if pd.isna(value):
            return None,"missing"
    except ImportError:
        pass
    token=unicodedata.normalize("NFKC",str(value)).strip()
    if not token:
        return None,"missing"
    if token in ALIASES:
        return ALIASES[token],"normalized" if token!=ALIASES[token] else "canonical"
    return None,"unrecognized"


def normalized_frame(frame):
    value=frame.copy(deep=True)
    counts=Counter()
    pairs=Counter()
    normalized=[]
    for token in value.field_condition:
        result,reason=normalize_going(token)
        normalized.append(result)
        counts[reason]+=1
        pairs[(str(token),str(result))]+=1
    import pandas as pd
    value["field_condition"]=pd.Series(normalized,index=value.index,dtype="string")
    clock=pd.to_numeric(value.last_3f_time,errors="coerce")
    invalid=clock.notna() & ~clock.between(10,100,inclusive="neither")
    value["last_3f_time"]=clock.mask(invalid)
    return value,{"counts":dict(counts),"transitions":[{"raw":a,"normalized":b,"rows":n} for (a,b),n in sorted(pairs.items())],
                  "last3f_canonical_missing_before":int(clock.isna().sum()),"last3f_finite_out_of_source_domain_to_missing":int(invalid.sum()),
                  "last3f_valid_domain":"10 < seconds < 100, same as sealed source parser", "raw_snapshot_preserved":True}


def repair_gate(receipt):
    """Exit zero is insufficient. Unavailable clocks are not invented.

    A full attempted pass may retain a few verified source-missing/invalid
    clocks. It is a usable research snapshot only after the per-race journal
    proves all nonterminal rows are such attempts, never pending or masked.
    """
    flags=("safety_verification_pass","source_integrity_pass","journal_audit_pass",
           "protected_state_unchanged","verification_source_epoch_stable","wrapper_and_verification_code_unchanged")
    if not isinstance(receipt,dict) or any(receipt.get(k) is not True for k in flags):
        return False,"repair-safety-or-journal-audit-not-pass"
    if (receipt.get("schema")!="last3f-single-network-run-wrapper-v1" or receipt.get("batch_id")!=REPAIR_PLAN.name
            or receipt.get("plan_sha256")!=REPAIR_PLAN_SHA or receipt.get("limit")!=24565
            or receipt.get("network_cli_invocations")!=1 or receipt.get("automatic_retries")!=0):
        return False,"repair-fixed-plan-or-single-attempt-binding-invalid"
    if receipt.get("execution_outcome") not in {"completed","completed_with_domain_missing","partial_pending_sources"}:
        return False,"repair-pass-not-exhausted-or-interrupted"
    child=receipt.get("child",{})
    final=receipt.get("child_final_status",{})
    if child.get("confirmed_exited") is not True or type(child.get("exit_code")) is not int or child.get("exit_code")!=0:
        return False,"repair-child-not-confirmed-clean-exit"
    counts=final.get("race_status_counts",{})
    if (final.get("schema")!="last3f-gap-status-v2" or final.get("phase")!="network"
            or final.get("cache_phase_complete") is not True or final.get("stop_reason") not in (None,"")
            or final.get("protected_state_unchanged") is not True
            or final.get("status")!=receipt["execution_outcome"]
            or not isinstance(counts,dict) or any(type(n) is not int or n<0 for n in counts.values())
            or sum(counts.values())!=final.get("target_races")
            or any(n for name,n in counts.items() if name not in {"completed","domain_missing","partial"})
            or type(final.get("remaining_nonterminal_races")) is not int
            or final["remaining_nonterminal_races"]!=counts.get("partial",0)):
        return False,"repair-has-pending-or-stop-reason"
    if receipt["execution_outcome"]=="partial_pending_sources" and counts.get("partial",0)==0:
        return False,"partial-status-with-no-partial-source-ledger"
    return True,None


def source_missing_remainders(latest):
    held=[]
    for rid,item in latest.items():
        if item.get("status") in {"completed","domain_missing"}:
            continue
        states=item.get("remaining_source_states",{})
        if (item.get("status")!="partial" or item.get("phase")!="network"
                or item.get("identity_grade")!="canonical_url_plus_visible_date_and_all_runners"
                or item.get("reason") not in (None,"") or item.get("source_verified") is False
                or not re.fullmatch(r"[0-9a-f]{64}",str(item.get("proof_sha256") or ""))
                or not isinstance(item.get("proof_file"),str)
                or not isinstance(states,dict) or not states
                or any(name not in {"invalid","missing"} or type(n) is not int or n<0 for name,n in states.items())
                or sum(states.values())<=0 or sum(states.values())!=item.get("remaining_gap_runners")):
            raise ValueError("remaining-clock-is-not-verified-unavailable-source:"+str(rid))
        held.append({"race_id":rid,"remaining_gap_runners":item["remaining_gap_runners"],"source_states":states,
                     "proof_sha256":item.get("proof_sha256"),"missing_values_retained":True})
    return held


def process_alive(reference):
    import psutil
    try:
        process=psutil.Process(reference["pid"])
        return process.is_running() and abs(process.create_time()-reference["created_at_epoch"])<.001
    except psutil.NoSuchProcess:
        return False


def no_acquisition_writer_alive():
    import psutil
    for process in psutil.process_iter(["cmdline"]):
        try:
            command=process.info.get("cmdline") or []
            if any(Path(str(token)).name.casefold() in {"repair_last3f_gaps_v2.py","run_last3f_gap_network_once.py"} for token in command):
                return False
        except (psutil.NoSuchProcess,psutil.AccessDenied):
            continue
    return True


def verify_repair_files(receipt, directory):
    # The frozen wrapper's independent receipts bind raw evidence + DB journal.
    audit=directory/"journal_audit.json"
    if sha(audit)!=receipt.get("journal_audit_sha256") or read(audit).get("audit_pass") is not True:
        raise ValueError("repair-journal-receipt-hash-mismatch")
    source=Path(receipt.get("source_output_dir", ""))/"source_integrity_receipt.json"
    expected=(ROOT/"reports/generated/data-quality/feature-gap").resolve()
    if source.resolve().parent.parent!=expected or source.is_symlink():
        raise ValueError("repair-source-receipt-path-invalid")
    if sha(source)!=receipt.get("source_integrity_receipt_sha256") or read(source).get("artifact_integrity_pass") is not True:
        raise ValueError("repair-source-receipt-hash-mismatch")
    from repair_last3f_gaps_v2 import Journal
    journal=Journal(REPAIR_PLAN,REPAIR_PLAN_SHA)
    final=receipt["child_final_status"]
    if journal.count!=final.get("event_count") or journal.last_hash!=final.get("last_event_sha256") or len(journal.latest)!=final.get("target_races"):
        raise ValueError("repair-final-source-event-chain-mismatch")
    held=source_missing_remainders(journal.latest)
    if len(held)!=final.get("remaining_nonterminal_races"):
        raise ValueError("repair-missing-only-ledger-count-mismatch")
    for row in held:
        event=journal.latest[row['race_id']]
        proof_path=(REPAIR_PLAN/event['proof_file']).resolve(strict=True)
        if not proof_path.is_relative_to(REPAIR_PLAN.resolve()) or sha(proof_path)!=event['proof_sha256']:
            raise ValueError('repair-unavailable-source-proof-not-bound')
        proof=read(proof_path)
        evidence=proof.get('evidence',{})
        if (proof.get('schema')!='last3f-repair-source-proof-v1' or evidence.get('race_id')!=row['race_id']
                or evidence.get('source',{}).get('identity_grade')!=event['identity_grade']):
            raise ValueError('repair-unavailable-source-proof-not-validated-result')
    return {"completion_sha256":sha(directory/"completion_receipt.json"),"journal_sha256":sha(audit),"source_sha256":sha(source),
            "unavailable_clock_remainders":held,"all_targets_attempted_or_completed":True,
            "all_clocks_complete":not held and not final.get('race_status_counts',{}).get('domain_missing',0),
            "partial_outcome_not_renamed_completed":bool(held)}


def register(directory):
    import repaired_context_evaluation as evaluation
    from training import db_context_fit as fit
    directory=safe_directory(directory,new=True)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    directory.mkdir(exist_ok=False)
    own={p:sha(ROOT/p) for p in ("scripts/run_repaired_context_research.py","scripts/repaired_context_evaluation.py")}
    # fit's semantic module ledger plus exact existing driver and quality files.
    sources=fit.source_hashes()
    extra=["scripts/evaluate_db_context.py","scripts/evaluate_db_shared_venues.py",
           "python-api/scraping/source_normalization.py","scripts/run_last3f_gap_network_once.py",
           "scripts/repair_last3f_gaps_v2.py","scripts/verify_last3f_gap_artifacts.py",
           "python-api/scraping/last3f_evidence.py"]
    code={**own,**{p:sha(ROOT/p) for p in extra}}
    # Bind all imported production formula sources without changing any of them.
    import importlib
    for module_name,expected in sources.items():
        try:
            module=importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        relative=Path(module.__file__).resolve().relative_to(ROOT).as_posix()
        if sha(ROOT/relative)!=expected:
            raise ValueError("source-module-sha-mismatch:"+module_name)
        code[relative]=expected
    before=protections()
    import psutil
    repair_processes=[]
    for pid in (41016,42652,40136,6676):
        try:
            process=psutil.Process(pid)
            command=process.cmdline()
            if any(Path(str(p)).name.casefold() in {'repair_last3f_gaps_v2.py','run_last3f_gap_network_once.py'} for p in command):
                repair_processes.append({"pid":pid,"created_at_epoch":process.create_time(),"command":command})
        except psutil.NoSuchProcess:
            pass
    if not repair_processes and not (BULK/'completion_receipt.json').exists():
        raise RuntimeError('no-existing-repair-process-or-completion-receipt')
    service_processes=[]
    for pid in (52324,30904,19600,27320):
        process=psutil.Process(pid)
        service_processes.append({'pid':pid,'created_at_epoch':process.create_time(),'command':process.cmdline()})
    plan={"schema":SCHEMA,"registered_at_utc":now(),"operation":"wait_for_verified_repair_then_one_fixed_study",
          "repair_output":str(BULK),"source_database":str(LIVE_DB),"period":["20160101","20260930"],
          "history_start":"20130101","test_years":list(range(2016,2027)),
          "groups":evaluation.groups(),"train":"2013 through TEST year minus 2","cal":"previous full year",
          "test":"whole year; 2026 through September 30","presets":fit.PRESETS,
          "normalization_aliases":ALIASES,"unrecognized_going":"missing; retain raw snapshot and record token",
          "missing_values_zero_imputed":False,"history_policy":"history_race_date < target_race_date; same-day results excluded",
          "completion_gate":"all targets attempted or completed plus independent source/journal PASS; retain only identity-verified source-missing/invalid clocks; pending/masked/stopped never pass",
          "unavailable_clock_policy":"partial_pending_sources remains partial; only validated exhausted missing/invalid source observations may remain NaN in identical-cohort research",
          "max_wait_seconds":MAX_WAIT_SECONDS,"workers":3,"threads_per_worker":2,"device":"cpu",
          "preset_parallel":False,"bootstrap_repetitions":1000,"bootstrap_seed":20261017,
          "bootstrap_intervals":"nominal paired 95% by whole date; descriptive, not simultaneous multiple-test approval",
          "automatic_winner_adoption":False,
          "paired_comparisons":[["condition_weight60","baseline46"],["condition_weight_gate_style73","baseline46"],["pedigree53","baseline46"],["full80","baseline46"],["full80","condition_weight_gate_style73"]],
          "probability_metrics":["NLL","Brier","ECE","selected_ECE","odds_band_calibration","top1_selection_calibration"],
          "accuracy_metrics":["race_macro_Spearman","runner_pooled_RMSE_mps","Top1"],
          "calibration":"CAL-only race temperature; never TEST-fit calibration",
          "rank_and_rmse_comparison":"speed models on identical cohort, NOT RMSE comparison to active winner-NLL model",
          "previous_historical_results_seen":True,"unknown_future_holdout":False,"optuna":False,
          "network":False,"roi_evaluated":False,"production_publish":False,"activate_model":False,"commit_push":False,
          "protected_before":before,"repair_processes":repair_processes,'service_processes':service_processes,"code_sha256":code,"fit_sources":sources,
          "runner_path":"scripts/run_repaired_context_research.py","runner_sha256":own["scripts/run_repaired_context_research.py"]}
    exclusive(directory/"registered_plan.json",plan)
    exclusive(directory/"registration_receipt.json",{"registered_plan_sha256":sha(directory/"registered_plan.json"),"recorded_at_utc":now()})
    status(directory,"waiting_for_verified_repair",new_training_started=False,repair_output=str(BULK))
    return plan


def wait_for_repair(directory,plan):
    receipt_path=Path(plan["repair_output"])/"completion_receipt.json"
    deadline=time.monotonic()+plan["max_wait_seconds"]
    while not receipt_path.exists():
        if (directory/"stop-request.json").exists():
            raise RuntimeError("research-stop-requested-before-data-freeze")
        if time.monotonic()>deadline:
            raise TimeoutError("repair-verification-not-completed-within-one-day-no-auto-retry")
        guard(plan)
        if not any(process_alive(p) for p in plan["repair_processes"]):
            raise RuntimeError("repair-wrapper-exited-without-completion-receipt-no-retry")
        time.sleep(WAIT_SECONDS)
    receipt=read(receipt_path)
    passed,reason=repair_gate(receipt)
    if not passed:
        raise ValueError(reason)
    exit_deadline=time.monotonic()+90
    while any(process_alive(p) for p in plan["repair_processes"]):
        if time.monotonic()>exit_deadline:
            raise RuntimeError("repair-wrapper-still-alive-after-completion-receipt")
        time.sleep(1)
    if not no_acquisition_writer_alive():
        raise RuntimeError("another-acquisition-writer-started-no-snapshot")
    verified=verify_repair_files(receipt,Path(plan["repair_output"]))
    exclusive(directory/"repair_gate_receipt.json",{"passed":True,"recorded_at_utc":now(),"receipt_path":str(receipt_path),**verified,
        "remaining_missing_are_not_invented":True,"domain_missing_is_separately_audited":True})
    return receipt


def snapshot(directory,plan):
    guard(plan)
    if (directory/'stop-request.json').exists():
        raise RuntimeError('research-stop-requested-before-snapshot')
    if not no_acquisition_writer_alive():
        raise RuntimeError('acquisition-writer-started-before-snapshot')
    import psutil
    if psutil.virtual_memory().available<6*1024**3:
        raise MemoryError("research-preparation-needs-six-gib-available-no-restart-performed")
    source=Path(plan["source_database"]).resolve(strict=True)
    if source!=LIVE_DB.resolve(strict=True):
        raise ValueError("research-fixed-live-database-required")
    if shutil.disk_usage(directory).free<2*source.stat().st_size+3*1024**3:
        raise OSError("insufficient-disk-for-isolated-research-snapshot")
    target=directory/"snapshot.sqlite3"
    if target.exists():
        raise FileExistsError("research-snapshot-must-be-new")
    deadline=time.monotonic()+600
    def progress(status_code,remaining,total):
        if time.monotonic()>deadline:
            raise TimeoutError("sqlite-online-backup-deadline-no-source-mutation")
    with closing(sqlite3.connect(source.as_uri()+"?mode=ro",uri=True,timeout=3)) as src:
        src.execute("PRAGMA query_only=ON")
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst,pages=4096,progress=progress,sleep=.01)
    # This is the separate copy only; never checkpoint/vacuum the live source.
    with closing(sqlite3.connect(target)) as db:
        db.execute("PRAGMA journal_mode=DELETE")
    digest=sha(target)
    exclusive(directory/"snapshot_receipt.json",{"snapshot":str(target),"sha256":digest,"recorded_at_utc":now(),
        "source_open_mode":"ro + query_only","source_database_writes":0,"physical_copy_isolated":True})
    return target,digest


def run_jobs(directory,plan,jobs,worker):
    """At most three owned fits in flight; drift never dispatches another."""
    guard(plan)
    if (directory/'stop-request.json').exists():
        raise RuntimeError('research-stop-requested-before-fit-dispatch')
    executor=ProcessPoolExecutor(max_workers=3,mp_context=multiprocessing.get_context('spawn'))
    pending={}
    iterator=iter(jobs)
    completed=0
    def add_next():
        try:
            job=next(iterator)
        except StopIteration:
            return
        pending[executor.submit(worker,job)]=job
    try:
        for _ in range(min(3,len(jobs))):
            add_next()
        while pending:
            guard(plan)
            if (directory/'stop-request.json').exists():
                raise RuntimeError('research-stop-requested-no-more-fits')
            done,_=wait(pending,timeout=5,return_when=FIRST_COMPLETED)
            finished=[]
            for future in done:
                pending.pop(future)
                finished.append(future.result())
            for item in finished:
                completed+=1
                guard(plan)
                status(directory,'training',jobs=len(jobs),completed=completed,last_completed=item)
            for _ in finished:
                add_next()
    except BaseException:
        for future in pending:
            future.cancel()
        status(directory,'stopping_owned_research_fits',completed=completed,queued_fits_not_started=True,
               note='Only already-running owned fits may finish. No external process is signalled.')
        raise
    finally:
        executor.shutdown(wait=True,cancel_futures=True)
    return completed


def yearly_quality(frame):
    import pandas as pd
    output=[]
    fields=("horse_id","time_seconds","last_3f_time","horse_weight","horse_weight_change","field_condition","sire","damsire","_ctx_history_corners")
    for year,part in frame.groupby(frame.race_date.astype(str).str[:4],sort=True):
        item={"year":str(year),"rows":len(part),"races":int(part.race_id.nunique()),"fields":{}}
        for field in fields:
            series=part[field]
            present=series.notna()
            if field in ("horse_id","sire","damsire"):
                from experiments.features import _identity
                present &= series.map(lambda x:_identity(x) is not None)
            elif field=="field_condition":
                present &= series.isin({"良","稍重","重","不良"})
            item["fields"][field]={"available":int(present.sum()),"missing":int((~present).sum()),"coverage":float(present.mean())}
        output.append(item)
    return output


def prepare(directory,plan,snapshot_path,snapshot_sha):
    from training import db_context_features as source, db_context_fit as fit
    target,history,coverage=source.load_db_frames(snapshot_path,"2013-01","2026-09")
    target,normal_target=normalized_frame(target)
    history,normal_history=normalized_frame(history)
    raw=source.build_training_features(target,history)
    point=raw.attrs.get("point_in_time_audit",{})
    context=raw.attrs.get("context_audit",{})
    if (point.get("same_day_future_references")!=0 or point.get("future_leakage_rows")!=0
            or point.get("same_day_results_excluded") is not True
            or context.get("target_corners_or_outcomes_consumed") is not False
            or context.get("same_day_results_used") is not False):
        raise ValueError("new-research-prior-only-feature-audit-not-pass")
    quality=yearly_quality(history)
    # Distinguish an absent history from a history with missing last3f clocks.
    missingness={name:{"missing":int(raw[name].isna().sum()),"rate":float(raw[name].isna().mean())} for name in raw}
    receipt={"schema":SCHEMA,"snapshot_sha256":snapshot_sha,"target_rows":len(target),"target_races":int(target.race_id.nunique()),
        "history_rows":len(history),"history_races":int(history.race_id.nunique()),"normalization":{"target":normal_target,"history":normal_history},
        "yearly_raw_quality":quality,"feature_missingness":missingness,"no_history_target_rows":int(raw.safe_prior_starts.eq(0).sum()),
        "point_in_time_audit":point,"context_audit":context,"coverage":coverage,
        "unavailable_raw_values_not_imputed":True,"late_capture_not_promoted_to_prestart_proof":True,
        "average_clock_missingness_differs_from_raw_clock_missingness":True,
        "normalized_only_in_isolated_dataframe":True,"live_source_modified":False}
    # Preserve audit attrs in the separate receipt, not per-worker parquet metadata.
    target,raw=fit.prepare(target,raw)
    target.attrs={};raw.attrs={}
    target_path=directory/"target.parquet";feature_path=directory/"features.parquet"
    target.to_parquet(target_path,index=False);raw.to_parquet(feature_path,index=False)
    if sha(snapshot_path)!=snapshot_sha:
        raise ValueError("research-frozen-source-snapshot-mutated")
    exclusive(directory/"preparation.json",receipt)
    return {"target":str(target_path),"features":str(feature_path),"target_sha":sha(target_path),"features_sha":sha(feature_path)}


def report_html(directory,result):
    def cell(x):
        return html.escape(str(x))
    rows=[]
    probability_rows=[]
    labels={'baseline46':'基準46','condition_weight60':'条件・馬体重60',
            'condition_weight_gate_style73':'条件・馬体重・枠・脚質73','pedigree53':'基準＋血統53','full80':'全80'}
    def display(x):
        return '未算出' if x is None else f'{x:.6f}' if isinstance(x,float) else x
    for name,item in result["summaries"].items():
        acc=item["accuracy"]
        rows.append('<tr>'+''.join('<td>'+cell(display(x))+'</td>' for x in (labels[name],acc['rank_correlation'],acc['rmse_speed_mps'],acc['top1'],acc['race_count'],acc['runner_count']))+'</tr>')
        prob=item['probability']
        probability_rows.append('<tr>'+''.join('<td>'+cell(display(x))+'</td>' for x in (labels[name],prob['nll'],prob['brier'],prob['all_runner_ece'],prob['selection_ece'],prob['race_count']))+'</tr>')
    body='<!doctype html><html lang="ja"><meta charset="utf-8"><title>補完・馬場正規化後の特徴量比較</title><style>body{font:16px/1.7 Meiryo,sans-serif;margin:35px;max-width:1400px;color:#183047}th,td{padding:10px;border-bottom:1px solid #ddd}th{background:#234d66;color:white}pre{white-space:pre-wrap}</style><h1>補完・馬場正規化後の特徴量比較</h1><p>2016〜2026年9月の既知履歴の時系列再評価。使用中の勝率モデル・H04・前向き収集は変更していません。回収率・未知期間の優位性・本番採用の証明ではありません。</p><h2>順位・速度精度</h2><table><tr><th>候補</th><th>順位相関</th><th>RMSE m/s</th><th>Top1率</th><th>レース数</th><th>出走数</th></tr>'+''.join(rows)+'</table><h2>勝率校正</h2><table><tr><th>候補</th><th>NLL</th><th>Brier</th><th>全頭ECE</th><th>Top1選択ECE</th><th>対象レース</th></tr>'+''.join(probability_rows)+'</table><p>誤差指標は低いほど良い値です。CALのみで校正し、評価年では再調整していません。全頭とTop1、オッズ帯別の校正表は詳細JSONに分けています。保存オッズの帯別診断は、発走前に実行できるオッズや購入戦略の検証ではありません。</p><h2>追加価値・残欠損</h2><p>日付単位bootstrap95%区間で、53対46と80対73を含む対応比較を実施。preparation.jsonには年別の元時計・通過順位・血統カバー率と、履歴なし／平均値欠損の区別を保存。取得元にもない時計を埋めたとは扱いません。</p><p><a href="results.json">詳細評価JSON</a> / <a href="preparation.json">データ品質・正規化JSON</a> / <a href="repair_gate_receipt.json">補完監査と残欠損JSON</a></p><h2>詳細</h2><pre>'+html.escape(json.dumps(result,ensure_ascii=False,indent=2))+'</pre></html>'
    with (directory/"summary.html").open("x",encoding="utf-8") as stream:
        stream.write(body)


def execute(directory):
    import repaired_context_evaluation as evaluation
    directory=safe_directory(directory)
    plan=read(directory/"registered_plan.json")
    if plan.get("schema")!=SCHEMA or sha(directory/"registered_plan.json")!=read(directory/"registration_receipt.json")["registered_plan_sha256"]:
        raise ValueError("registered-plan-binding-invalid")
    exclusive(directory/"execution.claim.json",{"started_at_utc":now(),"pid":os.getpid(),"single_attempt":True})
    try:
        try:
            import psutil
            psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name=="nt" else 10)
        except (ImportError,OSError,AttributeError):
            pass
        guard(plan)
        wait_for_repair(directory,plan)
        status(directory,"copying_verified_research_source",new_training_started=False)
        snapshot_path,snapshot_sha=snapshot(directory,plan)
        status(directory,"normalizing_and_preparing_features",new_training_started=False)
        prepared=prepare(directory,plan,snapshot_path,snapshot_sha)
        guard(plan)
        import gc
        import psutil
        gc.collect()
        available=psutil.virtual_memory().available
        if available<9*1024**3:
            raise MemoryError('three-research-workers-require-nine-gib-available-no-restart-performed')
        sources={**plan["fit_sources"],**plan["code_sha256"]}
        protocol={**plan,"snapshot_sha256":snapshot_sha,"snapshot_path":str(snapshot_path),"source_hashes":sources,
                  "sources":sources,"period_end":"20260930",**prepared}
        exclusive(directory/"protocol.json",protocol)
        protocol_sha=sha(directory/"protocol.json")
        payload={"output":str(directory),**prepared,"sources":protocol["sources"],"period_end":"20260930",
                 "protocol_sha":protocol_sha,"runner_sha":plan["runner_sha256"]}
        jobs=[{**payload,"year":year,"group":name} for year in plan["test_years"] for name in plan["groups"]]
        status(directory,"training",jobs=len(jobs),workers=3,threads_per_worker=2,completed=0)
        run_jobs(directory,plan,jobs,evaluation.worker)
        status(directory,"aggregating",jobs=len(jobs),completed=len(jobs))
        result=evaluation.aggregate(directory,protocol)
        after=guard(plan)
        exclusive(directory/"after_protection.json",after)
        result.update(protected_state_unchanged=True,research_protocol_sha256=protocol_sha,source_snapshot_sha256=snapshot_sha)
        exclusive(directory/"results.json",result)
        report_html(directory,result)
        # Snapshot digest was already computed; avoid a second giant disk scan.
        files={p.name:(snapshot_sha if p==snapshot_path else sha(p)) for p in directory.iterdir() if p.is_file() and p.name not in {"status.json","artifact_manifest.json","stdout.log","stderr.log"}}
        exclusive(directory/"artifact_manifest.json",{"schema":SCHEMA,"sha256":files})
        status(directory,"completed",jobs=len(jobs),completed=len(jobs),results_path=str(directory/"results.json"),production_activated=False)
    except BaseException as error:
        failure={"schema":SCHEMA,"failed_at_utc":now(),"error_type":type(error).__name__,"reason":str(error),
                 "automatic_retry":False,"existing_services_stopped":False,"model_or_forward_changed_by_this_runner":False}
        exclusive(directory/("failure-"+uuid.uuid4().hex+".json"),failure)
        status(directory,"failed_needs_review",**failure)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir",type=Path,required=True)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--register",action="store_true")
    group.add_argument("--run",action="store_true")
    args=parser.parse_args()
    if args.register:
        register(args.run_dir)
    else:
        execute(args.run_dir)


if __name__=="__main__":
    main()

"""Finite, guarded continuation of one stopped repair and the fixed 55 jobs.

Wait for the existing independent audit; never restart it. Start each new
registered child exactly once, and stop on any audit, data or protection issue.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'python-api'),str(ROOT/'keiba')]
PARENT=ROOT/'reports/generated/last3f-research-continuation'
OLD_STUDY=ROOT/'reports/generated/context-repaired-v2/20261017-after-last3f-v2'
OLD_PLAN_SHA='96cd84e7813ef64f3ede3a1b2dff7b5abf38c1faa82ea2be0c43c6f3360799f3'
FEATURE=ROOT/'reports/generated/data-quality/feature-gap'
OLD_OUTPUT=FEATURE/'20261017-last3f-network-full-v1'
REPAIR=FEATURE/'20261017-last3f-resume-v3'
AUDIT=FEATURE/'20261017-last3f-resume-v3-audit'
STUDY=ROOT/'reports/generated/context-repaired-v3/20261017-after-repair-v3'
PROBE=ROOT/'reports/generated/data-quality/http400-diagnostic/20261017-archive-one-response-v3/receipt.json'
PYTHON=ROOT/'python-api/.venv/Scripts/python.exe'
OWN_CODE=('scripts/continue_last3f_study_v3_once.py','scripts/repair_last3f_gaps_v3.py',
          'scripts/last3f_http_transport_v3.py','scripts/last3f_evidence_v3.py',
          'scripts/audit_last3f_resume_v3.py','scripts/run_repaired_context_research_v3.py',
          'scripts/audit_feature_gap_updates.py','python-api/scraping/fetch_pipeline.py',
          'python-api/scraping/request_pacing.py','python-api/scraping/constants.py',
          'scripts/probe_last3f_archive_v3.py')


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)


def now():return datetime.now(timezone.utc).isoformat()


def main(output):
    from run_repaired_context_research import guard as original_guard
    from run_last3f_gap_network_once import run_process
    from repair_last3f_gaps_v2 import atomic_status,lower_priority
    import psutil
    lower_priority()
    output=Path(output).absolute()
    if output.is_symlink() or any(parent.is_symlink() for parent in output.parents):
        raise ValueError('continuation-linked-output-forbidden')
    output=output.resolve()
    if output.parent!=PARENT.resolve() or output.exists():
        raise ValueError('new-explicit-continuation-output-required')
    if any(path.exists() for path in (REPAIR,AUDIT,STUDY)):
        raise ValueError('continuation-destination-already-exists-no-overwrite')
    if sha(OLD_STUDY/'registered_plan.json')!=OLD_PLAN_SHA:
        raise ValueError('original-study-hash-changed')
    original=read(OLD_STUDY/'registered_plan.json')
    output.mkdir(parents=True)
    code={**original['code_sha256'],**{relative:sha(ROOT/relative) for relative in OWN_CODE}}
    receipt={'schema':'last3f-study-continuation-once-v3','started_at_utc':now(),'pid':os.getpid(),
        'created_at_epoch':psutil.Process().create_time(),'original_study_sha256':OLD_PLAN_SHA,
        'code_sha256':code,'service_processes':original['service_processes'],
        'old_audit':str(OLD_OUTPUT),'repair_run':str(REPAIR),'repair_output':str(AUDIT),'research_run':str(STUDY),
        'network_probe':{'path':str(PROBE),'sha256':sha(PROBE)},'automatic_retries':0,
        'original_outputs_overwritten':False,'active_model_or_forward_changed':False}
    write(output/'start_receipt.json',receipt)
    def guard():
        original_guard(original)
        for relative,digest in code.items():
            if sha(ROOT/relative)!=digest:raise ValueError('continuation-code-changed:'+relative)
        if sha(PROBE)!=receipt['network_probe']['sha256']:raise ValueError('validated-probe-changed')
        if (output/'stop-request.json').exists():raise RuntimeError('operator-stop-request-no-more-stages')
    def state(stage,**extra):
        atomic_status(output/'status.json',{'schema':receipt['schema'],'stage':stage,'observed_at_utc':now(),**extra})
    def command(stage,arguments):
        guard();state(stage)
        result=run_process([str(PYTHON),'-X','utf8',*map(str,arguments)],cwd=ROOT,
            stdout_path=output/(stage+'.stdout.log'),stderr_path=output/(stage+'.stderr.log'))
        write(output/(stage+'-exit.json'),result)
        if result.get('confirmed_exited') is not True or result.get('exit_code')!=0:
            raise RuntimeError('one-shot-stage-failed-no-retry:'+stage)
        guard()
    try:
        guard();state('waiting_for_original_independent_audit',new_network_started=False,new_training_started=False)
        deadline=time.monotonic()+3*60*60
        while not (OLD_OUTPUT/'completion_receipt.json').exists():
            guard()
            if time.monotonic()>deadline:raise TimeoutError('original-audit-not-complete-within-three-hours-no-restart')
            live=False
            for item in original['repair_processes']:
                try:live|=abs(psutil.Process(item['pid']).create_time()-item['created_at_epoch'])<.01
                except psutil.NoSuchProcess:pass
            if not live:raise RuntimeError('original-audit-process-exited-without-receipt')
            time.sleep(30)
        old=read(OLD_OUTPUT/'completion_receipt.json')
        if old.get('execution_outcome')!='stopped' or not all(old.get(key) is True for key in (
            'safety_verification_pass','source_integrity_pass','journal_audit_pass','protected_state_unchanged',
            'verification_source_epoch_stable','wrapper_and_verification_code_unchanged')):
            raise RuntimeError('stopped-original-audit-not-pass-no-new-repair')
        command('register-repair',[ROOT/'scripts/repair_last3f_gaps_v3.py','--run-dir',REPAIR,
            '--output-dir',AUDIT,'--network-probe',PROBE,'--register'])
        command('repair-and-independent-audit',[ROOT/'scripts/repair_last3f_gaps_v3.py','--run-dir',REPAIR,
            '--execute','--allow-network'])
        completed=read(AUDIT/'completion_receipt.json')
        if completed.get('safety_verification_pass') is not True or completed.get('coverage_pass') is not True:
            raise RuntimeError('resumed-repair-or-combined-audit-not-pass-no-training')
        command('register-fixed-study',[ROOT/'scripts/run_repaired_context_research_v3.py','--run-dir',STUDY,
            '--register','--repair-run',REPAIR,'--repair-output',AUDIT,
            '--repair-writer-entrypoint','scripts/repair_last3f_gaps_v3.py'])
        command('fixed-55-study',[ROOT/'scripts/run_repaired_context_research_v3.py','--run-dir',STUDY,'--run'])
        result=read(STUDY/'status.json')
        if result.get('stage')!='completed' or result.get('completed')!=55:
            raise RuntimeError('fixed-study-not-55-completed')
        guard()
        write(output/'completion_receipt.json',{'schema':receipt['schema'],'completed_at_utc':now(),
            'completed':55,'research_manifest':str(STUDY/'artifact_manifest.json'),
            'manifest_sha256':sha(STUDY/'artifact_manifest.json'),'protected_state_unchanged':True,
            'original_outputs_preserved':True,'automatic_retries':0})
        state('completed',completed=55)
    except BaseException as error:
        failure={'schema':receipt['schema'],'failed_at_utc':now(),'error':type(error).__name__+': '+str(error),
                 'automatic_retry':False,'protected_services_stopped':False}
        write(output/'failure.json',failure);state('failed_needs_review',**failure)
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();main(args.output)

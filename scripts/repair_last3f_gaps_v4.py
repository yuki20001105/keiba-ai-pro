"""Explicit V4 orchestration: three read-only parser processes, one writer.

The frozen V3 acquisition, source replay, transaction, independent verifier and
provider policy are unchanged. Only the bounded local parsing executor differs.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'python-api')]


def configured_engine():
    from last3f_cache_processes_v4 import ProcessCacheExecutorV4
    spec=importlib.util.spec_from_file_location('_last3f_repair_v3_private_v4',ROOT/'scripts/repair_last3f_gaps_v3.py')
    engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
    engine.ThreadPoolExecutor=ProcessCacheExecutorV4
    engine.__file__=str(Path(__file__).resolve())
    engine.CODE=tuple(dict.fromkeys((*engine.CODE,'scripts/repair_last3f_gaps_v4.py',
        'scripts/last3f_cache_processes_v4.py','scripts/audit_last3f_resume_v4.py')))
    original_write=engine.write
    def write(path,value):
        if Path(path).name=='plan.json':
            value=deepcopy(value)
            value['execution'].pop('cache_parse_threads',None)
            value['execution'].update(cache_parse_processes=3,cache_worker_native_threads=1,
                database_writers=1,local_implementation_version='v4-cache-processes-audit-baseline',
                audit_io_optimization_enabled=False)
        elif Path(path).name=='continuation_plan.json':
            value={**value,'local_implementation_version':'v4-cache-processes-audit-baseline',
                   'minimum_provider_interval_seconds':3.0,'cache_parse_processes':3,'cache_worker_native_threads':1,
                   'audit_io_optimization_enabled':False}
        return original_write(path,value)
    engine.write=write
    return engine


def execute(run,*,allow_network=False):
    """Do not delegate V3's hardcoded auditor import or child entrypoint."""
    from run_last3f_gap_network_once import run_process
    from audit_last3f_resume_v4 import audit_resume
    engine=configured_engine();run=Path(run).resolve()
    plan,plan_sha,targets,envelope=engine.load(run)
    output=Path(envelope['repair_output']);output.mkdir(exist_ok=False)
    engine.write(output/'start_receipt.json',{'schema':engine.SCHEMA,'started_at_utc':engine.now(),
        'pid':os.getpid(),'run_dir':str(run),'automatic_retries':0,'network_requested':allow_network,
        'local_implementation_version':'v4-cache-processes-audit-baseline',
        'audit_io_optimization_enabled':False})
    engine.write(run/'execution.claim.json',{'started_at_utc':engine.now(),'pid':os.getpid(),'single_attempt':True})
    before=engine.V2.preservation_receipt()
    command=[str(ROOT/'python-api/.venv/Scripts/python.exe'),'-X','utf8',str(Path(__file__).resolve()),
             '--run-dir',str(run),'--acquire']
    if allow_network:command.append('--allow-network')
    child=run_process(command,cwd=ROOT,stdout_path=output/'acquisition.stdout.log',stderr_path=output/'acquisition.stderr.log')
    engine.write(output/'child_exit_receipt.json',child)
    if child.get('confirmed_exited') is not True:raise RuntimeError('v4-child-not-exited-no-audit')
    final=engine.read(run/'status.json');outcome=final.get('status','unconfirmed')
    engine.V2.atomic_status(output/'progress.json',{'schema':engine.SCHEMA,'status':'auditing',
        'acquisition_outcome':outcome,'updated_at_utc':engine.now()})
    audited=audit_resume(engine.OLD_RUN,engine.OLD_OUTPUT,run,output)
    after=engine.V2.preservation_receipt();engine.guard(plan,envelope)
    result={**audited,'schema':'last3f-network-resume-completion-v3','finished_at_utc':engine.now(),
        'local_implementation_version':'v4-cache-processes-audit-baseline',
        'audit_io_optimization_enabled':False,
        'original_plan_sha256':engine.OLD_PLAN_SHA,'old_completion_sha256':envelope['old_completion_sha256'],
        'new_plan_sha256':plan_sha,'continuation_plan_sha256':engine.sha(run/'continuation_plan.json'),
        'execution_outcome':outcome,'automatic_retries':0,'child':child,'old_status_preserved':'stopped',
        'child_final_status':final,'before_protected':before,'after_protected':after,
        'protected_state_unchanged':engine.V2.protected_projection(before)==engine.V2.protected_projection(after),
        'wrapper_and_verification_code_unchanged':True}
    result['safety_verification_pass']=all(result.get(key) is True for key in (
        'source_integrity_pass','journal_audit_pass','coverage_pass','protected_state_unchanged',
        'verification_source_epoch_stable','source_files_stable_after_journal_audit','wrapper_and_verification_code_unchanged'))
    coverage=engine.read(result['combined_coverage_audit'])
    if coverage.get('coverage_pass') is True and child['exit_code']==0 and outcome not in {'stopped','paused','blocked','failed','interrupted'}:
        states=coverage['race_status_counts']
        result['execution_outcome']='partial_pending_sources' if states.get('partial') else 'completed_with_domain_missing' if states.get('domain_missing') else 'completed'
    engine.write(output/'completion_receipt.json',result)
    engine.V2.atomic_status(output/'progress.json',{'schema':engine.SCHEMA,'status':result['execution_outcome'],
        'updated_at_utc':engine.now(),'safety_verification_pass':result['safety_verification_pass'],
        'coverage_pass':coverage.get('coverage_pass',False)})
    print(json.dumps({'completion_receipt':str(output/'completion_receipt.json'),
        'outcome':result['execution_outcome'],'safety_verification_pass':result['safety_verification_pass']},ensure_ascii=False),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path);parser.add_argument('--network-probe',type=Path)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--register',action='store_true');mode.add_argument('--acquire',action='store_true');mode.add_argument('--execute',action='store_true')
    parser.add_argument('--allow-network',action='store_true');args=parser.parse_args()
    engine=configured_engine()
    if args.register:
        if not args.output_dir:parser.error('--output-dir required')
        engine.register(args.run_dir,args.output_dir,network_probe=args.network_probe)
    elif args.acquire:engine.acquire(args.run_dir,allow_network=args.allow_network)
    else:
        try:execute(args.run_dir,allow_network=args.allow_network)
        except BaseException as error:
            output=Path(engine.read(args.run_dir/'continuation_plan.json')['repair_output'])
            if output.is_dir():engine.V2.independent_failure_receipt(output,{'schema':engine.SCHEMA,
                'failed_at_utc':engine.now(),'error':type(error).__name__+': '+str(error),'automatic_retry':False})
            raise


if __name__=='__main__':main()

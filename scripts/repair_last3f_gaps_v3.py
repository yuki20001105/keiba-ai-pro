"""One separately registered cache-first continuation; old evidence is immutable.

Three bounded read/parse threads, one journalled missing-only writer. Network is
optional, preselected and probe-gated, never a fallback after an HTTP error.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'python-api')]
PARENT = ROOT / 'reports/generated/data-quality/feature-gap'
OLD_RUN = PARENT / '20261017-last3f-v2'
OLD_OUTPUT = PARENT / '20261017-last3f-network-full-v1'
OLD_PLAN_SHA = '71b36328587ceff9a80272994c1fff479661a51a94b77e9c3811477cb1898675'
STUDY = ROOT / 'reports/generated/context-repaired-v2/20261017-after-last3f-v2/registered_plan.json'
SCHEMA = 'last3f-network-resume-plan-v3'
CODE = ('scripts/repair_last3f_gaps_v3.py', 'scripts/last3f_evidence_v3.py',
        'scripts/last3f_http_transport_v3.py', 'scripts/audit_last3f_resume_v3.py',
        'scripts/run_last3f_gap_network_once.py','scripts/verify_last3f_gap_artifacts.py',
        'scripts/audit_feature_gap_updates.py','python-api/scraping/fetch_pipeline.py',
        'python-api/scraping/request_pacing.py','python-api/scraping/constants.py')


def private_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


V2 = private_module('_last3f_frozen_v2_private_resume', 'scripts/repair_last3f_gaps_v2.py')
sha, read, write, now = V2.digest_file, V2.read_json, V2.exclusive_json, V2.now


def binding(path):
    return {'path': str(Path(path).resolve()), 'sha256': sha(path)}


def safe_new_path(path):
    path = Path(path).absolute()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('resume-linked-directory-forbidden')
    path = path.resolve()
    if path.parent != PARENT.resolve() or path.exists():
        raise ValueError('new-feature-gap-directory-required')
    return path


def services():
    return read(STUDY)['service_processes']


def guard(plan, envelope):
    import psutil
    V2._check_protection(plan)
    for row in envelope['service_processes']:
        try:
            if abs(psutil.Process(row['pid']).create_time() - row['created_at_epoch']) > .01:
                raise ValueError('protected-service-process-replaced')
        except psutil.NoSuchProcess as exc:
            raise ValueError('protected-service-process-exited') from exc
    for relative, digest in envelope['code_sha256'].items():
        if sha(ROOT / relative) != digest:
            raise ValueError('resume-code-pin-changed:' + relative)
    for name,entry in envelope['original_artifacts'].items():
        info=Path(entry['path']).stat()
        if [info.st_size,info.st_mtime_ns] != envelope['original_fingerprints'][name]:
            raise ValueError('original-repair-artifact-changed')


def register(run, output, *, network_probe=None):
    run, output = safe_new_path(run), safe_new_path(output)
    if run == output:
        raise ValueError('separate-resume-and-output-directories-required')
    old_plan, old_sha, targets = V2.load_plan(OLD_RUN)
    if old_sha != OLD_PLAN_SHA:
        raise ValueError('original-plan-pin-mismatch')
    previous = read(OLD_OUTPUT / 'completion_receipt.json')
    if not all(previous.get(key) is True for key in ('safety_verification_pass', 'source_integrity_pass',
            'journal_audit_pass', 'protected_state_unchanged', 'verification_source_epoch_stable',
            'wrapper_and_verification_code_unchanged')):
        raise ValueError('stopped-original-data-not-independently-verified')
    if previous.get('execution_outcome') != 'stopped':
        raise ValueError('explicit-stopped-original-required')
    journal = V2.Journal(OLD_RUN, old_sha)
    selected = [row for row in targets if journal.latest.get(row['race_id'], {}).get('status') not in V2.TERMINAL]
    run.mkdir()
    plan = deepcopy(old_plan)
    plan.update(batch_id=run.name, created_at_utc=now(), source_epoch_at_plan=previous['after_verification']['source_epoch'])
    plan['counts'] = {'target_races': len(selected), 'original_target_races': len(targets)}
    with (run / 'targets.jsonl').open('xb') as stream:
        for row in selected:
            stream.write(V2.encoded(row) + b'\n')
    plan['targets_sha256'] = sha(run / 'targets.jsonl')
    plan['execution'] = {**plan['execution'], 'http_source': 'https://db.sp.netkeiba.com/race/RACE_ID/',
        'source_policy': 'preselected-archive-mobile-no-error-fallback', 'cache_parse_threads': 3,
        'database_writers': 1, 'consecutive_http400_pause_threshold': 1,
        'minimum_interval_seconds':3.0,'network_probe_required': True}
    write(run / 'plan.json', plan)
    new_sha = sha(run / 'plan.json')
    write(run / 'plan_receipt.json', {'plan_sha256': new_sha, 'batch_id': run.name})
    originals = {name: binding(path) for name, path in {
        'plan':OLD_RUN/'plan.json', 'targets':OLD_RUN/'targets.jsonl', 'events':OLD_RUN/'events.jsonl',
        'completion':OLD_OUTPUT/'completion_receipt.json', 'source':Path(previous['source_verifier_command'][-1])/'source_integrity_receipt.json',
        'journal':OLD_OUTPUT/'journal_audit.json'}.items()}
    # The original command ends in its independently named source output directory.
    envelope = {'schema':SCHEMA, 'registered_at_utc':now(), 'original_run':str(OLD_RUN),
        'original_output':str(OLD_OUTPUT), 'resumed_run':str(run), 'repair_output':str(output),
        'original_plan_sha256':old_sha, 'new_plan_sha256':new_sha,
        'old_completion_sha256':sha(OLD_OUTPUT/'completion_receipt.json'), 'original_artifacts':originals,
        'original_fingerprints':{name:[Path(item['path']).stat().st_size,Path(item['path']).stat().st_mtime_ns]
                                 for name,item in originals.items()},
        'code_sha256':{relative:sha(ROOT/relative) for relative in CODE}, 'service_processes':services(),
        'network_probe':binding(network_probe) if network_probe else None,
        'network_requires_validated_probe':True, 'network_maximum_attempts_per_race':1,
        'automatic_retries':0, 'cache_sources':['db.sp.netkeiba.com','db.netkeiba.com','race.netkeiba.com'],
        'source_policy':'archive-mobile', 'normal_values_overwritten':False, 'zero_fill':False,
        'original_status_preserved':'stopped; never promoted to complete', 'training_or_activation':False}
    guard(plan,envelope)
    write(run/'continuation_plan.json',envelope)
    write(run/'continuation_plan_receipt.json',{'continuation_plan_sha256':sha(run/'continuation_plan.json')})
    V2.atomic_status(run/'status.json',{'schema':SCHEMA,'status':'registered','updated_at_utc':now(),
        'target_races':len(selected),'original_target_races':len(targets),'new_network_started':False})
    return envelope


def load(run):
    plan, digest, targets = V2.load_plan(run)
    envelope = read(Path(run)/'continuation_plan.json')
    if (envelope.get('schema') != SCHEMA or envelope.get('new_plan_sha256') != digest
            or envelope.get('original_plan_sha256') != OLD_PLAN_SHA
            or read(Path(run)/'continuation_plan_receipt.json')['continuation_plan_sha256'] != sha(Path(run)/'continuation_plan.json')):
        raise ValueError('resume-envelope-binding-invalid')
    guard(plan,envelope)
    for entry in envelope['original_artifacts'].values():
        if sha(entry['path']) != entry['sha256']:
            raise ValueError('original-artifact-byte-hash-changed')
    return plan,digest,targets,envelope


def cached_candidate(target, cache_path):
    """Read/parse only. Real timestamps and exact source URLs survive intact."""
    from last3f_evidence_v3 import parse_last3f_evidence_v3
    from scraping.fetch_pipeline import decode_html_body
    rid=target['race_id']
    urls=(f'https://db.sp.netkeiba.com/race/{rid}/',f'https://db.netkeiba.com/race/{rid}/',
          f'https://race.netkeiba.com/race/result.html?race_id={rid}')
    failures=[];verified=[]
    with closing(V2.read_only(cache_path)) as cache:
        for url in urls:
            row=cache.execute('SELECT status,body,fetched_at FROM http_cache WHERE normalized_url=?',(url,)).fetchone()
            if not row or row[0]!=200 or not row[1]:
                continue
            try:
                if isinstance(row[2],bool) or not isinstance(row[2],(int,float)) or not math.isfinite(row[2]) or row[2]<=0:
                    raise ValueError('cache-real-observation-timestamp-required')
                body=bytes(row[1]); observed=datetime.fromtimestamp(row[2],timezone.utc).isoformat()
                receipt={'normalized_url':url,'status':200,'body_sha256':hashlib.sha256(body).hexdigest(),'fetched_at':row[2]}
                html=decode_html_body(body)
                evidence=parse_last3f_evidence_v3(html,race_id=rid,source_url=url,observed_at_utc=observed,
                    raw_body_sha256=receipt['body_sha256'],expected_date=target['race_date'],
                    expected_runners=target['expected_runners'],raw_body=body,
                    allow_cached_transport_identity=True,cache_receipt=receipt)
                verified.append({'evidence':evidence,'body':body,'html':html,'transport':{'cache_receipt':receipt},
                                 'failures':failures})
            except (ValueError,KeyError,TypeError,OverflowError,OSError) as error:
                failures.append({'source_url':url,'reason':str(error)})
    if verified:
        numeric={}
        for item in verified:
            for row in item['evidence']['rows']:
                if row['last3f_state']=='valid':
                    number=row['horse_number']; value=row['last3f_seconds']
                    if number in numeric and abs(numeric[number]-value)>1e-8:
                        return {'failures':failures,'reason':'verified-cache-clock-conflict'}
                    numeric[number]=value
        # Fixed quality-only preference; no race outcomes or model scores.
        return max(verified,key=lambda item:sum(row['last3f_state']=='valid' for row in item['evidence']['rows']))
    return {'failures':failures,'reason':'no-verified-cache-source'}


def apply_verified(run,plan,target,item):
    from last3f_evidence_v3 import parse_last3f_evidence_v3
    evidence=item['evidence']
    source=evidence['source']
    if (source.get('raw_body_sha256')!=hashlib.sha256(item['body']).hexdigest()
            or source.get('decoded_html_sha256')!=hashlib.sha256(item['html'].encode('utf-8')).hexdigest()
            or evidence.get('race_id')!=target['race_id'] or evidence.get('race_date')!=target['race_date']
            or sorted(V2.identity(row) for row in evidence['rows'])!=sorted(V2.identity(row) for row in target['expected_runners'])):
        raise ValueError('verified-source-bytes-or-target-mismatch-no-write')
    arguments={'race_id':target['race_id'],'source_url':source['source_url'],
        'observed_at_utc':source['observed_at_utc'],'raw_body_sha256':source['raw_body_sha256'],
        'expected_date':target['race_date'],'expected_runners':target['expected_runners'],'raw_body':item['body']}
    if source.get('network_transport_receipt'):
        arguments.update(allow_direct_transport_identity=True,transport_receipt=source['network_transport_receipt'])
    elif source.get('cached_transport_receipt'):
        arguments.update(allow_cached_transport_identity=True,cache_receipt=source['cached_transport_receipt'])
    verified=parse_last3f_evidence_v3(item['html'],**arguments)
    if verified != evidence:
        raise ValueError('parsed-clock-evidence-differs-from-original-source-no-write')
    sealed=V2.seal_source(run,evidence,item['body'],item['html'],item['transport'])
    try:
        result=V2.apply_evidence(plan['database'],evidence,batch_id=plan['batch_id'])
        payload,_=V2._current(plan['database'],target)
        status,remainder=V2._result_status(payload,evidence)
        return {'status':status,**result,**remainder,**sealed,'identity_grade':evidence['source']['identity_grade']}
    except (ValueError,KeyError,TypeError) as error:
        return {'status':'pending','reason':str(error),'updated_runners':0,**sealed}


def unavailable(item):
    states=item.get('remaining_source_states',{})
    grades={'canonical_url_plus_visible_date_and_all_runners','cached_transport_url_plus_visible_date_and_all_runners',
            'mobile_cached_transport_url_plus_visible_date_and_all_runners','mobile_network_transport_url_plus_visible_date_and_all_runners'}
    return (item.get('status')=='partial' and bool(states) and not item.get('reason')
        and set(states)<={'missing','invalid'} and all(type(n) is int and n>=0 for n in states.values())
        and sum(states.values())==item.get('remaining_gap_runners') and item.get('proof_file')
        and item.get('identity_grade') in grades)


def write_status(run,plan_sha,targets,journal,state,**extra):
    return V2._write_status(run,plan_sha,targets,journal,status=state,phase='cache' if not extra.get('network') else 'network',**extra)


async def network_pass(run,plan,envelope,targets,journal,record):
    if (run/'stop-request.json').exists():
        return 'stopped','operator-stop-request'
    from last3f_http_transport_v3 import Last3FTransportV3, TransportHalt, SourceRejected
    from market_edge.compute import CollectorMonitor
    from scraping.constants import get_random_headers
    import aiohttp
    probe=envelope.get('network_probe')
    if not probe or sha(probe['path'])!=probe['sha256']:
        return 'partial_pending_sources','archive-network-probe-not-validated'
    receipt=read(probe['path'])
    if (receipt.get('schema')!='last3f-archive-route-probe-v3' or receipt.get('decision')!='PASS'
            or receipt.get('source_policy')!='archive-mobile' or receipt.get('http_status')!=200
            or receipt.get('protected_state_unchanged') is not True or receipt.get('source_db_writes') is not False
            or type(receipt.get('metrics',{}).get('network_requests')) is not int
            or receipt['metrics']['network_requests']!=1):
        return 'partial_pending_sources','archive-network-probe-not-pass'
    todo=[row for row in targets if journal.latest.get(row['race_id'],{}).get('status') not in V2.TERMINAL
          and not unavailable(journal.latest.get(row['race_id'],{}))]
    client=Last3FTransportV3(run/'http_diagnostics',source_policy='archive-mobile')
    monitor=CollectorMonitor(V2.TRACK_PATH)
    masked_streak=0
    async with aiohttp.ClientSession(headers=get_random_headers(),cookie_jar=aiohttp.DummyCookieJar(),
                                    timeout=aiohttp.ClientTimeout(total=30)) as session:
        for target in todo:
            guard(plan,envelope)
            if (run/'stop-request.json').exists():
                return 'stopped','operator-stop-request'
            state=monitor.snapshot(); instant=time.time()
            if not state.get('healthy'):
                return 'paused','collector-unhealthy'
            while state.get('capturing') or any(row['start']-210<=instant<=row['end']+60 for row in state.get('capture_windows',[])):
                if (run/'stop-request.json').exists():
                    return 'stopped','operator-stop-request'
                await asyncio.sleep(1); guard(plan,envelope)
                state=monitor.snapshot();instant=time.time()
                if not state.get('healthy'):
                    return 'paused','collector-unhealthy'
            try:
                result=await client.fetch(session,target)
                item={'evidence':result.evidence,'body':result.response.body,'html':result.html,
                      'transport':{'receipt':result.receipt,'receipt_file':result.receipt_file,
                                   'receipt_sha256':result.receipt_sha256}}
                applied=apply_verified(run,plan,target,item)
                record(target,applied,phase='network')
                masked=(applied.get('status')=='partial' and not applied.get('updated_runners')
                        and applied.get('remaining_source_states',{}).get('masked',0)>0)
                masked_streak=masked_streak+1 if masked else 0
                member=masked and any(token in result.html for token in ('有料会員限定','プレミアム会員限定','会員登録が必要です'))
                if member or masked_streak>=3:
                    return 'paused','source-still-masked-member-gate' if member else 'source-still-masked'
            except SourceRejected as error:
                record(target,{'status':'pending','reason':str(error),'updated_runners':0,
                               'diagnostic':getattr(error,'receipt',{})},phase='network')
            except TransportHalt as error:
                details=getattr(error,'receipt',{})
                record(target,{'status':'blocked','reason':str(error),'updated_runners':0,
                               'diagnostic':details},phase='network')
                return 'blocked','http-transport-circuit-open-no-retry'
    return None,None


def acquire(run,*,allow_network=False):
    run=Path(run).resolve();plan,plan_sha,targets,envelope=load(run)
    if not Path(envelope['repair_output']).is_dir():
        raise ValueError('acquisition-must-have-owned-wrapper-output')
    write(run/'acquisition.claim.json',{'started_at_utc':now(),'pid':__import__('os').getpid(),'single_attempt':True})
    started=time.monotonic();job_state=stop_reason=None
    with V2.execution_lock(run),V2.failure_status(run):
        journal=V2.Journal(run,plan_sha)
        if journal.count:
            raise ValueError('v3-single-execution-no-automatic-retry')
        processed=updated=0
        def record(target,item,phase='cache'):
            nonlocal processed,updated
            journal.emit(**{**item,'phase':phase,'race_id':target['race_id']})
            processed+=1;updated+=int(item.get('updated_runners',0))
            if processed%25==0:
                write_status(run,plan_sha,targets,journal,'running',processed_this_run=processed,
                             updated_runners_this_run=updated,network=phase=='network')
                guard(plan,envelope)
        write_status(run,plan_sha,targets,journal,'running',processed_this_run=0,updated_runners_this_run=0)
        with ThreadPoolExecutor(max_workers=3) as pool:
            iterator=iter(targets);queue=[]
            for _ in range(3):
                target=next(iterator,None)
                if target:queue.append((target,pool.submit(cached_candidate,target,plan['cache_database'])))
            while queue:
                if (run/'stop-request.json').exists():
                    job_state,stop_reason='stopped','operator-stop-request';break
                target,future=queue.pop(0)
                item=future.result()
                _,(_,gaps)=V2._current(plan['database'],target)
                if not gaps:
                    result={'status':'completed','reason':'already-no-repairable-gaps','updated_runners':0}
                elif item.get('evidence'):
                    result=apply_verified(run,plan,target,item)
                else:
                    result={'status':'quarantined' if item.get('reason')=='verified-cache-clock-conflict' else 'pending',
                            'reason':item.get('reason'),'cache_failures':item['failures'],'updated_runners':0}
                record(target,result)
                target=next(iterator,None)
                if target:queue.append((target,pool.submit(cached_candidate,target,plan['cache_database'])))
        if not stop_reason and allow_network:
            job_state,stop_reason=asyncio.run(network_pass(run,plan,envelope,targets,journal,record))
        remaining=[row for row in targets if journal.latest.get(row['race_id'],{}).get('status') not in V2.TERMINAL]
        if not job_state:
            job_state='partial_pending_sources' if remaining else 'completed_with_domain_missing' if journal.status_counts.get('domain_missing') else 'completed'
        guard(plan,envelope)
        final=write_status(run,plan_sha,targets,journal,job_state,processed_this_run=processed,
            updated_runners_this_run=updated,remaining_nonterminal_races=len(remaining),stop_reason=stop_reason,
            protected_state_unchanged=True,elapsed_seconds=time.monotonic()-started)
    return final


def execute(run,*,allow_network=False):
    """Exactly one owned acquisition child; verify only after actual exit."""
    import os
    from run_last3f_gap_network_once import run_process
    run=Path(run).resolve();plan,plan_sha,targets,envelope=load(run)
    output=Path(envelope['repair_output']);output.mkdir(exist_ok=False)
    write(output/'start_receipt.json',{'schema':SCHEMA,'started_at_utc':now(),'pid':os.getpid(),
        'run_dir':str(run),'automatic_retries':0,'network_requested':allow_network})
    write(run/'execution.claim.json',{'started_at_utc':now(),'pid':os.getpid(),'single_attempt':True})
    before=V2.preservation_receipt()
    command=[str(ROOT/'python-api/.venv/Scripts/python.exe'),'-X','utf8',str(Path(__file__).resolve()),
             '--run-dir',str(run),'--acquire']
    if allow_network:command.append('--allow-network')
    child=run_process(command,cwd=ROOT,stdout_path=output/'acquisition.stdout.log',
                      stderr_path=output/'acquisition.stderr.log')
    write(output/'child_exit_receipt.json',child)
    if child.get('confirmed_exited') is not True:
        raise RuntimeError('new-writer-not-confirmed-exited-no-audit')
    final=read(run/'status.json')
    job_state=final.get('status','unconfirmed')
    V2.atomic_status(output/'progress.json',{'schema':SCHEMA,'status':'auditing',
        'acquisition_outcome':job_state,'updated_at_utc':now()})
    # Independent read-only auditor, never promotes the stopped original run.
    from audit_last3f_resume_v3 import audit_resume
    audited=audit_resume(OLD_RUN,OLD_OUTPUT,run,output)
    after=V2.preservation_receipt();guard(plan,envelope)
    result={'schema':'last3f-network-resume-completion-v3','finished_at_utc':now(),
        'original_plan_sha256':OLD_PLAN_SHA,'old_completion_sha256':envelope['old_completion_sha256'],
        'new_plan_sha256':plan_sha,'continuation_plan_sha256':sha(run/'continuation_plan.json'),
        'execution_outcome':job_state,'automatic_retries':0,'child':child,'old_status_preserved':'stopped',
        'child_final_status':final,'protected_state_unchanged':V2.protected_projection(before)==V2.protected_projection(after),
        'wrapper_and_verification_code_unchanged':True,'before_protected':before,'after_protected':after,**audited}
    result['schema']='last3f-network-resume-completion-v3'
    result['safety_verification_pass']=all(result.get(key) is True for key in (
        'source_integrity_pass','journal_audit_pass','coverage_pass','protected_state_unchanged',
        'verification_source_epoch_stable','source_files_stable_after_journal_audit','wrapper_and_verification_code_unchanged'))
    coverage=read(result['combined_coverage_audit'])
    if coverage.get('coverage_pass') is True and child['exit_code']==0 and job_state not in {'stopped','paused','blocked','failed','interrupted'}:
        states=coverage['race_status_counts']
        result['execution_outcome']='partial_pending_sources' if states.get('partial') else 'completed_with_domain_missing' if states.get('domain_missing') else 'completed'
    write(output/'completion_receipt.json',result)
    V2.atomic_status(output/'progress.json',{'schema':SCHEMA,'status':result['execution_outcome'],'updated_at_utc':now(),
        'safety_verification_pass':result['safety_verification_pass'],'coverage_pass':coverage.get('coverage_pass',False)})
    print(json.dumps({'completion_receipt':str(output/'completion_receipt.json'),
        'outcome':result['execution_outcome'],'safety_verification_pass':result['safety_verification_pass']},ensure_ascii=False),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--network-probe',type=Path)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--register',action='store_true');group.add_argument('--execute',action='store_true')
    group.add_argument('--acquire',action='store_true')
    parser.add_argument('--allow-network',action='store_true')
    args=parser.parse_args()
    if args.register:
        if not args.output_dir:parser.error('--output-dir required for registration')
        register(args.run_dir,args.output_dir,network_probe=args.network_probe)
    elif args.acquire:acquire(args.run_dir,allow_network=args.allow_network)
    else:
        try:execute(args.run_dir,allow_network=args.allow_network)
        except BaseException as error:
            destination=Path(read(args.run_dir/'continuation_plan.json')['repair_output'])
            if destination.is_dir():
                V2.independent_failure_receipt(destination,{'schema':SCHEMA,'failed_at_utc':now(),
                    'error':type(error).__name__+': '+str(error),'automatic_retry':False})
            raise


if __name__=='__main__':
    main()

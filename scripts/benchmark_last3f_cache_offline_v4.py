"""Bounded offline parser comparison; never changes a frozen repair pipeline."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor,ThreadPoolExecutor
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'python-api')]
CACHE=ROOT/'keiba/data/fetch_cache.db'
OLD=ROOT/'reports/generated/data-quality/feature-gap/20261017-last3f-v2'


def parse_only(target):
    from repair_last3f_gaps_v3 import cached_candidate
    item=cached_candidate(target,CACHE)
    if not item.get('evidence'):
        return {'race_id':target['race_id'],'reason':item.get('reason'),'failures':item.get('failures')}
    evidence=item['evidence']
    return {'race_id':target['race_id'],'race_date':evidence['race_date'],
        'source_url':evidence['source']['source_url'],'raw_sha256':evidence['source']['raw_body_sha256'],
        'observed_at_utc':evidence['source']['observed_at_utc'],
        'rows':[[row['horse_number'],row['horse_id'],row['horse_name'],row['last3f_state'],row['last3f_seconds']]
                for row in evidence['rows']]}


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


def run(output,samples):
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'
    import psutil
    from repair_last3f_gaps_v3 import V2
    V2.lower_priority()
    if psutil.virtual_memory().available<4*1024**3:raise MemoryError('offline-benchmark-requires-four-gib-reserve')
    output=Path(output).resolve()
    if output.parent!=(ROOT/'outputs/feature-research-20261017-v4').resolve() or output.exists():
        raise ValueError('new-offline-benchmark-output-required')
    output.parent.mkdir(parents=True,exist_ok=True)
    targets=[json.loads(line) for line in (OLD/'targets.jsonl').read_text(encoding='utf-8').splitlines()]
    with closing(sqlite3.connect(CACHE.as_uri()+'?mode=ro',uri=True,timeout=3)) as cache:
        cache.execute('PRAGMA query_only=ON')
        present={row[0] for row in cache.execute("SELECT normalized_url FROM http_cache WHERE normalized_url LIKE 'https://db.sp.netkeiba.com/race/%' AND status=200")}
    candidates=[row for row in targets if f"https://db.sp.netkeiba.com/race/{row['race_id']}/" in present]
    count=min(samples,len(candidates))
    chosen=[candidates[int(index*len(candidates)/count)] for index in range(count)]
    records=[]
    for kind in ('serial','threads3','processes3'):
        started=time.perf_counter()
        if kind=='serial':items=[parse_only(target) for target in chosen]
        else:
            kwargs={'max_workers':3}
            factory=ThreadPoolExecutor if kind=='threads3' else ProcessPoolExecutor
            if kind=='processes3':kwargs['mp_context']=multiprocessing.get_context('spawn')
            with factory(**kwargs) as pool:items=list(pool.map(parse_only,chosen))
        seconds=time.perf_counter()-started
        records.append({'mode':kind,'seconds_including_worker_start':seconds,'races_per_second':count/seconds,
            'output_sha256':hashlib.sha256(canonical(items)).hexdigest(),'parsed_races':sum('rows' in row for row in items),
            'runner_rows':sum(len(row.get('rows',[])) for row in items),
            'numeric_clocks':sum(cell[3]=='valid' for row in items for cell in row.get('rows',[]))})
    equal=len({row['output_sha256'] for row in records})==1
    receipt={'schema':'offline-last3f-cache-speed-v4','observed_at_utc':datetime.now(timezone.utc).isoformat(),
        'sample_count':count,'sampling':'evenly spaced original target order among status200 mobile-cache URLs; no outcome selection',
        'target_ids':[row['race_id'] for row in chosen],'results':records,'outputs_exactly_equal':equal,
        'network_requests':0,'source_database_writes':False,'frozen_files_changed':False,
        'existing_processes_restarted':False,'benchmark_is_not_whole_pipeline_speedup':True,
        'timing_caveat':'One small sequential comparison during background auditing; filesystem warming/order and startup affect results.',
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with output.open('x',encoding='utf-8') as stream:json.dump(receipt,stream,ensure_ascii=False,indent=2)
    print(json.dumps({key:receipt[key] for key in ('sample_count','results','outputs_exactly_equal','network_requests')},ensure_ascii=False))
    if not equal:raise ValueError('offline-parser-result-mismatch-no-adoption')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--samples',type=int,default=64)
    args=parser.parse_args()
    if not 1<=args.samples<=128:parser.error('bounded sample count must be1..128')
    run(args.output,args.samples)

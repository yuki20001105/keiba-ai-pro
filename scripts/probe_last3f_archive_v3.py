"""One preselected, governed public archive validation; no repair or fallback."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'python-api')]


async def main(output):
    import aiohttp
    import psutil
    from last3f_http_transport_v3 import Last3FTransportV3,TransportHalt
    from market_edge.compute import CollectorMonitor
    from scraping.constants import get_random_headers
    from scraping.fetch_pipeline import fetch_context
    import repair_last3f_gaps_v2 as v2
    for pid,epoch in ((40136,1792223432.381708),(6676,1792223432.4030952)):
        try:
            if abs(psutil.Process(pid).create_time()-epoch)<.01:raise RuntimeError('original-writer-not-exited')
        except psutil.NoSuchProcess:pass
    state=CollectorMonitor(v2.TRACK_PATH).snapshot();instant=time.time()
    if not state.get('healthy') or state.get('capturing') or any(
        row['start']-210<=instant<=row['end']+60 for row in state.get('capture_windows',[])):
        raise RuntimeError('protected-forward-capture-or-unhealthy')
    original=ROOT/'reports/generated/data-quality/feature-gap/20261017-last3f-v2'
    plan,plan_sha,targets=v2.load_plan(original)
    target=next(row for row in targets if row['race_date'].startswith('2016'))
    output=Path(output).resolve()
    if output.parent!=(ROOT/'reports/generated/data-quality/http400-diagnostic').resolve() or output.exists():
        raise ValueError('new-isolated-output-required')
    output.mkdir(parents=True)
    before=v2.preservation_receipt()
    receipt={'schema':'last3f-archive-route-probe-v3','source_policy':'archive-mobile','decision':'FAIL',
        'maximum_requests':1,'original_plan_sha256':plan_sha,'race_id':target['race_id'],
        'source_db_writes':False,'automatic_retry':False,'fallback_after_error':False,
        'authentication_or_cookies':False,'before_protection':before}
    with fetch_context(job_id='last3f-archive-single-probe-v3') as metrics:
        client=Last3FTransportV3(output/'http_diagnostics',source_policy='archive-mobile')
        async with aiohttp.ClientSession(headers=get_random_headers(),cookie_jar=aiohttp.DummyCookieJar(),
                                        timeout=aiohttp.ClientTimeout(total=30)) as session:
            try:
                result=await client.fetch(session,target)
                numeric=sum(row['last3f_state']=='valid' for row in result.evidence['rows'])
                receipt.update(decision='PASS' if numeric else 'FAIL',http_status=result.response.status,
                    source_url=result.evidence['source']['source_url'],numeric_runners=numeric,
                    runner_count=result.evidence['runner_count'],evidence=result.evidence,
                    transport_receipt=result.receipt,observed_at_utc=result.observed_at_utc)
            except TransportHalt as error:
                receipt.update(reason=str(error),transport_receipt=getattr(error,'receipt',{}))
        receipt['metrics']=dict(metrics)
    after=v2.preservation_receipt()
    receipt.update(after_protection=after,protected_state_unchanged=v2.protected_projection(before)==v2.protected_projection(after))
    v2.exclusive_json(output/'receipt.json',receipt)
    print(json.dumps({key:receipt.get(key) for key in ('decision','race_id','source_url','http_status',
        'runner_count','numeric_runners','reason','protected_state_unchanged','metrics')},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--allow-network',action='store_true',required=True)
    args=parser.parse_args()
    asyncio.run(main(args.output))

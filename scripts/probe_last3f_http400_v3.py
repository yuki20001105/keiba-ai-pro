"""One governed public response diagnostic; never updates source data."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'python-api')]


async def probe(output):
    import aiohttp
    import psutil
    from bs4 import BeautifulSoup
    from evaluate_db_shared_venues import preservation_receipt
    from market_edge.compute import CollectorMonitor
    from scraping.constants import get_random_headers
    from scraping.fetch_pipeline import decode_html_body, fetch_bytes, fetch_context
    for pid, epoch in ((40136, 1792223432.381708), (6676, 1792223432.4030952)):
        try:
            if abs(psutil.Process(pid).create_time() - epoch) < .01:
                raise RuntimeError('original-repair-writer-still-running')
        except psutil.NoSuchProcess:
            pass
    before = preservation_receipt()
    state = CollectorMonitor(ROOT / 'reports/generated/forward-runtime/h04-live-20260920-v2').snapshot()
    instant = time.time()
    if (not state.get('healthy') or state.get('capturing') or any(
        row['start'] - 210 <= instant <= row['end'] + 60 for row in state.get('capture_windows', []))):
        raise RuntimeError('forward-collector-not-safe-for-one-diagnostic')
    output = Path(output).resolve()
    parent = (ROOT / 'reports/generated/data-quality/http400-diagnostic').resolve()
    if output.parent != parent or output.exists() or output.is_symlink():
        raise ValueError('new-fixed-diagnostic-directory-required')
    output.mkdir(parents=True)
    rid = '202408060412'
    url = 'https://race.netkeiba.com/race/result.html?race_id=' + rid
    with fetch_context(job_id='last3f-http400-single-diagnostic-v3') as metrics:
        async with aiohttp.ClientSession(headers=get_random_headers(), cookie_jar=aiohttp.DummyCookieJar(),
                                        timeout=aiohttp.ClientTimeout(total=25)) as session:
            response = await fetch_bytes(session, url, use_cache=False, force_refresh=True, max_retries=0,
                retry_statuses=set(), allow_redirects=False, min_interval_sec=3.0, max_body_bytes=3_000_000,
                total_timeout_sec=25)
    observed = datetime.now(timezone.utc).isoformat()
    (output / 'response.bin').write_bytes(response.body)
    html = decode_html_body(response.body)
    soup = BeautifulSoup(html, 'lxml')
    allowed = {'content-type', 'content-length', 'date', 'server', 'retry-after'}
    receipt = {'schema':'last3f-http400-single-diagnostic-v3', 'observed_at_utc':observed,
        'source_url':url, 'http_status':response.status, 'transport_source':response.source,
        'error':response.error, 'attempts':response.attempts, 'maximum_requests':1,
        'body_path':'response.bin', 'body_bytes':len(response.body),
        'body_sha256':hashlib.sha256(response.body).hexdigest(),
        'response_headers':{k:v for k,v in response.response_headers.items() if k.lower() in allowed},
        'title':soup.title.get_text(' ', strip=True) if soup.title else None,
        'visible_message':soup.get_text(' ', strip=True)[:1800], 'metrics':dict(metrics),
        'source_db_writes':False,'authentication_or_cookies':False, 'automatic_retries':False,
        'historical_prestart_capture_proven':False,
        'protection_before':before, 'protection_after':preservation_receipt(),
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if {k:v for k,v in receipt['protection_before'].items() if k!='forward_processes'} != {
        k:v for k,v in receipt['protection_after'].items() if k!='forward_processes'}:
        raise RuntimeError('protected-state-changed-during-one-probe')
    with (output / 'receipt.json').open('x',encoding='utf-8') as stream:
        json.dump(receipt,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in receipt.items() if not k.startswith('protection_')},ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--allow-network',action='store_true',required=True)
    arguments=parser.parse_args()
    asyncio.run(probe(arguments.output))

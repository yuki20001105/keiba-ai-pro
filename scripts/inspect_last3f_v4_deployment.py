"""Read-only deployment checks; creates a new receipt, never a completion claim."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / part) for part in ('scripts', 'python-api', 'keiba')]


def main(output):
    import psutil
    from audit_last3f_resume_v3 import shared_bytes
    from run_repaired_context_research import guard
    from run_repaired_context_research_v3 import assert_fixed_plan
    from continue_last3f_study_v4_once import OLD_STUDY, OLD_OUTPUT, REPAIR, PARENT, OWN_CODE

    def read(path):
        return json.loads(shared_bytes(Path(path)).decode('utf-8-sig'))

    def sha(path):
        return hashlib.sha256(shared_bytes(Path(path))).hexdigest()

    original = read(OLD_STUDY / 'registered_plan.json')
    guard(original)
    start = read(PARENT / '20261017-v4/start_receipt.json')
    for relative, digest in start['code_sha256'].items():
        if sha(ROOT / relative) != digest:
            raise ValueError('deployment-code-pin-changed:' + relative)
    owner = psutil.Process(start['pid'])
    if abs(owner.create_time() - start['created_at_epoch']) >= .01:
        raise ValueError('deployment-coordinator-pid-reused')
    old = read(OLD_OUTPUT / 'completion_receipt.json')
    if old.get('execution_outcome') != 'stopped' or not all(old.get(key) is True for key in (
        'safety_verification_pass', 'source_integrity_pass', 'journal_audit_pass',
        'protected_state_unchanged', 'verification_source_epoch_stable',
        'wrapper_and_verification_code_unchanged',
    )):
        raise ValueError('old-stopped-run-independent-safety-audit-not-pass')
    plan, envelope = read(REPAIR / 'plan.json'), read(REPAIR / 'continuation_plan.json')
    if (plan['execution']['cache_parse_processes'] != 3
            or plan['execution']['database_writers'] != 1
            or plan['execution']['minimum_interval_seconds'] != 3.0
            or plan['execution']['consecutive_http400_pause_threshold'] != 1
            or plan['execution']['audit_io_optimization_enabled'] is not False
            or envelope['new_plan_sha256'] != sha(REPAIR / 'plan.json')):
        raise ValueError('v4-execution-contract-mismatch')
    # The prospective study continues the existing fixed settings. A research
    # plan is not registered, and no fit is started, by this read-only inspector.
    assert_fixed_plan(original, original)
    stage = read(PARENT / '20261017-v4/status.json')
    progress = read(REPAIR / 'status.json')
    if (PARENT / '20261017-v4/failure.json').exists():
        raise ValueError('deployment-coordinator-failed')
    children = []
    for process in owner.children(recursive=True):
        try:
            children.append({'pid': process.pid, 'created_at_epoch': process.create_time(),
                'command': process.cmdline()})
        except psutil.NoSuchProcess:
            continue
    parser_workers = [row for row in children if '--multiprocessing-fork' in row['command']]
    if progress['status'] == 'running' and progress['phase'] == 'cache' and len(parser_workers) != 3:
        raise ValueError('cache-parser-worker-count-not-three')
    test_results = []
    out_root = ROOT / 'outputs/feature-research-20261017-v4'
    for filename in ('v4-core-regression.junit.xml', 'v4-frozen-core.junit.xml', 'v4-integration-final.junit.xml'):
        path = out_root / filename
        suites = list(ET.fromstring(shared_bytes(path)).iter('testsuite'))
        counts = {name: sum(int(suite.get(name, 0)) for suite in suites)
            for name in ('tests', 'errors', 'failures', 'skipped')}
        if counts['errors'] or counts['failures']:
            raise ValueError('deployment-tests-not-pass:' + filename)
        test_results.append({'path': str(path), 'sha256': sha(path), **counts})
    benchmark = out_root / 'cache-process-parity-64-repeat-v1.json'
    audit_benchmark = out_root / 'audit-benchmark-20261017T105412Z-fd735332/benchmark-result.json'
    receipt = {
        'schema': 'last3f-v4-deployment-observation-v1',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'deployment_checks_pass': True, 'is_completion_receipt': False,
        'main_stage': stage, 'repair_progress_snapshot': progress,
        'start_receipt_sha256': sha(PARENT / '20261017-v4/start_receipt.json'),
        'code_pins_checked': len(start['code_sha256']),
        'old_execution_outcome_preserved': 'stopped', 'old_independent_safety_audit_pass': True,
        'active_model_h04_services_protected': True, 'parser_workers': parser_workers,
        'repair_plan_sha256': sha(REPAIR / 'plan.json'),
        'tests': test_results,
        'cache_benchmark': {'path': str(benchmark), 'sha256': sha(benchmark)},
        'audit_benchmark': {'path': str(audit_benchmark), 'sha256': sha(audit_benchmark)},
        'adopted': ['verified-cache-first', 'three-read-only-parser-processes',
            'one-journalled-missing-only-writer', 'one-provider-request-minimum-three-seconds'],
        'not_adopted': {'experimental_unique_io_auditor': 'elapsed-time-regressed-in-bounded-comparison'},
        'whole_pipeline_speedup_measured': False, 'new_55_jobs_completed': False,
        'provider_error_free_guarantee': False, 'commit_push_or_model_switch': False,
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'receipt': str(output), 'deployment_checks_pass': True,
        'stage': stage['stage'], 'progress': progress}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args().output)

"""One-command local experiment queue. Candidates never enter active models."""
from __future__ import annotations
import argparse
import contextlib
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
import uuid
from typing import IO
import psutil
from experiments.common import REPO_ROOT, active_pointer_hash, file_sha256, read_json, source_manifest, write_json
from experiments.resources import capability_probe, resource_snapshot


def store_for(root: Path):
    from experiments.runtime import ExperimentStore
    store = ExperimentStore(root / 'experiment.sqlite3')
    store.initialize()
    return store


def verify_contract(root: Path, contract: dict, *, verify_data: bool = True) -> None:
    if contract['source_manifest'] != source_manifest():
        raise RuntimeError('experiment-source-changed')
    if contract['active_pointer_sha256'] != active_pointer_hash():
        raise RuntimeError('active-model-changed-during-experiment')
    if file_sha256(root / 'dataset/dataset_manifest.json') != contract['dataset_manifest_sha256']:
        raise RuntimeError('experiment-dataset-manifest-hash-mismatch')
    if verify_data and file_sha256(root / 'dataset/dataset.parquet') != contract['dataset_sha256']:
        raise RuntimeError('experiment-dataset-hash-mismatch')


def prepare(root: Path, source: Path) -> dict:
    from training.local_retrain import create_sqlite_snapshot
    from experiments.dataset import build_dataset
    root.mkdir(parents=True, exist_ok=True)
    snapshot_file = root / 'snapshot.json'
    if snapshot_file.exists():
        snapshot = read_json(snapshot_file)
        if file_sha256(Path(snapshot['path'])) != snapshot['sha256']:
            raise RuntimeError('snapshot-hash-mismatch')
    else:
        created = create_sqlite_snapshot(source.resolve(),(root/'snapshots').resolve())
        snapshot = {'path':str(created.path),'sha256':created.sha256,'size_bytes':created.size_bytes,
                    'active_pointer_sha256':active_pointer_hash()}
        write_json(snapshot_file,snapshot)
    if (root / 'dataset/dataset_manifest.json').exists():
        manifest = read_json(root / 'dataset/dataset_manifest.json')
        if manifest['dataset_sha256'] != file_sha256(root/'dataset/dataset.parquet'):
            raise RuntimeError('existing-dataset-mismatch')
        return manifest
    print('Building safe shared dataset from immutable snapshot',flush=True)
    return build_dataset(Path(snapshot['path']), root/'dataset')


def worker(root: Path, device: str, *, trial_runner=None) -> int:
    if trial_runner is None:
        from experiments.training import run_trial
        trial_runner = run_trial
    store = store_for(root)
    state = store.snapshot(root.name)
    contract = state['contract']
    verify_contract(root, contract)
    identity = f'{os.getpid()}-{psutil.Process().create_time()}-{uuid.uuid4().hex[:8]}'
    lease = store.claim(identity,device,ttl_seconds=120)
    if lease is None:
        return 0
    output = root/'trials'/lease.trial_id
    if output.exists():
        store.fail(lease,'trial-output-already-exists')
        return 1
    lock = threading.RLock()
    stopped = threading.Event()
    errors = []
    peak_mb = 0.0
    start = time.perf_counter()
    process = psutil.Process()

    def heartbeat():
        nonlocal lease, peak_mb
        while not stopped.wait(5):
            try:
                peak_mb = max(peak_mb,process.memory_info().rss/2**20)
                with lock:
                    lease = store.heartbeat(lease,ttl_seconds=120)
            except Exception as exc:
                errors.append(exc)
                return

    thread = threading.Thread(target=heartbeat,daemon=True)
    output_created = False

    def progress(stage, pct):
        if errors: raise RuntimeError('experiment-lease-or-cancellation-lost') from errors[0]
        write_json(output/'progress.json',{'stage':stage,'pct':pct,'elapsed_seconds':time.perf_counter()-start})
        with lock:
            if hasattr(store,'update_progress'):
                store.update_progress(lease,stage,pct)
        print(json.dumps({'trial':lease.trial_id,'stage':stage,'pct':pct},ensure_ascii=False),flush=True)

    try:
        output.mkdir(parents=True)
        output_created = True
        write_json(output/'process.json',{'pid':os.getpid(),'created_at':process.create_time(),'worker_id':identity})
        thread.start()
        # The trainer requires a fresh directory; process/progress metadata lives
        # one level above so it cannot invalidate or overwrite trainer artifacts.
        result = trial_runner(root/'dataset',output/'artifacts',lease.payload,progress)
        stopped.set(); thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError('experiment-heartbeat-thread-did-not-stop')
        if errors: raise RuntimeError('experiment-lease-or-cancellation-lost') from errors[0]
        verify_contract(root,contract,verify_data=False)
        result['resource_usage'] = {'wall_seconds':time.perf_counter()-start,
                                  'peak_sampled_rss_mb':max(peak_mb,process.memory_info().rss/2**20),
                                  'device':device,'num_threads':lease.payload['num_threads']}
        result['experiment_contract_sha256'] = state['contract_sha256']
        write_json(output/'result.json',result)
        artifacts = {str(p.relative_to(output)):file_sha256(p) for p in output.rglob('*') if p.is_file() and p.name not in ('progress.json',)}
        write_json(output/'artifact_manifest.json',artifacts)
        with lock:
            store.complete(lease,result)
        return 0
    except Exception as exc:
        stopped.set()
        if thread.is_alive(): thread.join(timeout=10)
        if output_created:
            with contextlib.suppress(Exception):
                write_json(output/'failure.json',{'error':str(exc),'type':type(exc).__name__,'traceback':traceback.format_exc()})
        with contextlib.suppress(Exception):
            store.fail(lease,str(exc)[:1000])
        return 1


@dataclass
class _OwnedChild:
    process: subprocess.Popen
    log: IO[bytes]
    device: str
    created_at: float | None


@dataclass
class _VerifiedWorker:
    process: psutil.Process
    created_at: float
    worker_id: str


def _verified_descendant_workers(store, root: Path, children: list[_OwnedChild]) -> list[_VerifiedWorker]:
    """Bind Windows venv descendants while their owned launcher is still alive.

    A process.json alone is never authority to terminate a PID. In particular,
    after a launcher exits we do not try to discover or kill its orphan workers.
    """
    metadata = {}
    for row in store.snapshot(root.name)['trials']:
        if not row.get('worker_id'):
            continue
        try:
            value = read_json(root/'trials'/row['trial_id']/'process.json')
            if (type(value.get('pid')) is int and value['pid'] > 0
                    and value.get('worker_id') == row['worker_id']
                    and isinstance(value.get('created_at'), (int, float))
                    and not isinstance(value['created_at'], bool)
                    and math.isfinite(value['created_at'])):
                metadata[(value['pid'], value['created_at'])] = row['worker_id']
        except (OSError, ValueError, TypeError):
            continue
    verified = {}
    for owned in children:
        if owned.created_at is None or owned.process.poll() is not None:
            continue
        try:
            parent = psutil.Process(owned.process.pid)
            if parent.create_time() != owned.created_at or not parent.is_running():
                continue
            descendants = parent.children(recursive=True)
            # If the launcher died during discovery, ancestry is not accepted.
            if owned.process.poll() is not None or not parent.is_running():
                continue
            for descendant in descendants:
                try:
                    identity = (descendant.pid, descendant.create_time())
                    worker_id = metadata.get(identity)
                    if worker_id is not None and descendant.is_running():
                        verified[identity] = _VerifiedWorker(descendant, identity[1], worker_id)
                except psutil.Error:
                    continue
        except psutil.Error:
            continue
    return list(verified.values())


def _worker_stopped(worker: _VerifiedWorker) -> bool:
    try:
        return not worker.process.is_running()
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False


def _acknowledge_terminated_children(store, root: Path, children: list[_OwnedChild],
                                     workers: list[_VerifiedWorker] | None = None) -> None:
    """A ledger row is not proof of termination: bind it to our stopped Popen."""
    from experiments.runtime import TrialLease
    stopped = {(child.process.pid, child.created_at): None for child in children
               if child.process.poll() is not None and child.created_at is not None}
    stopped.update({(worker.process.pid, worker.created_at): worker.worker_id for worker in workers or []
                    if _worker_stopped(worker)})
    if not stopped:
        return
    for row in store.snapshot(root.name)['trials']:
        if row['status'] != 'cancelling':
            continue
        try:
            process = read_json(root/'trials'/row['trial_id']/'process.json')
            identity = (process.get('pid'), process.get('created_at'))
            if (identity not in stopped or process.get('worker_id') != row['worker_id']
                    or stopped[identity] not in (None, row['worker_id'])):
                continue
            lease = TrialLease(**{key: row[key] for key in (
                'experiment_id', 'trial_id', 'worker_id', 'device', 'memory_mb',
                'payload', 'fencing_token', 'record_version', 'lease_expires_at')})
            store.acknowledge_cancel(lease)
        except Exception:
            # Missing/mismatched metadata or an expired lease stays fail-closed.
            continue


def _reap_owned_children(store, root: Path, children: list[_OwnedChild], *, grace_seconds: float) -> list[int]:
    """Reap only handles created by this batch, never discover/kill service PIDs."""
    deadline = time.monotonic() + grace_seconds
    try:
        while any(child.process.poll() is None for child in children) and time.monotonic() < deadline:
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        # Capture verified descendants BEFORE killing a venv launcher. Killing
        # the launcher alone can otherwise leave the actual Python worker alive.
        workers = _verified_descendant_workers(store, root, children)
        for worker in workers:
            with contextlib.suppress(psutil.Error):
                worker.process.terminate()
        for worker in workers:
            try:
                worker.process.wait(timeout=5)
            except psutil.TimeoutExpired:
                with contextlib.suppress(psutil.Error):
                    worker.process.kill()
                with contextlib.suppress(psutil.Error):
                    worker.process.wait(timeout=5)
            except psutil.Error:
                pass
        for child in children:
            if child.process.poll() is None:
                with contextlib.suppress(OSError):
                    child.process.terminate()
        for child in children:
            try:
                child.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    child.process.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    child.process.wait(timeout=5)
        with contextlib.suppress(Exception):
            _acknowledge_terminated_children(store, root, children, workers)
        return sorted({child.process.pid for child in children if child.process.poll() is None}
                      | {worker.process.pid for worker in workers if not _worker_stopped(worker)})
    finally:
        for child in children:
            child.log.close()


def batch(root: Path, payloads: list[dict], *, resource_wait_timeout_seconds: float = 300,
          cancel_grace_seconds: float = 15, worker_script: Path | None = None) -> dict[str, dict]:
    worker_script = (worker_script or REPO_ROOT/'scripts/run_model_experiment.py').resolve()
    allowed_scripts = {(REPO_ROOT/'scripts/run_model_experiment.py').resolve(),
                       (REPO_ROOT/'scripts/run_model_search.py').resolve()}
    if worker_script not in allowed_scripts:
        raise ValueError('unsupported-experiment-worker-script')
    for name, value in [('resource_wait_timeout_seconds', resource_wait_timeout_seconds),
                        ('cancel_grace_seconds', cancel_grace_seconds)]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f'{name} must be a finite nonnegative number')
    store = store_for(root)
    contract = store.snapshot(root.name)['contract']
    verify_contract(root,contract)
    existing = {row['trial_id'] for row in store.snapshot(root.name)['trials']}
    for index, payload in enumerate(payloads):
        if payload['id'] in existing: continue
        device = 'gpu' if contract['capacity']['gpu'] and index % 2 else 'cpu'
        payload = {**payload,'device':device,'num_threads':4,'seed':payload.get('seed',42),
                   'experiment_contract_sha256':store.snapshot(root.name)['contract_sha256']}
        if device == 'gpu':
            payload.update({k:contract['gpu_opencl'][k] for k in ('gpu_platform_id','gpu_device_id')})
        store.enqueue(root.name,payload['id'],payload,device,memory_mb=contract['estimated_worker_memory_mb'])
    wanted = {p['id'] for p in payloads}
    children: list[_OwnedChild] = []
    owned_children: list[_OwnedChild] = []
    root_logs = root/'logs'; root_logs.mkdir(exist_ok=True)
    last_message = 0
    resource_wait_since: float | None = None
    try:
        while True:
            snapshot = store.snapshot(root.name)
            selected = [r for r in snapshot['trials'] if r['trial_id'] in wanted]
            if snapshot['status'] in ('blocked', 'cancelling', 'cancelled') or any(
                    r['status'] in ('failed','unknown','cancelled','cancelling') for r in selected):
                raise RuntimeError('experiment-trial-failed-or-cancelled; inspect status/logs before resuming')
            if len(selected) != len(wanted):
                raise RuntimeError('experiment-trial-ledger-incomplete')
            if all(r['status']=='completed' for r in selected):
                break
            for owned in list(children):
                exit_code = owned.process.poll()
                if exit_code is not None:
                    owned.log.close()
                    children.remove(owned)
                    if exit_code != 0:
                        raise RuntimeError(f'experiment-worker-exited: pid={owned.process.pid}, code={exit_code}')
            resources = resource_snapshot()
            for device, capacity in contract['capacity'].items():
                queued = sum(r['status']=='queued' and r['device']==device for r in selected)
                owned_running = sum(child.device==device for child in children)
                ledger_running = sum(r['status']=='running' and r['device']==device for r in snapshot['trials'])
                occupied = max(owned_running, ledger_running)
                if queued and occupied < capacity and resources['available_ram_mb'] >= contract['reserve_ram_mb'] + contract['estimated_worker_memory_mb']:
                    if device=='gpu' and (not resources['gpu'] or resources['gpu']['available_mb'] < 4096): continue
                    log = (root_logs/f'{device}-{time.time_ns()}.log').open('wb')
                    env = dict(os.environ,PYTHONUTF8='1',OMP_NUM_THREADS='4',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
                    cmd = [sys.executable,str(worker_script),'worker','--root',str(root),'--device',device]
                    try:
                        child = subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,cwd=REPO_ROOT,
                                                 creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
                    except BaseException:
                        log.close()
                        raise
                    owned = _OwnedChild(child, log, device, None)
                    children.append(owned)
                    owned_children.append(owned)
                    with contextlib.suppress(psutil.Error):
                        owned.created_at = psutil.Process(child.pid).create_time()
                    # Reserve for processes still loading their matrices.
                    resources['available_ram_mb'] -= contract['estimated_worker_memory_mb']
            # A running trial may legitimately need hours. The deadline covers
            # resource starvation with no worker making progress, not training.
            waiting = any(r['status']=='queued' for r in selected) and not children and not any(
                r['status']=='running' for r in snapshot['trials'])
            if waiting:
                if resource_wait_since is None:
                    resource_wait_since = time.monotonic()
                if time.monotonic() - resource_wait_since >= resource_wait_timeout_seconds:
                    raise RuntimeError(f'experiment-resources-unavailable: waited {resource_wait_timeout_seconds:g}s; '
                                       f'available_ram_mb={resources["available_ram_mb"]}; gpu={resources["gpu"]}')
            else:
                resource_wait_since = None
            if time.time()-last_message > 15:
                print(json.dumps({'phase':'waiting_for_resources' if waiting else 'training',
                                  'counts':{state:sum(r['status']==state for r in selected) for state in sorted({r['status'] for r in selected})},
                                  'running':[{k:r.get(k) for k in ('trial_id','device','progress_stage','progress_pct')} for r in selected if r['status']=='running'],
                                  'available_ram_mb':resources['available_ram_mb']},ensure_ascii=False),flush=True)
                last_message = time.time()
            time.sleep(2)
    except BaseException:
        with contextlib.suppress(Exception):
            store.cancel(root.name)
        remaining = _reap_owned_children(store, root, owned_children, grace_seconds=cancel_grace_seconds)
        if remaining:
            print(json.dumps({'error':'owned-workers-still-running', 'pids':remaining}),file=sys.stderr,flush=True)
        raise
    else:
        remaining = _reap_owned_children(store, root, owned_children, grace_seconds=30)
        if remaining:
            raise RuntimeError(f'experiment-workers-did-not-exit: {remaining}')
    return {row['trial_id']:row['result'] for row in store.snapshot(root.name)['trials'] if row['trial_id'] in wanted}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','probe','worker','run','status','cancel'])
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--source',type=Path,default=REPO_ROOT/'keiba/data/keiba_ultimate.db')
    parser.add_argument('--device',choices=['cpu','gpu'],default='cpu')
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to((REPO_ROOT/'reports/generated/model-experiments').resolve()):
        parser.error('experiment root must be under reports/generated/model-experiments')
    if args.action=='prepare':
        print(json.dumps(prepare(root,args.source),ensure_ascii=False,default=str))
    elif args.action=='probe':
        result = capability_probe(); write_json(root/'capability.json',result)
        print(json.dumps(result,ensure_ascii=False))
    elif args.action=='worker': return worker(root,args.device)
    elif args.action=='status': print(json.dumps(store_for(root).snapshot(root.name),ensure_ascii=False,default=str))
    elif args.action=='cancel': store_for(root).cancel(root.name)
    elif args.action=='run':
        from experiments.orchestrator import run_experiment
        print(json.dumps(run_experiment(root),ensure_ascii=False,default=str))
    return 0

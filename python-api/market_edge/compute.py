"""Track B process admission; Track A is read-only and always takes priority.

No training is implemented here. Only explicitly supplied, plan-bound Python
research commands are launched. A process is controllable only while descended
from this driver's Popen and carrying its unguessable environment ownership token.
Suspension does not release RAM/VRAM; critical RAM pressure cancels owned work.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import psutil

REPO = Path(__file__).resolve().parents[2]
BASE = REPO / "reports/generated/model-experiments"
UTC = timezone.utc


def _sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _epoch(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone-required")
    return parsed.timestamp()


@dataclass(frozen=True)
class ProtectionLimits:
    cpu_capacity: int = 1
    gpu_capacity: int = 1
    reserve_ram_mb: int = 4096
    min_disk_mb: int = 8192
    max_cpu_percent: float = 80
    max_pagefile_percent: float = 80
    max_disk_mbps: float = 100
    max_paging_mbps: float = 16
    max_gpu_percent: float = 85
    gpu_reserve_mb: int = 1024
    capture_margin_before: float = 120
    capture_margin_after: float = 60
    poll_seconds: float = 1
    clear_samples_before_resume: int = 3

    def __post_init__(self):
        if self.cpu_capacity != 1 or self.gpu_capacity not in (0, 1):
            raise ValueError("initial-capacity-is-at-most-one-CPU-plus-one-GPU")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
               for v in asdict(self).values()):
            raise ValueError("finite-nonnegative-protection-limits-required")
        if self.reserve_ram_mb < 3072 or self.poll_seconds <= 0 or self.poll_seconds > 5:
            raise ValueError("minimum-3GiB-reserve-and-maximum-5s-poll-required")


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    model: str
    device: str
    dataset: str
    fold: str
    command: list[str]
    cwd: str
    result_path: str
    cpu_threads: int = 2
    memory_mb: int = 512
    gpu_memory_mb: int = 1024
    kind: str = "research"

    def validate(self, output_root):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", self.job_id):
            raise ValueError("safe-job-id-required")
        if self.device not in ("cpu", "gpu") or self.kind not in ("research", "benchmark"):
            raise ValueError("unsupported-job-kind-or-device")
        if self.cpu_threads not in (1, 2) or self.memory_mb < 128 or self.gpu_memory_mb < 0:
            raise ValueError("bounded-job-resources-required")
        if not self.model or not self.dataset or not str(self.fold):
            raise ValueError("model-dataset-fold-required")
        allowed = { (REPO / "python-api/.venv/Scripts/python.exe").resolve(),
                    (REPO / ".local-runtime/gpu-validation/.venv/Scripts/python.exe").resolve() }
        if (not isinstance(self.command, list) or len(self.command) < 2
                or not all(isinstance(x, str) and x and "\0" not in x for x in self.command)
                or Path(self.command[0]).resolve() not in allowed):
            raise ValueError("explicit-isolated-Python-command-required")
        # No shell, -c, -m, or arbitrary existing service command is accepted.
        script = Path(self.command[1]).resolve()
        if not script.is_relative_to(REPO / "scripts") or not script.name.startswith("run_market_edge") or script.suffix != ".py":
            raise ValueError("dedicated-market-edge-worker-script-required")
        if Path(self.cwd).resolve() != REPO or not Path(self.result_path).resolve().is_relative_to(Path(output_root).resolve()):
            raise ValueError("research-cwd-and-isolated-result-path-required")
        if self.device == "gpu" and Path(self.command[0]).resolve() != (REPO / ".local-runtime/gpu-validation/.venv/Scripts/python.exe").resolve():
            raise ValueError("GPU-must-use-isolated-environment")


class CollectorMonitor:
    """Bounded SELECT-only reads; source checks every 30s, artifacts once/stat-change."""
    def __init__(self, collector_root, *, clock=time.time):
        self.root = Path(collector_root).resolve()
        self.clock = clock
        self.last_verified = -math.inf
        self.fingerprint = None
        self.protocol_sha256 = None
        self.artifact_stats = {}

    def _verify(self):
        envelope = json.loads((self.root / "protocol.json").read_text(encoding="utf-8"))
        protocol = envelope["protocol"]
        digest = hashlib.sha256(_json(protocol).encode()).hexdigest()
        if digest != envelope["protocol_sha256"] or (self.protocol_sha256 and digest != self.protocol_sha256):
            raise ValueError("collector-protocol-changed")
        if protocol.get("paper_only") is not True or protocol.get("automatic_purchase") is not False:
            raise ValueError("collector-not-frozen-paper-only")
        # Match Runtime.source_manifest's full set, not just its recorded entries:
        # adding a module is itself a frozen-source change even if old hashes match.
        source_paths = []
        for package in ("forward_runtime", "forward_adapters", "experiments", "prospective"):
            source_paths.extend((REPO / "python-api" / package).glob("*.py"))
        source_paths += [REPO / "scripts/run_forward_collector.py"]
        source_paths += [REPO / "python-api" / relative for relative in
                         ("training/eligibility.py", "training/local_retrain.py", "scraping/fetch_pipeline.py")]
        current_sources = {path.relative_to(REPO).as_posix() for path in source_paths}
        if current_sources != set(protocol["source_manifest"]):
            raise ValueError("collector-frozen-source-set-changed")
        for relative, expected in protocol["source_manifest"].items():
            source = (REPO / relative).resolve()
            if not source.is_relative_to(REPO) or _sha(source) != expected:
                raise ValueError("collector-source-changed")
        for expected in protocol["artifact_hashes"].values():
            if not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise ValueError("collector-artifact-hash-invalid")
            source = self.root / "objects" / expected
            stat = source.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            if self.artifact_stats.get(expected) != signature:
                if _sha(source) != expected:
                    raise ValueError("collector-artifact-changed")
                self.artifact_stats[expected] = signature
        analysis = self.root / "analysis-definition.json"
        definition = json.loads(analysis.read_text(encoding="utf-8"))
        if definition.get("protocol_sha256") != digest:
            raise ValueError("collector-analysis-protocol-mismatch")
        evaluation_sha = definition.get("definition", {}).get("evaluation_source_sha256")
        if (not isinstance(evaluation_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", evaluation_sha)
                or _sha(REPO / "python-api/forward_reports/evaluation.py") != evaluation_sha):
            raise ValueError("collector-evaluation-source-changed")
        fingerprint = (digest, _sha(analysis))
        if self.fingerprint and fingerprint != self.fingerprint:
            raise ValueError("collector-analysis-definition-changed")
        self.fingerprint, self.protocol_sha256 = fingerprint, digest
        self.last_verified = self.clock()

    def snapshot(self):
        instant = self.clock()
        result = {"healthy": False, "reasons": [], "observed_at": instant, "capture_windows": []}
        try:
            if instant - self.last_verified >= 30:
                self._verify()
            with sqlite3.connect((self.root / "ledger.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=2) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                owner = dict(db.execute("SELECT * FROM owner WHERE singleton=1").fetchone())
                contract = json.loads(db.execute("SELECT value FROM runtime_meta WHERE key='contract'").fetchone()[0])
                if contract["protocol_sha256"] != self.protocol_sha256:
                    raise ValueError("collector-ledger-protocol-mismatch")
                rows = db.execute("SELECT race_id,post_time,status FROM races ORDER BY post_time").fetchall()
                # Read only small recent control events, never stored feature vectors.
                capturing = [r["race_id"] for r in rows if r["status"] == "capturing"]
            result.update(owner=owner, source_verified=True, protocol_sha256=self.protocol_sha256,
                          capturing=capturing)
            match = re.fullmatch(r"pid-(\d+)-[a-f0-9]+", owner.get("owner_id") or "")
            if not match or owner["stop_requested"] or not owner.get("expires_at") or _epoch(owner["expires_at"]) - instant < 15:
                raise ValueError("collector-owner-stopped-expired-or-unverified")
            process = psutil.Process(int(match[1]))
            command = process.cmdline()
            script = str((REPO / "scripts/run_forward_collector.py").resolve()).casefold()
            if (not process.is_running() or script not in [str(Path(c).resolve()).casefold() for c in command if c.endswith(".py")]
                    or "run" not in command or "--root" not in command
                    or Path(command[command.index("--root") + 1]).resolve() != self.root
                    or process.status() in (psutil.STATUS_STOPPED, psutil.STATUS_ZOMBIE)):
                raise ValueError("collector-process-identity-or-health-unverified")
            result.update(pid=process.pid, process_create_time=process.create_time())
            for row in rows:
                post = _epoch(row["post_time"])
                if post + 120 >= instant:
                    result["capture_windows"].append({"race_id":row["race_id"], "start":post-330, "end":post-300, "status":row["status"]})
            result["healthy"] = True
        except (OSError, ValueError, KeyError, IndexError, TypeError, sqlite3.Error, psutil.Error) as exc:
            result["reasons"].append(str(exc))
        return result


class ResourceMonitor:
    def __init__(self, directory, *, clock=time.monotonic):
        self.directory, self.clock = Path(directory), clock
        self.previous = None
        self.gpu_cache = None
        self.gpu_at = -math.inf
        psutil.cpu_percent(interval=None)  # Prime nonblocking CPU measurement.

    def sample(self, *, gpu_refresh=True):
        tick = self.clock()
        memory, swap, io = psutil.virtual_memory(), psutil.swap_memory(), psutil.disk_io_counters()
        counts = (tick, io.read_bytes + io.write_bytes if io else None, swap.sin + swap.sout)
        elapsed = tick - self.previous[0] if self.previous else None
        disk = ((counts[1]-self.previous[1])/elapsed/2**20 if elapsed and counts[1] is not None and self.previous[1] is not None else None)
        paging = (counts[2]-self.previous[2])/elapsed/2**20 if elapsed else None
        self.previous = counts
        if gpu_refresh and tick-self.gpu_at >= 1:
            try:
                line = subprocess.check_output(["nvidia-smi", "--query-gpu=utilization.gpu,memory.total,memory.used", "--format=csv,noheader,nounits"], text=True, timeout=2,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0).splitlines()[0]
                utilization, total, used = map(float, line.split(","))
                self.gpu_cache = {"utilization_percent":utilization,"total_mb":total,"used_mb":used,"available_mb":total-used}
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                self.gpu_cache = None
            self.gpu_at = tick
        return {"available_ram_mb":memory.available/2**20,"cpu_percent":psutil.cpu_percent(interval=None),
                "pagefile_percent":swap.percent,"pagefile_used_mb":swap.used/2**20,
                "paging_mbps":paging,"paging_counters_supported":sys.platform != "win32",
                "disk_free_mb":psutil.disk_usage(str(self.directory)).free/2**20,"disk_mbps":disk,
                "gpu":self.gpu_cache,"sampled_at":time.time()}


def protection_reasons(resources, collector, limits, *, now=None):
    now = time.time() if now is None else now
    reasons = list(collector.get("reasons", [])) if not collector.get("healthy") else []
    if not collector.get("healthy"):
        reasons.append("collector-unhealthy")
    if collector.get("capturing"):
        reasons.append("collector-capturing")
    if any(w["start"]-limits.capture_margin_before <= now <= w["end"]+limits.capture_margin_after for w in collector.get("capture_windows", [])):
        reasons.append("capture-reserved-window")
    for key, threshold, below in (("available_ram_mb",limits.reserve_ram_mb,True), ("disk_free_mb",limits.min_disk_mb,True),
                                  ("cpu_percent",limits.max_cpu_percent,False), ("pagefile_percent",limits.max_pagefile_percent,False),
                                  ("disk_mbps",limits.max_disk_mbps,False)):
        value = resources.get(key)
        if value is None or not math.isfinite(value) or (value < threshold if below else value > threshold):
            reasons.append("resource:"+key)
    if resources.get("paging_mbps") is not None and resources["paging_mbps"] > limits.max_paging_mbps:
        reasons.append("resource:paging_mbps")
    return sorted(set(reasons))


def decide_admission(resources, collector, job, active, limits, *, gpu_verified_beneficial=False, now=None):
    reasons = protection_reasons(resources, collector, limits, now=now)
    if sum(x.device == job.device for x in active) >= getattr(limits, job.device+"_capacity"):
        reasons.append("device-capacity")
    # Reserve estimated startup RAM for other live jobs in addition to system availability.
    if resources.get("available_ram_mb",0) < limits.reserve_ram_mb + job.memory_mb + sum(x.memory_mb for x in active):
        reasons.append("insufficient-admission-RAM")
    if job.device == "gpu":
        gpu = resources.get("gpu")
        if job.kind != "benchmark" and not gpu_verified_beneficial:
            reasons.append("GPU-benefit-not-demonstrated")
        if not gpu or gpu["available_mb"] < limits.gpu_reserve_mb+job.gpu_memory_mb or gpu["utilization_percent"] > limits.max_gpu_percent:
            reasons.append("GPU-unavailable-or-busy")
    return sorted(set(reasons))


class JobLedger:
    def __init__(self, root, plan_sha256):
        self.root, self.path = Path(root), Path(root)/"job_ledger.sqlite"
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and tables != {"meta","jobs","events","telemetry"}:
                raise ValueError("not-a-market-edge-ledger")
            db.executescript("""CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY,model TEXT,device TEXT,dataset TEXT,fold TEXT,status TEXT,
                    started_at REAL,finished_at REAL,cpu_threads INTEGER,gpu_usage REAL,ram_mb REAL,result_path TEXT,
                    command_json TEXT,spec_json TEXT,pid INTEGER,process_create_time REAL,result_sha256 TEXT,exit_code INTEGER,
                    pause_reason TEXT,paused_seconds REAL DEFAULT 0,gpu_memory_mb REAL);
                CREATE TABLE IF NOT EXISTS events(at REAL,job_id TEXT,status TEXT,detail_json TEXT);
                CREATE TABLE IF NOT EXISTS telemetry(at REAL,resources_json TEXT,collector_json TEXT,reasons_json TEXT,active_jobs_json TEXT);""")
            old = db.execute("SELECT value FROM meta WHERE key='plan_sha256'").fetchone()
            if old and old[0] != plan_sha256:
                raise ValueError("immutable-job-plan-mismatch")
            db.execute("INSERT OR IGNORE INTO meta VALUES('plan_sha256',?)",(plan_sha256,))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def enqueue(self, job):
        specification = _json(asdict(job))
        with self.connect() as db:
            old = db.execute("SELECT * FROM jobs WHERE job_id=?",(job.job_id,)).fetchone()
            if old:
                if old["spec_json"] != specification:
                    raise ValueError("job-id-already-bound-to-other-spec")
                if old["status"] in ("running","paused","unknown","cancelled","failed"):
                    raise ValueError("previous-job-needs-investigation-no-auto-retry")
                if old["status"] == "completed" and _sha(old["result_path"]) != old["result_sha256"]:
                    raise ValueError("completed-result-changed")
                return
            db.execute("INSERT INTO jobs(job_id,model,device,dataset,fold,status,cpu_threads,result_path,command_json,spec_json) VALUES(?,?,?,?,?,'queued',?,?,?,?)",
                       (job.job_id,job.model,job.device,job.dataset,str(job.fold),job.cpu_threads,job.result_path,_json(job.command),specification))

    def update(self, job_id, status, **fields):
        allowed={"started_at","finished_at","gpu_usage","gpu_memory_mb","ram_mb","pid","process_create_time","result_sha256","exit_code","pause_reason","paused_seconds"}
        if not set(fields)<=allowed or status not in {"queued","running","paused","completed","failed","cancelled","unknown"}:
            raise ValueError("invalid-ledger-update")
        with self.connect() as db:
            query=", ".join(["status=?"]+[f"{key}=?" for key in fields])
            db.execute(f"UPDATE jobs SET {query} WHERE job_id=?",[status,*fields.values(),job_id])
            db.execute("INSERT INTO events VALUES(?,?,?,?)",(time.time(),job_id,status,_json(fields)))

    def rows(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY rowid")]

    def telemetry(self, resources, collector, reasons, active_jobs):
        """Persist each admission/pause observation; never infer per-job GPU usage."""
        with self.connect() as db:
            db.execute("INSERT INTO telemetry VALUES(?,?,?,?,?)",
                       (time.time(),_json(resources),_json(collector),_json(reasons),_json(active_jobs)))


class OwnedJob:
    def __init__(self, spec, process, log, token, *, processes=psutil):
        self.spec,self.process,self.log,self.token,self.processes=spec,process,log,token,processes
        self.created_at=processes.Process(process.pid).create_time()
        self.identities={process.pid:self.created_at}
        self.paused=False; self.paused_at=None; self.paused_seconds=0.0
        self.peak_ram_mb=None; self.peak_gpu_mb=None; self.peak_gpu_util=None

    def members(self):
        if self.process.poll() is not None:
            return []  # Never reclaim an orphan from persisted metadata alone.
        try:
            parent=self.processes.Process(self.process.pid)
            if parent.create_time()!=self.created_at:
                return []
            candidates=[parent,*parent.children(recursive=True)]
            result=[]
            for proc in candidates:
                try:
                    created=proc.create_time()
                    if proc.environ().get("MARKET_EDGE_OWNER_TOKEN")!=self.token or created<self.created_at-1:
                        continue
                    if proc.pid in self.identities and self.identities[proc.pid]!=created:
                        continue
                    self.identities[proc.pid]=created
                    result.append(proc)
                except psutil.Error:
                    continue
            return result
        except psutil.Error:
            return []

    def lower_priority(self):
        for proc in self.members():
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform=="win32" else 10)

    def sample(self):
        processes=self.members()
        total=0
        for proc in processes:
            try:
                total+=proc.memory_info().rss
                proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform=="win32" else 10)
            except psutil.Error:
                pass
        if processes:
            self.peak_ram_mb=max(self.peak_ram_mb or 0,total/2**20)

    def pause(self):
        members=self.members()
        if not members:
            raise RuntimeError("owned-process-tree-unverifiable")
        for proc in reversed(members):
            proc.suspend()
        self.paused=True; self.paused_at=time.monotonic()

    def resume(self):
        members=self.members()
        if not members:
            raise RuntimeError("owned-process-tree-unverifiable")
        for proc in members:
            proc.resume()
        self.paused_seconds+=time.monotonic()-self.paused_at
        self.paused=False; self.paused_at=None

    def cancel(self):
        members=self.members()
        if self.process.poll() is None and not members:
            raise RuntimeError("cannot-cancel-unverified-process-tree")
        for proc in reversed(members):
            try: proc.terminate()
            except psutil.NoSuchProcess: pass
        _,alive=self.processes.wait_procs(members,timeout=3)
        for proc in alive:
            # Captured identity must still match, even after terminate timeout.
            if proc.create_time()==self.identities.get(proc.pid) and proc.environ().get("MARKET_EDGE_OWNER_TOKEN")==self.token:
                proc.kill()
        _,alive=self.processes.wait_procs(alive,timeout=3)
        if alive:
            raise RuntimeError("owned-process-remains-alive")


@contextmanager
def _driver_lock(directory):
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/".market-edge-compute.lock").open("a+b") as stream:
        stream.seek(0,2)
        if not stream.tell(): stream.write(b" "); stream.flush()
        stream.seek(0)
        if sys.platform=="win32":
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try: yield
        finally:
            stream.seek(0)
            if sys.platform=="win32": msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def run_jobs(output_root, jobs, collector_root, plan_path, expected_plan_sha256, *, limits=None,
             gpu_verified_beneficial=False, max_wait_seconds=300, collector_monitor=None, resource_monitor=None):
    """Return list of ledger rows. Never fit, promote, or mutate Track A.

    Commands must produce their own result JSON. Benchmark workers should include
    100ms RAM/GPU sampling and train/inference timings there; scheduler samples
    are additional system-wide protection evidence, not a training speed metric.
    """
    root=Path(output_root).resolve(); plan=Path(plan_path).resolve()
    if not root.is_relative_to(BASE.resolve()) or root==BASE.resolve() or not plan.is_relative_to(root):
        raise ValueError("isolated-market-experiment-root-required")
    if not re.fullmatch(r"[a-f0-9]{64}",expected_plan_sha256) or _sha(plan)!=expected_plan_sha256:
        raise ValueError("frozen-experiment-plan-required-before-any-launch")
    if not math.isfinite(max_wait_seconds) or max_wait_seconds<0:
        raise ValueError("bounded-resource-wait-required")
    limits=limits or ProtectionLimits()
    specs=list(jobs)
    if len({j.job_id for j in specs})!=len(specs): raise ValueError("duplicate-job-id")
    for spec in specs: spec.validate(root)
    collector=collector_monitor or CollectorMonitor(collector_root)
    resources=resource_monitor or ResourceMonitor(root)
    active=[]; clear=0; waiting_since=None
    with _driver_lock(BASE):
        ledger=JobLedger(root,expected_plan_sha256)
        for spec in specs: ledger.enqueue(spec)
        try:
            while True:
                if _sha(plan)!=expected_plan_sha256: raise RuntimeError("research-plan-changed")
                state={row["job_id"]:row for row in ledger.rows()}
                for owned in list(active):
                    owned.sample()
                    code=owned.process.poll()
                    if code is not None:
                        owned.log.close(); active.remove(owned)
                        path=Path(owned.spec.result_path)
                        status="completed" if code==0 and path.is_file() else "failed"
                        ledger.update(owned.spec.job_id,status,finished_at=time.time(),exit_code=code,ram_mb=owned.peak_ram_mb,
                            gpu_usage=owned.peak_gpu_util,gpu_memory_mb=owned.peak_gpu_mb,result_sha256=_sha(path) if status=="completed" else None,paused_seconds=owned.paused_seconds)
                        if status!="completed": raise RuntimeError("research-worker-failed:"+owned.spec.job_id)
                        state[owned.spec.job_id]["status"]=status
                pending=[j for j in specs if state[j.job_id]["status"]=="queued"]
                if not pending and not active: return ledger.rows()
                sample=resources.sample(); health=collector.snapshot()
                reasons=protection_reasons(sample,health,limits)
                ledger.telemetry(sample,health,reasons,[x.spec.job_id for x in active])
                for owned in active:
                    gpu=sample.get("gpu")
                    if gpu and owned.spec.device=="gpu":
                        owned.peak_gpu_util=max(owned.peak_gpu_util or 0,gpu["utilization_percent"])
                        owned.peak_gpu_mb=max(owned.peak_gpu_mb or 0,gpu["used_mb"])
                if reasons:
                    clear=0
                    if sample["available_ram_mb"]<limits.reserve_ram_mb/2:
                        raise RuntimeError("critical-RAM-pressure-cancel-owned-research")
                    for owned in active:
                        if not owned.paused:
                            owned.pause()
                            ledger.update(owned.spec.job_id,"paused",pause_reason=", ".join(reasons))
                else:
                    clear+=1
                    if clear>=limits.clear_samples_before_resume:
                        for owned in active:
                            if owned.paused:
                                owned.resume()
                                ledger.update(owned.spec.job_id,"running",pause_reason=None,paused_seconds=owned.paused_seconds)
                    for spec in pending:
                        if any(x.paused for x in active): break
                        if decide_admission(sample,health,spec,[x.spec for x in active],limits,gpu_verified_beneficial=gpu_verified_beneficial): continue
                        if Path(spec.result_path).exists():
                            raise RuntimeError("refusing-preexisting-uncommitted-job-result:"+spec.job_id)
                        logdir=root/"logs";logdir.mkdir(exist_ok=True)
                        log=(logdir/(spec.job_id+"-"+uuid.uuid4().hex+".log")).open("xb")
                        token=uuid.uuid4().hex
                        env=dict(os.environ,MARKET_EDGE_OWNER_TOKEN=token,MARKET_EDGE_JOB_ID=spec.job_id,
                                 OMP_NUM_THREADS=str(spec.cpu_threads),OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1",PYTHONUTF8="1")
                        try:
                            child=subprocess.Popen(spec.command,cwd=spec.cwd,env=env,stdout=log,stderr=subprocess.STDOUT,
                                creationflags=(subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS) if sys.platform=="win32" else 0)
                            owned=OwnedJob(spec,child,log,token)
                            active.append(owned);owned.lower_priority()
                        except BaseException:
                            log.close();raise
                        ledger.update(spec.job_id,"running",started_at=time.time(),pid=child.pid,process_create_time=owned.created_at)
                        sample["available_ram_mb"]-=spec.memory_mb
                stalled=not active or all(x.paused for x in active)
                waiting_since=waiting_since or time.monotonic() if stalled else None
                if waiting_since is not None and time.monotonic()-waiting_since>=max_wait_seconds:
                    raise RuntimeError("protection-wait-timeout:"+", ".join(reasons or ["resource-admission"]))
                time.sleep(limits.poll_seconds)
        except BaseException:
            for owned in active:
                try:
                    owned.cancel()
                    ledger.update(owned.spec.job_id,"cancelled",finished_at=time.time(),pause_reason="driver-stopped-or-protection",ram_mb=owned.peak_ram_mb)
                except Exception as exc:
                    ledger.update(owned.spec.job_id,"unknown",pause_reason=str(exc))
                finally: owned.log.close()
            raise


def summarize_benchmark(records):
    """Same-data comparison only; 10% median fitting benefit is required.

    Metrics require separate review; this alone never authorizes GPU adoption.
    GPU samples are system-wide unless the worker explicitly records otherwise.
    """
    required={"device","dataset_sha256","dataset_rows","feature_count","boosting_rounds","train_seconds","inference_seconds","peak_ram_mb","gpu_memory_mb","metric"}
    if len(records)<2 or any(not required<=set(r) for r in records): raise ValueError("complete-benchmark-records-required")
    keys=("dataset_sha256","dataset_rows","feature_count","boosting_rounds")
    if len({tuple(r[k] for k in keys) for r in records})!=1: raise ValueError("benchmark-cohorts-or-settings-differ")
    for record in records:
        if record["device"] not in {"cpu","gpu"} or any(not isinstance(record[k],(int,float)) or not math.isfinite(record[k]) or record[k]<0 for k in ("train_seconds","inference_seconds","peak_ram_mb","gpu_memory_mb")):
            raise ValueError("finite-benchmark-resources-required")
    import statistics
    timings={d:[r["train_seconds"] for r in records if r["device"]==d] for d in ("cpu","gpu")}
    if not all(timings.values()) or min(sum(timings.values(),[]))<=0: raise ValueError("both-devices-positive-timing-required")
    ratio=statistics.median(timings["cpu"])/statistics.median(timings["gpu"])
    combined={d:statistics.median(r["train_seconds"]+r["inference_seconds"] for r in records if r["device"]==d) for d in ("cpu","gpu")}
    return {"cpu_over_gpu_speedup":ratio,"gpu_faster":ratio>=1.10,"required_fit_speedup":1.10,
            "train_plus_inference_speedup":combined["cpu"]/combined["gpu"],"timing_scope":"median fitting only; train+inference separate, not full campaign",
            "same_cohort":True,"metric_equivalence_requires_caller_review":True,"records":records}

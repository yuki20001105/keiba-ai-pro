"""Pure/temporary safety tests. No collector mutation, real process control, or training."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from market_edge import compute as c


def resources(**changes):
    return {"available_ram_mb":12000,"disk_free_mb":100000,"cpu_percent":10,"pagefile_percent":4,
            "paging_mbps":0,"disk_mbps":1,"gpu":{"available_mb":12000,"used_mb":2000,"utilization_percent":5},**changes}


def healthy(**changes):
    return {"healthy":True,"reasons":[],"capturing":[],"capture_windows":[],**changes}


def spec(tmp_path, **changes):
    return replace(c.JobSpec("j1","logistic","cpu","fixed-sha","2021",[str(c.REPO/"python-api/.venv/Scripts/python.exe"),
        str(c.REPO/"scripts/run_market_edge_worker.py"),"--model","logistic"],str(c.REPO),str(tmp_path/"result.json")),**changes)


@pytest.mark.parametrize("field,value", [("available_ram_mb",1000),("disk_free_mb",100),("cpu_percent",90),
                                         ("pagefile_percent",90),("disk_mbps",500),("disk_mbps",None),("paging_mbps",50)])
def test_resource_pressure_prevents_admission(tmp_path, field, value):
    assert c.decide_admission(resources(**{field:value}),healthy(),spec(tmp_path),[],c.ProtectionLimits(),now=100)


def test_collector_unhealthy_and_reserved_windows_prevent_launch(tmp_path):
    limits=c.ProtectionLimits()
    assert "collector-unhealthy" in c.decide_admission(resources(),healthy(healthy=False),spec(tmp_path),[],limits,now=100)
    health=healthy(capture_windows=[{"start":220,"end":250}])
    assert "capture-reserved-window" in c.decide_admission(resources(),health,spec(tmp_path),[],limits,now=100)
    assert not c.decide_admission(resources(),health,spec(tmp_path),[],limits,now=99)
    assert c.decide_admission(resources(),health,spec(tmp_path),[],limits,now=310)
    assert not c.decide_admission(resources(),health,spec(tmp_path),[],limits,now=311)


def test_adaptive_capacity_allows_cpu_plus_gpu_not_two_cpu(tmp_path):
    cpu=spec(tmp_path)
    gpu=spec(tmp_path,job_id="gpu",device="gpu",kind="benchmark")
    limits=c.ProtectionLimits()
    assert "device-capacity" in c.decide_admission(resources(),healthy(),cpu,[cpu],limits)
    assert not c.decide_admission(resources(),healthy(),gpu,[cpu],limits)
    assert c.decide_admission(resources(available_ram_mb=4800),healthy(),gpu,[cpu],limits)


def test_gpu_only_if_limited_benchmark_or_proven_beneficial(tmp_path):
    gpu=spec(tmp_path,device="gpu")
    assert "GPU-benefit-not-demonstrated" in c.decide_admission(resources(),healthy(),gpu,[],c.ProtectionLimits())
    assert not c.decide_admission(resources(),healthy(),gpu,[],c.ProtectionLimits(),gpu_verified_beneficial=True)
    assert c.decide_admission(resources(gpu=None),healthy(),replace(gpu,kind="benchmark"),[],c.ProtectionLimits())


@pytest.mark.parametrize("changes", [{"cpu_capacity":2},{"reserve_ram_mb":1024},{"poll_seconds":20},{"max_cpu_percent":float("nan")}])
def test_unbounded_or_unsafe_limits_rejected(changes):
    with pytest.raises(ValueError): c.ProtectionLimits(**changes)


def test_jobs_refuse_shell_other_service_and_result_outside_root(tmp_path):
    job=spec(tmp_path)
    job.validate(tmp_path)
    for bad in (replace(job,command=["cmd.exe","/c","echo hi"]),
                replace(job,command=[job.command[0],str(c.REPO/"scripts/run_forward_collector.py")]),
                replace(job,result_path=str(tmp_path.parent/"outside.json")),
                replace(job,device="gpu"),replace(job,job_id="../bad")):
        with pytest.raises(ValueError): bad.validate(tmp_path)


class FakeProcess:
    def __init__(self,pid,created=100,token="owned"):
        self.pid,self.created,self.token=pid,created,token
        self.kids=[];self.actions=[];self.alive=True
    def create_time(self): return self.created
    def environ(self): return {"MARKET_EDGE_OWNER_TOKEN":self.token}
    def children(self,recursive=True): return self.kids
    def nice(self,value): self.actions.append(("nice",value))
    def memory_info(self): return SimpleNamespace(rss=1024**2)
    def suspend(self): self.actions.append("pause")
    def resume(self): self.actions.append("resume")
    def terminate(self): self.actions.append("terminate");self.alive=False
    def kill(self): self.actions.append("kill");self.alive=False


class FakeProcesses:
    def __init__(self,*members): self.mapping={p.pid:p for p in members}
    def Process(self,pid): return self.mapping[pid]
    def wait_procs(self,members,timeout): return ([p for p in members if not p.alive],[p for p in members if p.alive])


def owned(tmp_path):
    parent=FakeProcess(10);worker=FakeProcess(11,101);foreign=FakeProcess(99,101,"not-ours")
    parent.kids=[worker,foreign]
    api=FakeProcesses(parent,worker,foreign)
    popen=SimpleNamespace(pid=10,poll=lambda:None)
    return c.OwnedJob(spec(tmp_path),popen,SimpleNamespace(close=lambda:None),"owned",processes=api),parent,worker,foreign


def test_only_owned_launcher_and_worker_are_paused_resumed_or_cancelled(tmp_path):
    job,parent,worker,foreign=owned(tmp_path)
    job.lower_priority();job.pause();job.resume();job.cancel()
    for process in (parent,worker):
        assert "pause" in process.actions and "resume" in process.actions and "terminate" in process.actions
    assert foreign.actions==[]


def test_reused_parent_pid_is_not_controlled(tmp_path):
    job,parent,worker,_=owned(tmp_path)
    parent.created=999
    with pytest.raises(RuntimeError,match="unverifiable"): job.pause()
    assert parent.actions==worker.actions==[]


def test_orphan_is_never_adopted_from_ledger_or_previous_child_identity(tmp_path):
    job,parent,worker,_=owned(tmp_path)
    assert len(job.members())==2
    job.process.poll=lambda:0
    assert job.members()==[]
    job.cancel()
    assert parent.actions==worker.actions==[]


def test_ledger_required_fields_and_no_automatic_retry(tmp_path):
    ledger=c.JobLedger(tmp_path,"a"*64)
    job=spec(tmp_path)
    ledger.enqueue(job);ledger.enqueue(job)
    row=ledger.rows()[0]
    assert {"job_id","model","device","dataset","fold","status","started_at","finished_at","cpu_threads","gpu_usage","ram_mb","result_path"}<=set(row)
    ledger.update(job.job_id,"running",pid=10,process_create_time=100)
    with pytest.raises(ValueError,match="investigation"): ledger.enqueue(job)
    with pytest.raises(ValueError,match="plan-mismatch"): c.JobLedger(tmp_path,"b"*64)


def test_system_telemetry_and_peak_gpu_are_persisted_not_mislabelled_per_process(tmp_path):
    ledger=c.JobLedger(tmp_path,"a"*64);job=spec(tmp_path);ledger.enqueue(job)
    ledger.telemetry(resources(),healthy(),["capture-reserved-window"],[job.job_id])
    ledger.update(job.job_id,"running",gpu_usage=12,gpu_memory_mb=2500,ram_mb=150)
    with ledger.connect() as db:
        row=dict(db.execute("SELECT * FROM telemetry").fetchone())
    assert json.loads(row["resources_json"])["pagefile_percent"]==4
    assert json.loads(row["active_jobs_json"])==[job.job_id]
    assert json.loads(row["reasons_json"])==["capture-reserved-window"]
    assert ledger.rows()[0]["gpu_memory_mb"]==2500


def test_completed_result_can_only_be_reused_with_same_hash(tmp_path):
    ledger=c.JobLedger(tmp_path,"a"*64);job=spec(tmp_path);ledger.enqueue(job)
    Path(job.result_path).write_text('{"done":true}')
    ledger.update(job.job_id,"completed",result_sha256=c._sha(job.result_path))
    ledger.enqueue(job)
    Path(job.result_path).write_text('{"done":false}')
    with pytest.raises(ValueError,match="result-changed"): ledger.enqueue(job)


def collector_fixture(tmp_path,monkeypatch):
    monkeypatch.setattr(c,"REPO",tmp_path)
    root=tmp_path/"collector";root.mkdir()
    paths=[f"python-api/{package}/module.py" for package in ("forward_runtime","forward_adapters","experiments","prospective")]
    paths += ["scripts/run_forward_collector.py","python-api/training/eligibility.py",
              "python-api/training/local_retrain.py","python-api/scraping/fetch_pipeline.py"]
    sources={}
    for relative in paths:
        source=tmp_path/relative;source.parent.mkdir(parents=True,exist_ok=True);source.write_text("# frozen\n")
        sources[relative]=c._sha(source)
    source=tmp_path/paths[0]
    evaluation=tmp_path/"python-api/forward_reports/evaluation.py"
    evaluation.parent.mkdir(parents=True);evaluation.write_text("# frozen evaluation\n")
    protocol={"paper_only":True,"automatic_purchase":False,"source_manifest":sources,"artifact_hashes":{}}
    digest=hashlib.sha256(c._json(protocol).encode()).hexdigest()
    (root/"protocol.json").write_text(c._json({"protocol":protocol,"protocol_sha256":digest}))
    (root/"analysis-definition.json").write_text(c._json({"protocol_sha256":digest,
        "definition":{"evaluation_source_sha256":c._sha(evaluation)}}))
    with sqlite3.connect(root/"ledger.sqlite3") as db:
        db.executescript("CREATE TABLE owner(singleton,owner_id,expires_at,stop_requested);CREATE TABLE runtime_meta(key,value);CREATE TABLE races(race_id,post_time,status);")
        expiry=datetime.fromtimestamp(1060,timezone.utc).isoformat()
        db.execute("INSERT INTO owner VALUES(1,'pid-321-abcdef',?,0)",(expiry,))
        db.execute("INSERT INTO runtime_meta VALUES('contract',?)",(c._json({"protocol_sha256":digest}),))
        db.execute("INSERT INTO races VALUES('race',?,'queued')",(datetime.fromtimestamp(2000,timezone.utc).isoformat(),))
    process=SimpleNamespace(pid=321,cmdline=lambda:["python",str(tmp_path/"scripts/run_forward_collector.py"),"run","--root",str(root)],
                            is_running=lambda:True,status=lambda:"running",create_time=lambda:900)
    monkeypatch.setattr(c.psutil,"Process",lambda _:process)
    clock=[1000]
    return c.CollectorMonitor(root,clock=lambda:clock[0]),root,source,clock


def test_collector_reads_are_nonmutating_and_source_checked_on_bounded_interval(tmp_path,monkeypatch):
    monitor,root,source,clock=collector_fixture(tmp_path,monkeypatch)
    before={p:p.read_bytes() for p in root.iterdir() if p.is_file()}
    first=monitor.snapshot()
    assert first["healthy"] and first["capture_windows"][0]["start"]==1670
    assert {p:p.read_bytes() for p in before}==before
    source.write_text("# modified")
    clock[0]=1030
    second=monitor.snapshot()
    assert not second["healthy"] and "collector-source-changed" in second["reasons"]


def test_expired_collector_prevents_admission(tmp_path,monkeypatch):
    monitor,_,_,clock=collector_fixture(tmp_path,monkeypatch)
    clock[0]=1046
    assert not monitor.snapshot()["healthy"]


@pytest.mark.parametrize("package",["forward_runtime","forward_adapters","experiments","prospective"])
def test_new_module_in_frozen_packages_is_fail_closed(tmp_path,monkeypatch,package):
    monitor,_,_,clock=collector_fixture(tmp_path,monkeypatch)
    assert monitor.snapshot()["healthy"]
    (tmp_path/f"python-api/{package}/added.py").write_text("# unrecorded module\n")
    clock[0]=1030
    value=monitor.snapshot()
    assert not value["healthy"] and "collector-frozen-source-set-changed" in value["reasons"]


def test_changed_frozen_evaluation_source_is_fail_closed(tmp_path,monkeypatch):
    monitor,_,_,clock=collector_fixture(tmp_path,monkeypatch)
    assert monitor.snapshot()["healthy"]
    (tmp_path/"python-api/forward_reports/evaluation.py").write_text("# changed\n")
    clock[0]=1030
    value=monitor.snapshot()
    assert not value["healthy"] and "collector-evaluation-source-changed" in value["reasons"]


def test_analysis_definition_must_bind_same_protocol_before_first_admission(tmp_path,monkeypatch):
    monitor,root,_,_=collector_fixture(tmp_path,monkeypatch)
    path=root/"analysis-definition.json"
    value=json.loads(path.read_text());value["protocol_sha256"]="f"*64
    path.write_text(c._json(value))
    state=monitor.snapshot()
    assert not state["healthy"] and "collector-analysis-protocol-mismatch" in state["reasons"]


def test_plan_hash_mismatch_does_not_launch_process_or_create_ledger(tmp_path,monkeypatch):
    monkeypatch.setattr(c,"BASE",tmp_path)
    root=tmp_path/"run";root.mkdir();plan=root/"experiment_plan.json";plan.write_text("{}")
    monkeypatch.setattr(c.subprocess,"Popen",lambda *a,**k:pytest.fail("must not launch"))
    with pytest.raises(ValueError,match="frozen-experiment-plan"):
        c.run_jobs(root,[],tmp_path,plan,"a"*64)
    assert not (root/"job_ledger.sqlite").exists()


def test_capture_wait_timeout_never_launches(tmp_path,monkeypatch):
    monkeypatch.setattr(c,"BASE",tmp_path)
    root=tmp_path/"run";root.mkdir();plan=root/"experiment_plan.json";plan.write_text("{}")
    monitor=SimpleNamespace(snapshot=lambda:healthy(capturing=["current-race"]))
    resource=SimpleNamespace(sample=lambda:resources())
    monkeypatch.setattr(c.subprocess,"Popen",lambda *a,**k:pytest.fail("capture must reserve machine"))
    with pytest.raises(RuntimeError,match="protection-wait-timeout"):
        c.run_jobs(root,[spec(root)],tmp_path,plan,c._sha(plan),max_wait_seconds=0,collector_monitor=monitor,resource_monitor=resource)
    assert c.JobLedger(root,c._sha(plan)).rows()[0]["status"]=="queued"


@pytest.mark.parametrize("scenario",["complete","pause_resume","critical_ram"])
def test_run_jobs_completes_or_controls_only_owned_fake_workers(tmp_path,monkeypatch,scenario):
    monkeypatch.setattr(c,"BASE",tmp_path)
    root=tmp_path/"run";root.mkdir();plan=root/"experiment_plan.json";plan.write_text("{}")
    job=spec(root)
    created=[];counter=[0]

    class FakeOwned:
        def __init__(self,specification,process,log,token):
            self.spec,self.process,self.log=specification,process,log
            self.created_at=123;self.paused=False;self.paused_seconds=0
            self.peak_ram_mb=128;self.peak_gpu_util=None;self.peak_gpu_mb=None
            self.actions=[];created.append(self)
        def sample(self):
            if scenario=="complete":
                Path(self.spec.result_path).write_text('{"done":true}')
                self.process.done=True
        def lower_priority(self): self.actions.append("low-priority")
        def pause(self): self.paused=True;self.actions.append("pause")
        def resume(self):
            self.paused=False;self.actions.append("resume");self.paused_seconds=3
            Path(self.spec.result_path).write_text('{"done":true}')
            self.process.done=True
        def cancel(self): self.actions.append("cancel");self.process.done=True

    class FakeChild:
        pid=123;done=False
        def poll(self): return 0 if self.done else None

    def popen(command,**kwargs):
        assert command==job.command
        assert kwargs["env"]["MARKET_EDGE_OWNER_TOKEN"]
        assert kwargs["env"]["OMP_NUM_THREADS"]=="2"
        assert "shell" not in kwargs
        return FakeChild()

    def sampled_resources():
        counter[0]+=1
        return resources(available_ram_mb=1000) if scenario=="critical_ram" and counter[0]>=2 else resources()

    def sampled_health():
        return healthy(capturing=["reserved-race"]) if scenario=="pause_resume" and counter[0]==2 else healthy()

    monkeypatch.setattr(c,"OwnedJob",FakeOwned)
    monkeypatch.setattr(c.subprocess,"Popen",popen)
    monkeypatch.setattr(c.time,"sleep",lambda _:None)
    arguments={"collector_monitor":SimpleNamespace(snapshot=sampled_health),"resource_monitor":SimpleNamespace(sample=sampled_resources)}
    if scenario=="critical_ram":
        with pytest.raises(RuntimeError,match="critical-RAM"):
            c.run_jobs(root,[job],tmp_path,plan,c._sha(plan),**arguments)
        assert created[0].actions==["low-priority","cancel"]
        assert c.JobLedger(root,c._sha(plan)).rows()[0]["status"]=="cancelled"
    else:
        result=c.run_jobs(root,[job],tmp_path,plan,c._sha(plan),**arguments)
        assert result[0]["status"]=="completed" and result[0]["result_sha256"]==c._sha(job.result_path)
        if scenario=="pause_resume":
            assert created[0].actions==["low-priority","pause","resume"]
            assert result[0]["paused_seconds"]==3
    assert len(created)==1 and created[0].log.closed


def test_stale_result_is_not_accepted_as_a_new_success(tmp_path,monkeypatch):
    monkeypatch.setattr(c,"BASE",tmp_path)
    root=tmp_path/"run";root.mkdir();plan=root/"experiment_plan.json";plan.write_text("{}")
    job=spec(root);Path(job.result_path).write_text('{"old":true}')
    monkeypatch.setattr(c.subprocess,"Popen",lambda *a,**k:pytest.fail("old result must not launch"))
    with pytest.raises(RuntimeError,match="preexisting-uncommitted"):
        c.run_jobs(root,[job],tmp_path,plan,c._sha(plan),collector_monitor=SimpleNamespace(snapshot=healthy),
                   resource_monitor=SimpleNamespace(sample=resources))


def test_benchmark_requires_identical_data_and_complete_resource_measurements():
    base={"dataset_sha256":"a"*64,"dataset_rows":20000,"feature_count":9,"boosting_rounds":100,
          "inference_seconds":.1,"peak_ram_mb":100,"gpu_memory_mb":100,"metric":{"nll":.5}}
    report=c.summarize_benchmark([{**base,"device":"cpu","train_seconds":1},{**base,"device":"gpu","train_seconds":2}])
    assert report["gpu_faster"] is False and report["metric_equivalence_requires_caller_review"]
    with pytest.raises(ValueError,match="cohorts"):
        c.summarize_benchmark([{**base,"device":"cpu","train_seconds":1},{**base,"device":"gpu","train_seconds":2,"dataset_rows":10}])
    limited=c.summarize_benchmark([{**base,"device":"cpu","train_seconds":1.09},{**base,"device":"gpu","train_seconds":1}])
    assert not limited["gpu_faster"] and limited["required_fit_speedup"]==1.10

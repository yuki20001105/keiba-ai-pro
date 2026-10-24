"""V4 integration contracts using temp files/mocks, never network/DB/fit/jobs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib
import json
from pathlib import Path
import pickle
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "python-api"), str(ROOT / "keiba")]

import audit_last3f_resume_v3 as public_audit_v3
import audit_last3f_resume_v4 as audit_v4
import continue_last3f_study_v4_once as coordinator_v4
import last3f_cache_processes_v4 as processes_v4
import repair_last3f_gaps_v3 as public_repair_v3
import repair_last3f_gaps_v4 as repair_v4
import run_repaired_context_research_v3 as public_study_v3
import run_repaired_context_research_v4 as study_v4


def test_repair_uses_private_process_executor_and_preserves_public_v3():
    original = {name: getattr(public_repair_v3, name) for name in ("ThreadPoolExecutor", "CODE", "write", "__file__")}
    engine = repair_v4.configured_engine()
    assert engine is not public_repair_v3
    assert engine.ThreadPoolExecutor is processes_v4.ProcessCacheExecutorV4
    assert public_repair_v3.ThreadPoolExecutor is ThreadPoolExecutor
    assert engine.cached_candidate.__code__.co_code == public_repair_v3.cached_candidate.__code__.co_code
    assert engine.cached_candidate.__code__.co_filename == public_repair_v3.cached_candidate.__code__.co_filename
    assert Path(engine.__file__).resolve() == ROOT / "scripts/repair_last3f_gaps_v4.py"
    assert all(getattr(public_repair_v3, key) == value for key, value in original.items())
    for filename in ("scripts/repair_last3f_gaps_v3.py", "scripts/repair_last3f_gaps_v4.py",
            "scripts/last3f_cache_processes_v4.py", "scripts/audit_last3f_resume_v4.py"):
        assert filename in engine.CODE
    assert "scripts/verify_last3f_gap_artifacts_v4.py" not in engine.CODE  # measured slower, kept experimental only


def test_repair_plan_metadata_is_accurate_without_mutating_caller(tmp_path):
    engine = repair_v4.configured_engine()
    original = {"execution": {"cache_parse_threads": 3, "database_writers": 1,
        "minimum_interval_seconds": 3.0, "network_probe_required": True},
        "scope": {"from": "20160101", "to": "20261016"}, "targets_sha256": "a" * 64}
    before = deepcopy(original)
    path = tmp_path / "plan.json"
    engine.write(path, original)
    sealed = engine.read(path)
    assert original == before
    assert "cache_parse_threads" not in sealed["execution"]
    assert sealed["execution"]["cache_parse_processes"] == 3
    assert sealed["execution"]["cache_worker_native_threads"] == 1
    assert sealed["execution"]["database_writers"] == 1
    assert sealed["execution"]["minimum_interval_seconds"] == 3.0
    assert sealed["execution"]["audit_io_optimization_enabled"] is False
    assert sealed["scope"] == before["scope"] and sealed["targets_sha256"] == before["targets_sha256"]
    with pytest.raises(FileExistsError):
        engine.write(path, original)


def test_continuation_metadata_retains_hashes_and_provider_policy(tmp_path):
    engine = repair_v4.configured_engine()
    original = {"schema": "last3f-network-resume-plan-v3", "original_plan_sha256": "a" * 64,
        "new_plan_sha256": "b" * 64, "old_completion_sha256": "c" * 64,
        "automatic_retries": 0, "network_maximum_attempts_per_race": 1, "normal_values_overwritten": False}
    before = deepcopy(original)
    path = tmp_path / "continuation_plan.json"
    engine.write(path, original)
    sealed = engine.read(path)
    assert original == before and all(sealed[key] == value for key, value in before.items())
    assert sealed["minimum_provider_interval_seconds"] == 3.0
    assert sealed["cache_parse_processes"] == 3 and sealed["cache_worker_native_threads"] == 1
    assert sealed["local_implementation_version"] == "v4-cache-processes-audit-baseline"
    assert sealed["audit_io_optimization_enabled"] is False


class FakeFuture:
    def __init__(self):
        self.finished = False
    def done(self):
        return self.finished


class FakePool:
    def __init__(self, **kwargs):
        self.arguments = kwargs
        self.submitted, self.shutdowns = [], []
    def submit(self, fn, *args):
        future = FakeFuture()
        self.submitted.append((fn, args, future))
        return future
    def shutdown(self, **kwargs):
        self.shutdowns.append(kwargs)


@pytest.fixture
def process_pool(monkeypatch):
    pools = []
    def create(**kwargs):
        value = FakePool(**kwargs); pools.append(value); return value
    monkeypatch.setattr(processes_v4, "ProcessPoolExecutor", create)
    monkeypatch.setattr(processes_v4, "_check_source_pins", lambda: None)
    monkeypatch.setattr(processes_v4, "_check_memory_reserve", lambda: None)
    return pools


def test_private_candidate_is_not_pickled_and_worker_is_importable(tmp_path, process_pool):
    engine = repair_v4.configured_engine()
    cache = tmp_path / "cache.sqlite3"; cache.write_bytes(b"synthetic-not-opened-as-db")
    target = {"race_id": "202601010101", "race_date": "20260101", "expected_runners": [{"horse_number": 1}]}
    before = deepcopy(target)
    with processes_v4.ProcessCacheExecutorV4(max_workers=3) as executor:
        future = executor.submit(engine.cached_candidate, target, cache)
    pool = process_pool[0]
    assert pool.arguments["max_workers"] == 3
    assert pool.arguments["mp_context"].get_start_method() == "spawn"
    assert pool.arguments["initializer"] is processes_v4._initialize_worker
    fn, args, actual_future = pool.submitted[0]
    assert fn is processes_v4._parse_cached_target and fn is not engine.cached_candidate
    assert args == (target, str(cache.resolve())) and actual_future is future
    assert target == before
    assert pickle.loads(pickle.dumps(fn)) is fn  # importable public entrypoint, no child is spawned
    assert pool.shutdowns == [{"wait": True, "cancel_futures": False}]


def test_executor_has_three_pending_tasks_and_waits_on_exception(tmp_path, process_pool):
    engine = repair_v4.configured_engine()
    cache = tmp_path / "cache.sqlite3"; cache.write_bytes(b"fixture")
    with pytest.raises(RuntimeError, match="bounded-queue-full"):
        with processes_v4.ProcessCacheExecutorV4(max_workers=3) as executor:
            for number in range(4):
                executor.submit(engine.cached_candidate, {"horse_number": number}, cache)
    assert len(process_pool[0].submitted) == 3
    assert process_pool[0].shutdowns == [{"wait": True, "cancel_futures": True}]


def test_executor_rejects_other_functions_before_dispatch(tmp_path, process_pool):
    cache = tmp_path / "cache.sqlite3"; cache.write_bytes(b"fixture")
    with processes_v4.ProcessCacheExecutorV4(max_workers=3) as executor:
        with pytest.raises(ValueError, match="only-pinned"):
            executor.submit(lambda *args: {}, {}, cache)
    assert process_pool[0].submitted == []


@pytest.mark.parametrize("allow_network", [False, True])
def test_execute_spawns_actual_v4_entrypoint_and_never_audits_live_child(monkeypatch, tmp_path, allow_network):
    import run_last3f_gap_network_once as wrapper
    run = tmp_path / "new-run"; run.mkdir()
    output = tmp_path / "new-output"
    commands, audits, writes = [], [], []
    engine = types.SimpleNamespace(load=lambda _run: ({}, "a" * 64, [], {"repair_output": str(output)}),
        write=lambda path, value: writes.append((Path(path), deepcopy(value))),
        SCHEMA="last3f-network-resume-plan-v3", now=lambda: "2026-10-17T00:00:00+00:00",
        V2=types.SimpleNamespace(preservation_receipt=lambda: {"fixed": True}))
    monkeypatch.setattr(repair_v4, "configured_engine", lambda: engine)
    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        return {"confirmed_exited": False, "exit_code": None, "pid": 1234}
    monkeypatch.setattr(wrapper, "run_process", fake_run)
    monkeypatch.setattr(audit_v4, "audit_resume", lambda *a, **k: audits.append((a, k)))
    with pytest.raises(RuntimeError, match="child-not-exited-no-audit"):
        repair_v4.execute(run, allow_network=allow_network)
    assert len(commands) == 1 and not audits
    command, kwargs = commands[0]
    assert Path(command[0]) == ROOT / "python-api/.venv/Scripts/python.exe"
    assert Path(command[3]) == ROOT / "scripts/repair_last3f_gaps_v4.py"
    assert command[4:7] == ["--run-dir", str(run.resolve()), "--acquire"]
    assert ("--allow-network" in command) is allow_network
    assert any(path.name == "child_exit_receipt.json" for path, _value in writes)
    assert not any(path.name == "completion_receipt.json" for path, _value in writes)


def test_audit_uses_private_unchanged_baseline_checker_without_mutating_public_v3():
    old_checker, old_file = public_audit_v3.source_checker, public_audit_v3.__file__
    engine = audit_v4.configured_auditor()
    checker = importlib.import_module("verify_last3f_gap_artifacts")
    assert engine is not public_audit_v3 and engine.source_checker is checker
    assert public_audit_v3.source_checker is old_checker and public_audit_v3.__file__ == old_file
    assert Path(engine.__file__).resolve() == ROOT / "scripts/audit_last3f_resume_v4.py"
    assert engine.audit_resume.__code__.co_code == public_audit_v3.audit_resume.__code__.co_code
    assert engine.combined_coverage.__code__.co_code == public_audit_v3.combined_coverage.__code__.co_code
    assert engine.combined_journal_audit.__code__.co_code == public_audit_v3.combined_journal_audit.__code__.co_code


def test_audit_wrapper_forwards_only_explicit_arguments(monkeypatch, tmp_path):
    arguments, expected = [], {"audit_pass": "synthetic"}
    def fake_audit(*args, **kwargs):
        arguments.append((args, kwargs)); return expected
    monkeypatch.setattr(audit_v4, "configured_auditor", lambda: types.SimpleNamespace(audit_resume=fake_audit))
    paths = [tmp_path / value for value in ("old", "old-output", "new", "new-output")]
    assert audit_v4.audit_resume(*paths, repository_root=tmp_path) is expected
    assert arguments == [(tuple(paths), {"repository_root": tmp_path})]


@pytest.mark.parametrize("partial", [False, True])
def test_completed_child_is_audited_then_schema_and_partial_semantics_are_preserved(monkeypatch, tmp_path, partial):
    import run_last3f_gap_network_once as wrapper
    run = tmp_path / "new-run"; run.mkdir()
    output = tmp_path / "new-output"
    coverage_path = output / "combined_coverage_audit.json"
    writes, audits = [], []
    engine = types.SimpleNamespace(
        load=lambda _run: ({}, "a" * 64, [], {"repair_output": str(output), "old_completion_sha256": "b" * 64}),
        write=lambda path, value: writes.append((Path(path), deepcopy(value))),
        read=lambda path: ({"coverage_pass": True, "race_status_counts": {"completed": 1, "partial": int(partial)}}
            if Path(path) == coverage_path else {"status": "partial_pending_sources" if partial else "completed"}),
        sha=lambda _path: "c" * 64, guard=lambda *_args: None,
        OLD_RUN=tmp_path / "old", OLD_OUTPUT=tmp_path / "old-output", OLD_PLAN_SHA="d" * 64,
        SCHEMA="last3f-network-resume-plan-v3", now=lambda: "2026-10-17T00:00:00+00:00",
        V2=types.SimpleNamespace(preservation_receipt=lambda: {"fixed": True},
            protected_projection=lambda value: value, atomic_status=lambda *_args: None))
    monkeypatch.setattr(repair_v4, "configured_engine", lambda: engine)
    monkeypatch.setattr(wrapper, "run_process", lambda *_a, **_k: {"confirmed_exited": True, "exit_code": 0, "pid": 1234})
    def fake_audit(*args):
        audits.append(args)
        return {"schema": "last3f-resume-independent-audit-v3", "combined_coverage_audit": str(coverage_path),
            **dict.fromkeys(["source_integrity_pass", "journal_audit_pass", "coverage_pass",
                "verification_source_epoch_stable", "source_files_stable_after_journal_audit"], True)}
    monkeypatch.setattr(audit_v4, "audit_resume", fake_audit)
    result = repair_v4.execute(run)
    assert len(audits) == 1
    assert result["schema"] == "last3f-network-resume-completion-v3"  # auditor schema cannot overwrite the wrapper schema
    assert result["safety_verification_pass"] is True and result["old_status_preserved"] == "stopped"
    assert result["execution_outcome"] == ("partial_pending_sources" if partial else "completed")
    assert any(path.name == "completion_receipt.json" for path, _value in writes)


def fixed_research_plan():
    import repaired_context_evaluation as evaluation
    return {"schema": "repair-gated-context-research-v2", "workers": 3, "threads_per_worker": 2,
        "period": ["20160101", "20260930"], "history_start": "20130101", "test_years": list(range(2016, 2027)),
        "groups": evaluation.groups(), "presets": [{"id": "lean", "num_boost_round": 120}, {"id": "standard", "num_boost_round": 200}],
        "calibration": "CAL-only race temperature; never TEST-fit calibration", "bootstrap_repetitions": 1000,
        "bootstrap_seed": 20261017, "network": False, "activate_model": False, "roi_evaluated": False,
        "code_sha256": {"scripts/run_repaired_context_research_v3.py": public_study_v3.sha(public_study_v3.__file__)},
        "runner_path": "scripts/run_repaired_context_research_v3.py", "runner_sha256": public_study_v3.sha(public_study_v3.__file__)}


def test_research_seals_v4_runner_and_both_source_pins_without_changing_55_jobs(tmp_path):
    engine = study_v4.configured_engine()
    value = fixed_research_plan(); before = deepcopy(value)
    path = tmp_path / "registered_plan.json"
    engine.exclusive(path, value)
    sealed = engine.read(path)
    assert value == before  # the write hook must not silently edit the caller's settings
    engine.assert_fixed_plan(sealed, before)
    assert sealed["runner_path"] == "scripts/run_repaired_context_research_v4.py"
    assert sealed["runner_sha256"] == engine.sha(ROOT / sealed["runner_path"])
    assert sealed["code_sha256"]["scripts/run_repaired_context_research_v3.py"] == engine.sha(ROOT / "scripts/run_repaired_context_research_v3.py")
    assert sealed["code_sha256"]["scripts/run_repaired_context_research_v4.py"] == sealed["runner_sha256"]
    assert [len(group) for group in sealed["groups"].values()] == [46, 60, 73, 53, 80]
    assert len(sealed["groups"]) * len(sealed["test_years"]) == 55
    assert sealed["workers"] == 3 and sealed["threads_per_worker"] == 2
    assert sealed["period"] == ["20160101", "20260930"] and sealed["calibration"] == before["calibration"]


def test_research_extends_only_private_writer_allowlist_and_output_root():
    old_names, old_output = public_study_v3.WRITER_NAMES, public_study_v3.OUTPUT
    engine = study_v4.configured_engine()
    assert "repair_last3f_gaps_v4.py" in engine.WRITER_NAMES
    assert engine.OUTPUT == ROOT / "reports/generated/context-repaired-v4"
    assert public_study_v3.WRITER_NAMES == old_names and public_study_v3.OUTPUT == old_output
    assert "repair_last3f_gaps_v4.py" not in old_names


def test_v4_writer_is_detected_before_any_research_snapshot(monkeypatch):
    import psutil
    engine = study_v4.configured_engine()
    process = types.SimpleNamespace(info={"pid": 77, "name": "python.exe", "cmdline":
        ["python", str(ROOT / "scripts/repair_last3f_gaps_v4.py"), "--run-dir", "fixture", "--acquire"]})
    monkeypatch.setattr(psutil, "process_iter", lambda *_args: [process])
    assert engine.no_acquisition_writer_alive() is False


def test_v4_research_uses_unchanged_frozen_feature_preparation_and_fit_dispatch():
    engine = study_v4.configured_engine()
    configured = engine.configured_engine(None)
    assert configured.SCHEMA == engine.SCHEMA and configured.OUTPUT == engine.OUTPUT
    assert configured.guard is engine.guard and configured.wait_for_repair is engine.wait_for_repair
    for name in ("normalized_frame", "prepare", "snapshot", "run_jobs", "report_html"):
        assert getattr(configured, name).__code__.co_code == getattr(engine.FROZEN, name).__code__.co_code


def test_sealed_protocol_cannot_relax_fixed_research_metric_or_window():
    engine = study_v4.configured_engine()
    fixed = fixed_research_plan()
    for key in ("period", "groups", "calibration", "bootstrap_repetitions", "workers", "threads_per_worker"):
        altered = deepcopy(fixed); altered[key] = "not-authorized"
        with pytest.raises(ValueError, match="fixed-research-setting-changed"):
            engine.assert_fixed_plan(altered, fixed)


@pytest.fixture
def superseded_waiter(monkeypatch, tmp_path):
    import psutil
    root = tmp_path / "repo"; root.mkdir()
    feature = root / "feature"; feature.mkdir()
    parent = root / "coordinators"; parent.mkdir()
    old = parent / "old-v3"; old.mkdir()
    monkeypatch.setattr(coordinator_v4, "ROOT", root)
    monkeypatch.setattr(coordinator_v4, "FEATURE", feature)
    monkeypatch.setattr(coordinator_v4, "PARENT", parent)
    monkeypatch.setattr(coordinator_v4, "SUPERSEDED", old)
    receipt = {"pid": 17, "created_at_epoch": 123.4}
    failure = {"error": "RuntimeError: operator-stop-request-no-more-stages"}
    (old / "start_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    (old / "failure.json").write_text(json.dumps(failure), encoding="utf-8")
    reads, processes, live = [], [], {"epoch": None}
    def read(path):
        reads.append(Path(path))
        return json.loads(Path(path).read_text(encoding="utf-8"))
    def process(pid=None):
        if pid is None:
            return types.SimpleNamespace(create_time=lambda: 456.7)
        if live["epoch"] is None:
            raise psutil.NoSuchProcess(pid)
        return types.SimpleNamespace(create_time=lambda: live["epoch"])
    monkeypatch.setattr(coordinator_v4, "read", read)
    monkeypatch.setattr(psutil, "Process", process)
    monkeypatch.setattr(psutil, "process_iter", lambda *_args: iter(processes))
    return {"root": root, "feature": feature, "parent": parent, "old": old, "receipt": receipt,
        "failure": failure, "reads": reads, "processes": processes, "live": live}


def test_superseded_waiter_same_pid_and_epoch_refuses_v4(superseded_waiter):
    state = superseded_waiter
    state["live"]["epoch"] = state["receipt"]["created_at_epoch"]
    with pytest.raises(RuntimeError, match="still-alive-no-v4"):
        coordinator_v4.superseded_waiter_exited()
    assert [path.name for path in state["reads"]] == ["start_receipt.json"]


@pytest.mark.parametrize("epoch", [None, 987.6])
def test_superseded_waiter_missing_or_reused_pid_is_not_the_old_owner(superseded_waiter, epoch):
    state = superseded_waiter; state["live"]["epoch"] = epoch
    result = coordinator_v4.superseded_waiter_exited()
    assert result["superseded_while_waiting_only"] is True
    assert result["start_sha256"] == coordinator_v4.sha(state["old"] / "start_receipt.json")
    assert result["failure_sha256"] == coordinator_v4.sha(state["old"] / "failure.json")
    assert [path.name for path in state["reads"]] == ["start_receipt.json", "failure.json"]


def test_superseded_waiter_needs_exact_operator_stop_receipt(superseded_waiter):
    state = superseded_waiter
    (state["old"] / "failure.json").write_text(json.dumps({"error": "unexpected-failure"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="not-explicitly-superseded"):
        coordinator_v4.superseded_waiter_exited()


@pytest.mark.parametrize("destination", ["repair", "audit", "study"])
def test_any_started_v3_destination_blocks_v4(superseded_waiter, destination):
    state = superseded_waiter
    path = {"repair": state["feature"] / "20261017-last3f-resume-v3",
        "audit": state["feature"] / "20261017-last3f-resume-v3-audit",
        "study": state["root"] / "reports/generated/context-repaired-v3/20261017-after-repair-v3"}[destination]
    path.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="started-a-stage-no-duplicate"):
        coordinator_v4.superseded_waiter_exited()


@pytest.mark.parametrize("filename", ["register-repair-exit.json", "register-repair.stdout.log.process-start.json", "child-still-running-123.json"])
def test_any_v3_child_start_or_exit_receipt_blocks_v4(superseded_waiter, filename):
    (superseded_waiter["old"] / filename).write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="started-a-stage-no-duplicate"):
        coordinator_v4.superseded_waiter_exited()


@pytest.mark.parametrize("entrypoint", ["continue_last3f_study_v3_once.py", "repair_last3f_gaps_v3.py"])
def test_live_v3_argv_blocks_v4_even_after_recorded_owner_exits(superseded_waiter, entrypoint):
    state = superseded_waiter
    state["processes"].append(types.SimpleNamespace(info={"pid": 88, "name": "python.exe",
        "cmdline": ["python", str(state["root"] / "scripts" / entrypoint), "--run-dir", "fixture"]}))
    with pytest.raises(RuntimeError, match="child-alive-no-v4-start"):
        coordinator_v4.superseded_waiter_exited()


def test_unreadable_python_owner_blocks_v4(superseded_waiter):
    import psutil
    def cmdline():
        raise psutil.AccessDenied(99)
    superseded_waiter["processes"].append(types.SimpleNamespace(
        info={"pid": 99, "name": "python.exe", "cmdline": None}, cmdline=cmdline))
    with pytest.raises(RuntimeError, match="unreadable-owner"):
        coordinator_v4.superseded_waiter_exited()


def test_coordinator_code_drift_stops_before_any_new_stage(monkeypatch, superseded_waiter):
    import repair_last3f_gaps_v2 as repair_v2
    import run_last3f_gap_network_once as wrapper
    import run_repaired_context_research as frozen_study
    state = superseded_waiter
    study = state["root"] / "old-study"; study.mkdir()
    plan = {"code_sha256": {}, "service_processes": [], "repair_processes": []}
    (study / "registered_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(coordinator_v4, "OLD_STUDY", study)
    monkeypatch.setattr(coordinator_v4, "OLD_PLAN_SHA", coordinator_v4.sha(study / "registered_plan.json"))
    for name in ("REPAIR", "AUDIT", "STUDY"):
        monkeypatch.setattr(coordinator_v4, name, state["root"] / ("not-created-" + name))
    probe = state["root"] / "probe.json"; probe.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(coordinator_v4, "PROBE", probe)
    scripts = state["root"] / "scripts"; scripts.mkdir()
    candidate = scripts / "fixture.py"; candidate.write_text("# synthetic", encoding="utf-8")
    monkeypatch.setattr(coordinator_v4, "OWN_CODE", ("scripts/fixture.py",))
    monkeypatch.setattr(repair_v2, "lower_priority", lambda: None)
    monkeypatch.setattr(frozen_study, "guard", lambda _plan: None)
    launched = []
    monkeypatch.setattr(wrapper, "run_process", lambda *a, **k: launched.append((a, k)))
    original_sha = coordinator_v4.sha
    count = {"fixture": 0}
    def sha(path):
        if Path(path) == candidate:
            count["fixture"] += 1
            return "a" * 64 if count["fixture"] == 1 else "b" * 64
        return original_sha(path)
    monkeypatch.setattr(coordinator_v4, "sha", sha)
    output = state["parent"] / "new-v4"
    with pytest.raises(ValueError, match="continuation-code-changed"):
        coordinator_v4.main(output)
    assert not launched
    failure = json.loads((output / "failure.json").read_text())
    assert failure["automatic_retry"] is False and failure["protected_services_stopped"] is False
    assert not any(path.name.endswith("-exit.json") for path in output.iterdir())

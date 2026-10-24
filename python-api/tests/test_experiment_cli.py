"""Offline scheduler/worker contracts: no real processes, GPUs, or training."""
from __future__ import annotations

import io
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from experiments import cli
from experiments.common import file_sha256, read_json, write_json
from experiments.runtime import ExperimentStore


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "experiment-001"
    store = ExperimentStore(root / "experiment.sqlite3", clock=lambda: 1000.0)
    store.initialize()
    store.create_experiment(root.name, {
        "capacity": {"cpu": 2, "gpu": 0}, "reserve_ram_mb": 100,
        "estimated_worker_memory_mb": 200, "source_manifest": {},
        "active_pointer_sha256": None, "dataset_sha256": "a" * 64,
        "dataset_manifest_sha256": "b" * 64,
    })
    monkeypatch.setattr(cli, "store_for", lambda _root: store)
    monkeypatch.setattr(cli, "verify_contract", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.psutil, "Process", lambda *_args: SimpleNamespace(
        create_time=lambda: 50.0, memory_info=lambda: SimpleNamespace(rss=8 * 2**20),
        is_running=lambda: True, children=lambda **_kwargs: []))
    return root, store


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.on_sleep = lambda _seconds: None

    def sleep(self, seconds):
        self.now += seconds
        self.on_sleep(seconds)

    def monotonic(self):
        return self.now

    def time(self):
        return 1000 + self.now

    def time_ns(self):
        return int(self.time() * 10**9)


class FakeChild:
    def __init__(self, pid=900001, *, ignore_terminate=False):
        self.pid = pid
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0
        self.ignore_terminate = ignore_terminate

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        if not self.ignore_terminate:
            self.returncode = -15

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9

    def wait(self, timeout):
        self.wait_calls += 1
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake-owned-worker", timeout)
        return self.returncode


def claim_child(root, store, child, *, timestamp=50.0):
    lease = store.claim(f"{child.pid}-{timestamp}-fake-worker", "cpu")
    write_json(root / "trials" / lease.trial_id / "process.json", {
        "pid": child.pid, "created_at": timestamp, "worker_id": lease.worker_id,
    })
    return lease


def resources():
    return {"available_ram_mb": 10000, "gpu": None}


def test_worker_keeps_trainer_output_empty_and_root_results_and_manifest(setup, monkeypatch):
    root, store = setup
    store.enqueue(root.name, "M01", {"num_threads": 4}, "cpu")
    invoked = []

    def run_trial(dataset, output, payload, progress):
        assert dataset == root / "dataset"
        assert output == root / "trials" / "M01" / "artifacts"
        assert not output.exists()
        assert (output.parent / "process.json").exists()
        progress("training", 50)
        write_json(output / "fold.json", {"safe": True})
        invoked.append(output)
        return {"rmse": 0.4}

    monkeypatch.setitem(sys.modules, "experiments.training", SimpleNamespace(run_trial=run_trial))
    assert cli.worker(root, "cpu") == 0
    output = root / "trials" / "M01"
    assert invoked == [output / "artifacts"]
    assert read_json(output / "result.json")["rmse"] == 0.4
    manifest = read_json(output / "artifact_manifest.json")
    assert manifest[str((output / "artifacts/fold.json").relative_to(output))] == file_sha256(output / "artifacts/fold.json")
    assert "process.json" in manifest and "result.json" in manifest
    assert "progress.json" not in manifest
    assert store.snapshot(root.name)["trials"][0]["status"] == "completed"


def test_worker_cooperatively_stops_when_progress_observes_cancel(setup, monkeypatch):
    root, store = setup
    store.enqueue(root.name, "M01", {"num_threads": 4}, "cpu")

    def run_trial(dataset, output, payload, progress):
        store.cancel(root.name)
        progress("training", 10)
        raise AssertionError("Cancelled worker must not continue")

    monkeypatch.setitem(sys.modules, "experiments.training", SimpleNamespace(run_trial=run_trial))
    assert cli.worker(root, "cpu") == 1
    snapshot = store.snapshot(root.name)
    assert snapshot["status"] == "cancelled"
    assert snapshot["trials"][0]["result"] is None
    assert (root / "trials/M01/failure.json").exists()


def test_worker_setup_failure_marks_claim_failed_without_training(setup, monkeypatch):
    root, store = setup
    store.enqueue(root.name, "M01", {"num_threads": 4}, "cpu")
    monkeypatch.setitem(sys.modules, "experiments.training", SimpleNamespace(
        run_trial=lambda *_args: pytest.fail("must not start training")))
    monkeypatch.setattr(cli, "write_json", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))
    assert cli.worker(root, "cpu") == 1
    assert store.snapshot(root.name)["trials"][0]["status"] == "failed"


def test_batch_resource_starvation_has_deadline_and_starts_no_process(setup, monkeypatch):
    root, store = setup
    clock = FakeClock()
    monkeypatch.setattr(cli, "time", clock)
    monkeypatch.setattr(cli, "resource_snapshot", lambda: {"available_ram_mb": 0, "gpu": None})
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("no resources"))
    with pytest.raises(RuntimeError, match="experiment-resources-unavailable"):
        cli.batch(root, [{"id": "M01"}], resource_wait_timeout_seconds=4, cancel_grace_seconds=0)
    assert clock.now == 4
    assert store.snapshot(root.name)["status"] == "cancelled"


@pytest.mark.parametrize("cooperative", [False, True])
def test_batch_failure_cancels_and_reaps_only_its_owned_child(setup, monkeypatch, cooperative):
    root, store = setup
    clock = FakeClock()
    child = FakeChild()
    unrelated_service = FakeChild(pid=900999)
    captured = {}
    calls = [0]
    monkeypatch.setattr(cli, "time", clock)

    def popen(command, **kwargs):
        captured["log"] = kwargs["stdout"]
        captured["lease"] = claim_child(root, store, child)
        return child

    def resource_snapshot():
        calls[0] += 1
        if calls[0] == 2:
            raise RuntimeError("resource probe failed")
        return resources()

    def sleep(seconds):
        if cooperative and seconds < 1:
            store.fail(captured["lease"], "cancel observed")
            child.returncode = 0

    clock.on_sleep = sleep
    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    monkeypatch.setattr(cli, "resource_snapshot", resource_snapshot)
    with pytest.raises(RuntimeError, match="resource probe failed"):
        cli.batch(root, [{"id": "M01"}, {"id": "M02"}], cancel_grace_seconds=1)
    assert child.terminate_calls == (0 if cooperative else 1)
    assert child.wait_calls >= 1
    assert captured["log"].closed
    assert unrelated_service.terminate_calls == unrelated_service.kill_calls == 0
    assert store.snapshot(root.name)["status"] == "cancelled"


def test_owned_child_escalates_to_kill_and_ack_requires_matching_identity(setup, monkeypatch):
    root, store = setup
    store.enqueue(root.name, "M01", {}, "cpu")
    child = FakeChild(ignore_terminate=True)
    claim_child(root, store, child)
    store.cancel(root.name)
    log = io.BytesIO()
    owned = cli._OwnedChild(child, log, "cpu", 50.0)
    assert cli._reap_owned_children(store, root, [owned], grace_seconds=0) == []
    assert child.terminate_calls == child.kill_calls == 1
    assert log.closed
    assert store.snapshot(root.name)["status"] == "cancelled"


@pytest.mark.parametrize("mismatch", ["pid", "created_at", "worker_id"])
def test_cleanup_does_not_ack_unverified_identity(setup, mismatch):
    root, store = setup
    store.enqueue(root.name, "M01", {}, "cpu")
    child = FakeChild()
    claim_child(root, store, child)
    path = root / "trials/M01/process.json"
    data = read_json(path)
    data[mismatch] = "wrong" if mismatch == "worker_id" else data[mismatch] + 1
    write_json(path, data)
    store.cancel(root.name)
    cli._reap_owned_children(store, root, [cli._OwnedChild(child, io.BytesIO(), "cpu", 50.0)], grace_seconds=0)
    assert store.snapshot(root.name)["trials"][0]["status"] == "cancelling"


def test_popen_error_closes_log_and_cancels_queued_work(setup, monkeypatch):
    root, store = setup
    captured = {}
    monkeypatch.setattr(cli, "resource_snapshot", resources)

    def popen(*_args, **kwargs):
        captured["log"] = kwargs["stdout"]
        raise OSError("cannot start worker")

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    with pytest.raises(OSError, match="cannot start worker"):
        cli.batch(root, [{"id": "M01"}], cancel_grace_seconds=0)
    assert captured["log"].closed
    assert store.snapshot(root.name)["status"] == "cancelled"


def test_nonzero_child_exit_is_not_endlessly_respawned(setup, monkeypatch):
    root, store = setup
    clock = FakeClock()
    child = FakeChild()
    captured = []
    monkeypatch.setattr(cli, "time", clock)
    monkeypatch.setattr(cli, "resource_snapshot", resources)

    def popen(*_args, **kwargs):
        captured.append(kwargs["stdout"])
        child.returncode = 1
        return child

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    with pytest.raises(RuntimeError, match="experiment-worker-exited"):
        cli.batch(root, [{"id": "M01"}], cancel_grace_seconds=0)
    assert len(captured) == 1 and captured[0].closed
    assert store.snapshot(root.name)["status"] == "cancelled"


def test_batch_success_collects_results_and_closes_child_logs(setup, monkeypatch):
    root, store = setup
    clock = FakeClock()
    child = FakeChild()
    captured = {}
    monkeypatch.setattr(cli, "time", clock)
    monkeypatch.setattr(cli, "resource_snapshot", resources)

    def popen(*_args, **kwargs):
        captured["log"] = kwargs["stdout"]
        captured["lease"] = claim_child(root, store, child)
        return child

    def finish(_seconds):
        store.complete(captured["lease"], {"rmse": 0.2})
        child.returncode = 0

    clock.on_sleep = finish
    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    result = cli.batch(root, [{"id": "M01"}], resource_wait_timeout_seconds=0)
    assert result == {"M01": {"rmse": 0.2}}
    assert captured["log"].closed
    assert child.terminate_calls == child.kill_calls == 0


def test_verify_contract_checks_manifest_even_when_dataset_reload_is_skipped(tmp_path, monkeypatch):
    root = tmp_path / "experiment"
    manifest = root / "dataset/dataset_manifest.json"
    write_json(manifest, {"features": ["safe_distance"]})
    contract = {"source_manifest": {}, "active_pointer_sha256": None,
                "dataset_manifest_sha256": file_sha256(manifest)}
    monkeypatch.setattr(cli, "source_manifest", lambda: {})
    monkeypatch.setattr(cli, "active_pointer_hash", lambda: None)
    cli.verify_contract(root, contract, verify_data=False)
    write_json(manifest, {"features": ["future_result"]})
    with pytest.raises(RuntimeError, match="experiment-dataset-manifest-hash-mismatch"):
        cli.verify_contract(root, contract, verify_data=False)


def test_worker_does_not_overwrite_directory_created_during_claim_race(setup, monkeypatch):
    root, store = setup
    store.enqueue(root.name, "M01", {"num_threads": 4}, "cpu")
    output = root / "trials/M01"
    original_mkdir = Path.mkdir

    def concurrent_mkdir(path, *args, **kwargs):
        original_mkdir(path, *args, **kwargs)
        if path == output:
            (output / "original.txt").write_text("keep", encoding="utf-8")
            raise FileExistsError("concurrent output creation")

    monkeypatch.setattr(Path, "mkdir", concurrent_mkdir)
    monkeypatch.setitem(sys.modules, "experiments.training", SimpleNamespace(
        run_trial=lambda *_args: pytest.fail("must not train")))
    assert cli.worker(root, "cpu") == 1
    assert [path.name for path in output.iterdir()] == ["original.txt"]
    assert (output / "original.txt").read_text(encoding="utf-8") == "keep"


def test_existing_running_workers_do_not_count_as_resource_starvation(setup, monkeypatch):
    root, store = setup
    clock = FakeClock()
    for trial_id in ("M01", "M02", "M03"):
        store.enqueue(root.name, trial_id, {"num_threads": 4}, "cpu")
    previous_one = store.claim("previous-worker-1", "cpu")
    previous_two = store.claim("previous-worker-2", "cpu")
    child = FakeChild()
    captured = {}
    monkeypatch.setattr(cli, "time", clock)
    monkeypatch.setattr(cli, "resource_snapshot", lambda: resources() if clock.now >= 4 else {
        "available_ram_mb": 0, "gpu": None})

    def popen(*_args, **kwargs):
        assert clock.now >= 4
        captured["log"] = kwargs["stdout"]
        captured["lease"] = claim_child(root, store, child)
        return child

    def finish(_seconds):
        if clock.now == 2:
            store.complete(previous_one, {"ok": True})
        elif clock.now == 4:
            store.complete(previous_two, {"ok": True})
        else:
            store.complete(captured["lease"], {"ok": True})
            child.returncode = 0

    clock.on_sleep = finish
    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    result = cli.batch(root, [{"id": name} for name in ("M01", "M02", "M03")], resource_wait_timeout_seconds=1)
    assert set(result) == {"M01", "M02", "M03"}
    assert clock.now == 6 and captured["log"].closed


class FakePsProcess(FakeChild):
    """psutil's timeout/error contract differs from subprocess.Popen."""

    def __init__(self, pid, created_at, *, descendants=(), launcher=None, ignore_terminate=False):
        super().__init__(pid, ignore_terminate=ignore_terminate)
        self.created_at = created_at
        self.descendants = descendants
        self.launcher = launcher
        self.children_calls = 0

    def create_time(self):
        return self.created_at

    def is_running(self):
        return (self.launcher.poll() if self.launcher is not None else self.poll()) is None

    def children(self, recursive=False):
        assert recursive is True
        self.children_calls += 1
        return list(self.descendants)

    def wait(self, timeout):
        self.wait_calls += 1
        if self.returncode is None:
            raise cli.psutil.TimeoutExpired(timeout, pid=self.pid)
        return self.returncode


@pytest.mark.parametrize("ignore_terminate", [False, True])
def test_windows_venv_worker_is_verified_and_reaped_before_launcher(setup, monkeypatch, ignore_terminate):
    root, store = setup
    store.enqueue(root.name, "M01", {}, "cpu")
    launcher = FakeChild(pid=901001)
    worker = FakePsProcess(901101, 60.0, ignore_terminate=ignore_terminate)
    unrelated_child = FakePsProcess(901102, 61.0)
    claim_child(root, store, worker, timestamp=60.0)
    store.cancel(root.name)
    parent = FakePsProcess(launcher.pid, 50.0, descendants=[worker, unrelated_child], launcher=launcher)
    monkeypatch.setattr(cli.psutil, "Process", lambda pid: parent if pid == launcher.pid else pytest.fail("unexpected PID lookup"))
    original_terminate = launcher.terminate

    def terminate_launcher():
        assert not worker.is_running(), "actual Python worker must be reaped before its launcher"
        original_terminate()

    monkeypatch.setattr(launcher, "terminate", terminate_launcher)
    log = io.BytesIO()
    assert cli._reap_owned_children(store, root, [cli._OwnedChild(launcher, log, "cpu", 50.0)], grace_seconds=0) == []
    assert worker.terminate_calls == 1
    assert worker.kill_calls == int(ignore_terminate)
    assert worker.wait_calls >= 1 and launcher.wait_calls >= 1
    assert unrelated_child.terminate_calls == unrelated_child.kill_calls == 0
    assert store.snapshot(root.name)["status"] == "cancelled"
    assert log.closed


@pytest.mark.parametrize("mismatch", ["pid", "created_at", "worker_id"])
def test_windows_descendant_metadata_mismatch_never_authorizes_termination(setup, monkeypatch, mismatch):
    root, store = setup
    store.enqueue(root.name, "M01", {}, "cpu")
    launcher = FakeChild(pid=902001)
    worker = FakePsProcess(902101, 60.0)
    claim_child(root, store, worker, timestamp=60.0)
    metadata = root / "trials/M01/process.json"
    value = read_json(metadata)
    value[mismatch] = "wrong-worker" if mismatch == "worker_id" else value[mismatch] + 1
    write_json(metadata, value)
    store.cancel(root.name)
    parent = FakePsProcess(launcher.pid, 50.0, descendants=[worker], launcher=launcher)
    monkeypatch.setattr(cli.psutil, "Process", lambda pid: parent)
    cli._reap_owned_children(store, root, [cli._OwnedChild(launcher, io.BytesIO(), "cpu", 50.0)], grace_seconds=0)
    assert worker.terminate_calls == worker.kill_calls == worker.wait_calls == 0
    assert worker.is_running()
    assert store.snapshot(root.name)["trials"][0]["status"] == "cancelling"


def test_exited_launcher_does_not_authorize_killing_orphan_from_ledger(setup, monkeypatch):
    root, store = setup
    store.enqueue(root.name, "M01", {}, "cpu")
    launcher = FakeChild(pid=903001)
    launcher.returncode = 0
    orphan = FakePsProcess(903101, 60.0)
    claim_child(root, store, orphan, timestamp=60.0)
    store.cancel(root.name)
    monkeypatch.setattr(cli.psutil, "Process", lambda pid: pytest.fail("orphan PID must not be looked up"))
    cli._reap_owned_children(store, root, [cli._OwnedChild(launcher, io.BytesIO(), "cpu", 50.0)], grace_seconds=0)
    assert orphan.terminate_calls == orphan.kill_calls == orphan.wait_calls == 0
    assert store.snapshot(root.name)["trials"][0]["status"] == "cancelling"


def test_launcher_pid_reuse_does_not_authorize_descendant_termination(setup, monkeypatch):
    root, store = setup
    store.enqueue(root.name, "M01", {}, "cpu")
    launcher = FakeChild(pid=904001)
    worker = FakePsProcess(904101, 60.0)
    claim_child(root, store, worker, timestamp=60.0)
    store.cancel(root.name)
    reused_parent = FakePsProcess(launcher.pid, 99.0, descendants=[worker], launcher=launcher)
    monkeypatch.setattr(cli.psutil, "Process", lambda pid: reused_parent)
    cli._reap_owned_children(store, root, [cli._OwnedChild(launcher, io.BytesIO(), "cpu", 50.0)], grace_seconds=0)
    assert reused_parent.children_calls == 0
    assert worker.terminate_calls == worker.kill_calls == 0
    assert store.snapshot(root.name)["trials"][0]["status"] == "cancelling"


def test_launcher_exit_during_discovery_keeps_descendant_unverified(setup, monkeypatch):
    root, store = setup
    store.enqueue(root.name, "M01", {}, "cpu")
    launcher = FakeChild(pid=905001)
    worker = FakePsProcess(905101, 60.0)
    claim_child(root, store, worker, timestamp=60.0)
    store.cancel(root.name)
    parent = FakePsProcess(launcher.pid, 50.0, launcher=launcher)

    def children(recursive):
        assert recursive is True
        launcher.returncode = 0
        return [worker]

    monkeypatch.setattr(parent, "children", children)
    monkeypatch.setattr(cli.psutil, "Process", lambda pid: parent)
    cli._reap_owned_children(store, root, [cli._OwnedChild(launcher, io.BytesIO(), "cpu", 50.0)], grace_seconds=0)
    assert worker.terminate_calls == worker.kill_calls == 0
    assert store.snapshot(root.name)["trials"][0]["status"] == "cancelling"


def test_ledger_worker_outside_verified_descendants_is_not_touched(setup, monkeypatch):
    root, store = setup
    store.enqueue(root.name, "M01", {}, "cpu")
    launcher = FakeChild(pid=906001)
    unrelated_worker = FakePsProcess(906101, 60.0)
    claim_child(root, store, unrelated_worker, timestamp=60.0)
    store.cancel(root.name)
    parent = FakePsProcess(launcher.pid, 50.0, descendants=[], launcher=launcher)
    monkeypatch.setattr(cli.psutil, "Process", lambda pid: parent)
    cli._reap_owned_children(store, root, [cli._OwnedChild(launcher, io.BytesIO(), "cpu", 50.0)], grace_seconds=0)
    assert unrelated_worker.terminate_calls == unrelated_worker.kill_calls == 0
    assert store.snapshot(root.name)["trials"][0]["status"] == "cancelling"

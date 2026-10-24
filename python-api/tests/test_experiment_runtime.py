"""Only temporary experiment ledgers; no application or training imports."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from experiments.runtime import (
    ExperimentCancelled, ExperimentConflict, ExperimentStore, LeaseLost, TrialLease,
)


@pytest.fixture
def ledger(tmp_path):
    now = [1000.0]
    store = ExperimentStore(tmp_path / "comparison" / "ledger.sqlite", clock=lambda: now[0])
    store.initialize()
    return store, now


def create(store, *, cpu=1, gpu=1):
    store.create_experiment("compare-001", {"capacity": {"cpu": cpu, "gpu": gpu}, "baseline_sha256": "a" * 64})


def enqueue(store, trial="M01", device="cpu"):
    store.enqueue("compare-001", trial, {"features": ["safe_distance"], "seed": 42}, device, memory_mb=2048)


def test_complete_is_durable_hashed_and_does_not_activate_model(ledger):
    store, now = ledger
    create(store)
    enqueue(store)
    lease = store.claim("worker-1", "cpu", 60)
    assert TrialLease.from_dict(lease.to_dict()) == lease
    store.update_progress(lease, "validation", 90)
    assert store.snapshot("compare-001")["trials"][0]["record_version"] == lease.record_version
    result = {"rmse": 0.2, "note": "比較実験のみ"}
    store.complete(lease, result, {"path": "trials/M01/model.joblib", "sha256": "b" * 64})
    reopened = ExperimentStore(store.db_path, clock=lambda: now[0])
    reopened.initialize()
    trial = reopened.snapshot("compare-001")["trials"][0]
    assert trial["result"] == result
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert trial["result_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert trial["artifact"]["sha256"] == "b" * 64
    assert trial["status"] == "completed"
    assert trial["progress_pct"] == 100
    assert not list(store.db_path.parent.rglob("*.joblib"))
    assert not list(store.db_path.parent.rglob(".active_model.json"))
    with pytest.raises(LeaseLost):
        store.complete(lease, {"rmse": 999})
    store.finish_experiment("compare-001")
    store.create_experiment("compare-002", {})


def test_single_active_experiment_and_duplicate_trial_are_rejected(ledger):
    store, _ = ledger
    create(store)
    with pytest.raises(ExperimentConflict):
        store.create_experiment("compare-002", {})
    enqueue(store)
    with pytest.raises(ExperimentConflict):
        enqueue(store)
    assert len(store.snapshot("compare-001")["trials"]) == 1


def test_atomic_device_capacity_and_unique_claims_under_concurrency(ledger):
    store, _ = ledger
    create(store, cpu=2)
    for index in range(8):
        enqueue(store, f"cpu-{index}")
    enqueue(store, "gpu-1", "gpu")
    enqueue(store, "gpu-2", "gpu")
    with ThreadPoolExecutor(max_workers=8) as pool:
        leases = list(pool.map(lambda index: store.claim(f"worker-{index}", "cpu"), range(8)))
    claimed = [lease for lease in leases if lease is not None]
    assert len(claimed) == 2
    assert len({lease.trial_id for lease in claimed}) == 2
    assert len({lease.fencing_token for lease in claimed}) == 2
    gpu = store.claim("gpu-worker", "gpu")
    assert gpu is not None
    assert store.claim("gpu-worker-2", "gpu") is None
    store.complete(claimed[0], {"ok": True})
    following = store.claim("worker-next", "cpu")
    assert following.fencing_token > max(lease.fencing_token for lease in claimed)


def test_heartbeat_cas_and_fence_reject_old_or_other_workers(ledger):
    store, now = ledger
    create(store)
    enqueue(store)
    original = store.claim("worker-1", "cpu", 30)
    now[0] += 10
    renewed = store.heartbeat(original, 60)
    assert renewed.lease_expires_at == now[0] + 60
    assert renewed.record_version == original.record_version + 1
    for stale in [original, replace(renewed, worker_id="wrong-worker"),
                  replace(renewed, fencing_token=renewed.fencing_token + 1)]:
        with pytest.raises(LeaseLost):
            store.complete(stale, {"ok": True})
        with pytest.raises(LeaseLost):
            store.update_progress(stale, "training", 50)
    store.complete(renewed, {"ok": True})


def test_expiry_blocks_all_new_claims_and_is_never_requeued(ledger):
    store, now = ledger
    create(store, cpu=2)
    enqueue(store, "M01")
    enqueue(store, "M02")
    enqueue(store, "M03", "gpu")
    lease = store.claim("worker-1", "cpu", 10)
    now[0] += 10
    assert store.claim("worker-2", "cpu") is None
    assert store.claim("worker-3", "gpu") is None
    snapshot = store.snapshot("compare-001")
    assert snapshot["status"] == "blocked"
    assert [row["status"] for row in snapshot["trials"]] == ["unknown", "queued", "queued"]
    with pytest.raises(LeaseLost):
        store.complete(lease, {"ok": True})
    with pytest.raises(LeaseLost):
        store.heartbeat(lease)
    with pytest.raises(ExperimentConflict):
        store.finish_experiment("compare-001")
    with pytest.raises(ExperimentConflict):
        store.create_experiment("compare-002", {})
    store.cancel("compare-001")
    assert store.snapshot("compare-001")["status"] == "blocked"


def test_cancel_experiment_stops_admission_and_requires_owner_termination_ack(ledger):
    store, _ = ledger
    create(store)
    enqueue(store, "M01")
    enqueue(store, "M02")
    running = store.claim("worker-1", "cpu")
    store.cancel("compare-001")
    assert store.claim("worker-2", "cpu") is None
    assert store.snapshot("compare-001")["status"] == "cancelling"
    with pytest.raises(ExperimentCancelled):
        store.complete(running, {"ok": True})
    with pytest.raises(ExperimentCancelled):
        store.heartbeat(running)
    with pytest.raises(LeaseLost):
        store.acknowledge_cancel(replace(running, worker_id="other-worker"))
    store.acknowledge_cancel(running)
    result = store.snapshot("compare-001")
    assert result["status"] == "cancelled"
    assert all(trial["status"] == "cancelled" for trial in result["trials"])
    store.create_experiment("compare-002", {})


def test_cancel_single_trial_preserves_other_trials_and_fail_can_ack_cancel(ledger):
    store, _ = ledger
    create(store)
    enqueue(store, "M01")
    enqueue(store, "M02")
    running = store.claim("worker-1", "cpu")
    store.cancel("compare-001", "M01")
    assert store.claim("worker-2", "cpu") is None  # cancelling worker still occupies capacity
    store.fail(running, "worker terminated after cancellation")
    following = store.claim("worker-2", "cpu")
    assert following.trial_id == "M02"
    store.fail(following, "training failed")
    store.finish_experiment("compare-001")
    assert store.snapshot("compare-001")["status"] == "cancelled"


def test_disabled_gpu_and_invalid_capacity(ledger):
    store, _ = ledger
    for capacity in [{"cpu": 3, "gpu": 1}, {"cpu": 1, "gpu": 2}, {"cpu": True, "gpu": 0}]:
        with pytest.raises(ValueError):
            store.create_experiment("bad-capacity", {"capacity": capacity})
    create(store, cpu=2, gpu=0)
    with pytest.raises(ExperimentConflict):
        enqueue(store, "gpu-trial", "gpu")
    assert store.claim("gpu-worker", "gpu") is None


@pytest.mark.parametrize("ttl", [0, -1, float("nan"), float("inf"), True, "60"])
def test_invalid_ttl_is_rejected_without_claiming(ledger, ttl):
    store, _ = ledger
    create(store)
    enqueue(store)
    with pytest.raises(ValueError):
        store.claim("worker-1", "cpu", ttl)
    assert store.snapshot("compare-001")["trials"][0]["status"] == "queued"


def test_nonfinite_results_and_bad_artifact_hash_never_publish(ledger):
    store, _ = ledger
    create(store)
    enqueue(store)
    lease = store.claim("worker-1", "cpu")
    with pytest.raises(ValueError):
        store.complete(lease, {"rmse": float("nan")})
    with pytest.raises(ValueError):
        store.complete(lease, {}, {"sha256": "invalid"})
    assert store.snapshot("compare-001")["trials"][0]["result"] is None


def test_reject_existing_nonexperiment_database_without_schema_changes(tmp_path):
    path = tmp_path / "not-an-experiment.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE races (race_id TEXT)")
        connection.execute("INSERT INTO races VALUES ('saved-race')")
    with pytest.raises(ExperimentConflict):
        ExperimentStore(path).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("races",)]
        assert connection.execute("SELECT race_id FROM races").fetchall() == [("saved-race",)]


def test_competing_stores_cannot_create_two_active_experiments(ledger):
    store, now = ledger

    def attempt(index):
        other = ExperimentStore(store.db_path, clock=lambda: now[0])
        try:
            other.create_experiment(f"experiment-{index}", {})
            return True
        except ExperimentConflict:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(4))) == 1


def test_contract_and_payload_are_detached_and_hashed(ledger):
    store, _ = ledger
    contract = {"capacity": {"cpu": 1, "gpu": 0}, "baseline": "frozen"}
    payload = {"features": ["safe_distance"]}
    store.create_experiment("compare-001", contract)
    store.enqueue("compare-001", "M01", payload, "cpu")
    contract["baseline"] = "changed"
    payload["features"].append("future_result")
    snapshot = store.snapshot("compare-001")
    assert snapshot["contract"]["baseline"] == "frozen"
    assert snapshot["trials"][0]["payload"]["features"] == ["safe_distance"]
    lease = store.claim("worker-1", "cpu")
    lease.payload["features"].append("untrusted_local_mutation")
    assert store.snapshot("compare-001")["trials"][0]["payload"]["features"] == ["safe_distance"]
    canonical = json.dumps(snapshot["contract"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert snapshot["contract_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()


def test_progress_does_not_extend_lease_and_expired_result_is_not_stored(ledger):
    store, now = ledger
    create(store)
    enqueue(store)
    lease = store.claim("worker-1", "cpu", 10)
    now[0] += 9
    store.update_progress(lease, "evaluation", 99)
    assert store.snapshot("compare-001")["trials"][0]["lease_expires_at"] == 1010
    now[0] += 1
    with pytest.raises(LeaseLost):
        store.complete(lease, {"ok": True})
    trial = store.snapshot("compare-001")["trials"][0]
    assert trial["status"] == "unknown"
    assert trial["result"] is None


def test_cancellation_is_idempotent_and_cannot_erase_completed_results(ledger):
    store, _ = ledger
    create(store)
    enqueue(store, "M01")
    enqueue(store, "M02")
    lease = store.claim("worker-1", "cpu")
    store.complete(lease, {"rmse": 0.5})
    store.cancel("compare-001", "M01")
    store.cancel("compare-001", "M02")
    first = store.snapshot("compare-001")
    store.cancel("compare-001", "M02")
    second = store.snapshot("compare-001")
    assert first["trials"] == second["trials"]
    assert second["trials"][0]["result"] == {"rmse": 0.5}
    assert second["trials"][0]["status"] == "completed"


def test_finish_requires_all_trials_and_failed_experiment_releases_admission(ledger):
    store, _ = ledger
    create(store)
    enqueue(store)
    with pytest.raises(ExperimentConflict):
        store.finish_experiment("compare-001")
    lease = store.claim("worker-1", "cpu")
    with pytest.raises(ExperimentConflict):
        store.finish_experiment("compare-001")
    store.fail(lease, "evaluation did not meet the input contract")
    store.finish_experiment("compare-001")
    assert store.snapshot("compare-001")["status"] == "failed"
    store.create_experiment("compare-002", {})


def test_cancelled_lease_expiry_is_unknown_not_assumed_dead(ledger):
    store, now = ledger
    create(store)
    enqueue(store)
    lease = store.claim("worker-1", "cpu", 10)
    store.cancel("compare-001")
    now[0] += 11
    with pytest.raises(LeaseLost):
        store.acknowledge_cancel(lease)
    assert store.snapshot("compare-001")["trials"][0]["status"] == "unknown"
    assert store.snapshot("compare-001")["status"] == "blocked"

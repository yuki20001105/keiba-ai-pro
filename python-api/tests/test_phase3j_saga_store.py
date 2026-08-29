from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python-api"))

from scraping.cross_store_saga_codec import (  # noqa: E402
    SagaCodecError,
    decode_event,
    decode_snapshot,
    encode_snapshot,
    event_sha256,
    snapshot_sha256,
)
from scraping.cross_store_saga_contract import (  # noqa: E402
    EventKind,
    SagaBinding,
    SagaEvent,
    SagaState,
)
from scraping.cross_store_saga_store import (  # noqa: E402
    OutboxClaim,
    OutboxState,
    SagaStore,
    SagaStoreCorruptionError,
    SagaStoreDisabledError,
    StoreResultCode,
)
from scraping.saga_runtime_config import SagaRuntimeConfig  # noqa: E402


OPERATION_ID = "11111111-1111-4111-8111-111111111111"
REVIEW_ID = "22222222-2222-4222-8222-222222222222"
OWNER_ID = "33333333-3333-4333-8333-333333333333"
JOB_ID = "44444444-4444-4444-8444-444444444444"
RESERVATION_ID = "55555555-5555-4555-8555-555555555555"
REQUEST_HASH = "a" * 64


def _binding(**changes: object) -> SagaBinding:
    values: dict[str, object] = {
        "operation_id": OPERATION_ID,
        "review_id": REVIEW_ID,
        "review_version": 3,
        "owner_user_id": OWNER_ID,
        "job_id": JOB_ID,
        "request_hash": REQUEST_HASH,
    }
    values.update(changes)
    return SagaBinding(**values)  # type: ignore[arg-type]


def _store(
    tmp_path: Path,
    *,
    fault=None,
) -> SagaStore:
    store = SagaStore(
        SagaRuntimeConfig.ci_disposable(tmp_path / "phase3j.sqlite"),
        fault_injector=fault,
    )
    store.initialize()
    return store


def _prepare(store: SagaStore):
    prepared = store.prepare(_binding(), 100)
    assert prepared.code is StoreResultCode.APPLIED
    assert prepared.snapshot is not None
    assert prepared.outbox is not None
    return prepared


def test_disabled_configuration_cannot_construct_a_store() -> None:
    with pytest.raises(SagaStoreDisabledError, match="phase3j-runtime-disabled"):
        SagaStore(SagaRuntimeConfig())


def test_initialize_enforces_sqlite_safety_pragmas_and_isolated_schema(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    connection = sqlite3.connect(store.database_path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()
    assert tables == {
        "phase3j_jobs",
        "phase3j_sagas",
        "phase3j_saga_events",
        "phase3j_outbox",
    }
    assert "jobs" not in tables


def test_atomic_prepare_inserts_job_saga_and_initial_outbox(tmp_path: Path) -> None:
    store = _store(tmp_path)
    prepared = _prepare(store)
    assert prepared.snapshot.state is SagaState.RESERVE_PENDING
    assert prepared.outbox.state is OutboxState.PENDING
    assert prepared.outbox.intent == prepared.snapshot.pending_intent
    assert store.table_counts() == {
        "phase3j_jobs": 1,
        "phase3j_sagas": 1,
        "phase3j_saga_events": 0,
        "phase3j_outbox": 1,
    }

    replay = store.prepare(_binding(), 101)
    assert replay.code is StoreResultCode.DUPLICATE
    assert store.table_counts()["phase3j_outbox"] == 1


@pytest.mark.parametrize(
    "checkpoint", ["after-job-insert", "after-saga-insert", "after-outbox-insert"]
)
def test_prepare_rolls_back_every_row_on_injected_crash(
    tmp_path: Path, checkpoint: str
) -> None:
    def fail(name: str) -> None:
        if name == checkpoint:
            raise RuntimeError("injected-crash")

    store = _store(tmp_path, fault=fail)
    with pytest.raises(RuntimeError, match="injected-crash"):
        store.prepare(_binding(), 100)
    assert store.table_counts() == {
        "phase3j_jobs": 0,
        "phase3j_sagas": 0,
        "phase3j_saga_events": 0,
        "phase3j_outbox": 0,
    }


def test_prepare_rejects_operation_or_job_identity_conflicts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _prepare(store)
    changed_review = store.prepare(_binding(review_version=4), 101)
    assert changed_review.code is StoreResultCode.CONFLICT

    changed_operation = store.prepare(
        _binding(operation_id="66666666-6666-4666-8666-666666666666"), 102
    )
    assert changed_operation.code is StoreResultCode.CONFLICT
    assert store.table_counts()["phase3j_jobs"] == 1


def test_snapshot_codec_is_exact_canonical_and_binding_hash_checked(tmp_path: Path) -> None:
    snapshot = _prepare(_store(tmp_path)).snapshot
    payload = encode_snapshot(snapshot)
    assert decode_snapshot(payload, expected_hash=snapshot_sha256(payload)) == snapshot

    extra = json.loads(payload)
    extra["unexpected"] = True
    extra_payload = json.dumps(extra, sort_keys=True, separators=(",", ":"))
    with pytest.raises(SagaCodecError, match="snapshot-schema-invalid"):
        decode_snapshot(extra_payload)

    binding_tamper = json.loads(payload)
    binding_tamper["binding"]["binding_hash"] = "0" * 64
    tampered_payload = json.dumps(binding_tamper, sort_keys=True, separators=(",", ":"))
    with pytest.raises(SagaCodecError, match="binding-hash-mismatch"):
        decode_snapshot(tampered_payload)


def test_persisted_snapshot_corruption_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _prepare(store)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE phase3j_sagas SET snapshot_hash=? WHERE operation_id=?",
            ("0" * 64, OPERATION_ID),
        )
    with pytest.raises(SagaStoreCorruptionError, match="snapshot-hash-mismatch"):
        store.load_snapshot(OPERATION_ID)


def test_apply_appends_event_and_next_outbox_and_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    initial = _prepare(store).snapshot
    event = SagaEvent(
        kind=EventKind.RESERVATION_GRANTED,
        event_id="00000000-0000-4000-8000-000000000001",
        binding_hash=initial.binding.binding_hash,
        intent_id=initial.pending_intent.intent_id,  # type: ignore[union-attr]
        observed_at_epoch=110,
        reservation_id=RESERVATION_ID,
        reservation_fencing_token=7,
        reservation_expires_at_epoch=200,
    )
    applied = store.apply(OPERATION_ID, event)
    assert applied.code is StoreResultCode.APPLIED
    assert applied.snapshot.state is SagaState.LOCAL_PREPARE_PENDING
    assert store.table_counts()["phase3j_saga_events"] == 1
    assert store.table_counts()["phase3j_outbox"] == 2

    replay = store.apply(OPERATION_ID, event)
    assert replay.code is StoreResultCode.DUPLICATE
    assert store.table_counts()["phase3j_saga_events"] == 1

    conflicting = SagaEvent(
        kind=EventKind.RESERVATION_GRANTED,
        event_id=event.event_id,
        binding_hash=initial.binding.binding_hash,
        intent_id=initial.pending_intent.intent_id,  # type: ignore[union-attr]
        observed_at_epoch=110,
        reservation_id=RESERVATION_ID,
        reservation_fencing_token=7,
        reservation_expires_at_epoch=201,
    )
    assert store.apply(OPERATION_ID, conflicting).code is StoreResultCode.CONFLICT


def test_event_log_is_append_only_even_for_direct_sql(tmp_path: Path) -> None:
    store = _store(tmp_path)
    initial = _prepare(store).snapshot
    store.apply(
        OPERATION_ID,
        SagaEvent(
            kind=EventKind.RESERVATION_REJECTED,
            event_id="00000000-0000-4000-8000-000000000002",
            binding_hash=initial.binding.binding_hash,
            intent_id=initial.pending_intent.intent_id,  # type: ignore[union-attr]
            observed_at_epoch=110,
            reason_code="review-rejected",
        ),
    )
    with sqlite3.connect(store.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM phase3j_saga_events")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE phase3j_saga_events SET event_hash=?", ("0" * 64,)
            )


def test_claim_is_single_winner_under_concurrency(tmp_path: Path) -> None:
    store = _store(tmp_path)
    intent_id = _prepare(store).outbox.intent.intent_id

    def claim(index: int):
        return store.claim(intent_id, f"owner-{index}", 110, 30)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))
    assert sum(result.code is StoreResultCode.APPLIED for result in results) == 1
    assert sum(result.code is StoreResultCode.CONFLICT for result in results) == 7
    record = store.load_outbox(intent_id)
    assert record.state is OutboxState.CLAIMED
    assert record.fencing_token == 1


def test_expired_claim_recovery_increments_fence_and_stale_ack_is_rejected(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    intent_id = _prepare(store).outbox.intent.intent_id
    first = store.claim(intent_id, "owner-a", 110, 5).claim
    assert first is not None
    recovered = store.recover_outbox(OPERATION_ID, 115)
    assert recovered.recovered_count == 1
    second = store.claim(intent_id, "owner-b", 116, 10).claim
    assert second is not None
    assert second.fencing_token == first.fencing_token + 1

    stale = store.acknowledge(first, "a" * 64, "confirmed", 117)
    assert stale.code is StoreResultCode.CONFLICT
    accepted = store.acknowledge(second, "b" * 64, "confirmed", 117)
    assert accepted.code is StoreResultCode.APPLIED
    assert accepted.outbox.state is OutboxState.ACKNOWLEDGED


def test_settlement_replay_is_idempotent_but_conflicting_replay_fails(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    intent_id = _prepare(store).outbox.intent.intent_id
    claim = store.claim(intent_id, "owner-a", 110, 30).claim
    assert claim is not None
    first = store.block(claim, "a" * 64, "ambiguous-outcome", 111)
    assert first.code is StoreResultCode.APPLIED
    duplicate = store.block(claim, "a" * 64, "ambiguous-outcome", 112)
    assert duplicate.code is StoreResultCode.DUPLICATE
    conflict = store.block(claim, "b" * 64, "ambiguous-outcome", 112)
    assert conflict.code is StoreResultCode.CONFLICT
    wrong_terminal = store.acknowledge(claim, "a" * 64, "ambiguous-outcome", 112)
    assert wrong_terminal.code is StoreResultCode.CONFLICT


def test_unavailable_release_returns_claim_to_pending_without_resetting_fence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    intent_id = _prepare(store).outbox.intent.intent_id
    claim = store.claim(intent_id, "owner-a", 110, 30).claim
    assert claim is not None
    released = store.release(claim, 111)
    assert released.code is StoreResultCode.APPLIED
    assert released.outbox.state is OutboxState.PENDING
    assert released.outbox.fencing_token == 1
    next_claim = store.claim(intent_id, "owner-b", 112, 30).claim
    assert next_claim is not None
    assert next_claim.fencing_token == 2


def test_recover_outbox_is_operation_scoped_and_unknown_operation_changes_nothing(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = _prepare(store)
    second_binding = _binding(
        operation_id="66666666-6666-4666-8666-666666666666",
        review_id="77777777-7777-4777-8777-777777777777",
        job_id="88888888-8888-4888-8888-888888888888",
    )
    second = store.prepare(second_binding, 100)
    assert second.code is StoreResultCode.APPLIED
    first_claim = store.claim(first.outbox.intent.intent_id, "owner-a", 110, 5).claim
    second_claim = store.claim(second.outbox.intent.intent_id, "owner-b", 110, 5).claim
    assert first_claim is not None and second_claim is not None

    before_first = store.load_outbox(first_claim.intent.intent_id)
    before_second = store.load_outbox(second_claim.intent.intent_id)
    unknown = store.recover_outbox(
        "99999999-9999-4999-8999-999999999999", 115
    )
    assert unknown.code is StoreResultCode.NOT_FOUND
    assert unknown.recovered_count == 0
    assert store.load_outbox(first_claim.intent.intent_id) == before_first
    assert store.load_outbox(second_claim.intent.intent_id) == before_second

    recovered = store.recover_outbox(OPERATION_ID, 115)
    assert recovered.code is StoreResultCode.APPLIED
    assert recovered.recovered_count == 1
    assert store.load_outbox(first_claim.intent.intent_id).state is OutboxState.PENDING  # type: ignore[union-attr]
    assert store.load_outbox(second_claim.intent.intent_id).state is OutboxState.CLAIMED  # type: ignore[union-attr]


def test_recovery_event_is_full_canonical_saga_event_json(tmp_path: Path) -> None:
    store = _store(tmp_path)
    initial = _prepare(store).snapshot
    granted = store.apply(
        OPERATION_ID,
        SagaEvent(
            kind=EventKind.RESERVATION_GRANTED,
            event_id="00000000-0000-4000-8000-000000000010",
            binding_hash=initial.binding.binding_hash,
            intent_id=initial.pending_intent.intent_id,  # type: ignore[union-attr]
            observed_at_epoch=110,
            reservation_id=RESERVATION_ID,
            reservation_fencing_token=7,
            reservation_expires_at_epoch=120,
        ),
    )
    assert granted.code is StoreResultCode.APPLIED
    recovered = store.recover(OPERATION_ID, 120)
    assert recovered.code is StoreResultCode.APPLIED

    connection = sqlite3.connect(store.database_path)
    try:
        row = connection.execute(
            "SELECT event_hash,event_json FROM phase3j_saga_events ORDER BY created_at_epoch DESC LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    event = decode_event(row[1], expected_hash=row[0])
    assert event.kind is EventKind.RESERVATION_EXPIRED
    assert event.binding_hash == initial.binding.binding_hash
    assert event.observed_at_epoch == 120
    assert event_sha256(row[1]) == row[0]


def test_all_store_connections_close_so_disposable_directory_is_immediately_removable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "disposable"
    workspace.mkdir()
    store = SagaStore(
        SagaRuntimeConfig.ci_disposable(workspace / "phase3j.sqlite")
    )
    store.initialize()
    prepared = store.prepare(_binding(), 100)
    intent_id = prepared.outbox.intent.intent_id
    claim = store.claim(intent_id, "owner-a", 110, 5).claim
    assert claim is not None
    store.release(claim, 111)
    store.recover_outbox(OPERATION_ID, 112)
    store.load_snapshot(OPERATION_ID)
    store.load_outbox(intent_id)
    store.table_counts()
    shutil.rmtree(workspace)
    assert workspace.exists() is False


def test_store_rejects_clock_regression_for_saga_and_outbox_operations(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    prepared = _prepare(store)
    intent_id = prepared.outbox.intent.intent_id
    regression_claim = store.claim(intent_id, "owner-a", 99, 5)
    assert regression_claim.code is StoreResultCode.REJECTED
    assert regression_claim.reason_code == "outbox-clock-regression"

    claim = store.claim(intent_id, "owner-a", 110, 5).claim
    assert claim is not None
    assert store.release(claim, 109).reason_code == "outbox-clock-regression"
    assert (
        store.block(claim, "a" * 64, "ambiguous-outcome", 109).reason_code
        == "outbox-clock-regression"
    )
    assert store.recover_outbox(OPERATION_ID, 109).reason_code == "outbox-clock-regression"

    initial = prepared.snapshot
    old_event = SagaEvent(
        kind=EventKind.RESERVATION_REJECTED,
        event_id="00000000-0000-4000-8000-000000000011",
        binding_hash=initial.binding.binding_hash,
        intent_id=initial.pending_intent.intent_id,  # type: ignore[union-attr]
        observed_at_epoch=99,
        reason_code="review-rejected",
    )
    assert store.apply(OPERATION_ID, old_event).reason_code == "saga-clock-regression"
    assert store.recover(OPERATION_ID, 99).reason_code == "saga-clock-regression"


def test_fabricated_claim_with_different_intent_or_binding_is_rejected(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    prepared = _prepare(store)
    claim = store.claim(prepared.outbox.intent.intent_id, "owner-a", 110, 30).claim
    assert claim is not None
    corrupted_intent = replace(
        claim.intent,
        operation_id="66666666-6666-4666-8666-666666666666",
    )
    fabricated_record = replace(claim.record, intent=corrupted_intent)
    fabricated = OutboxClaim(fabricated_record, claim.owner)
    result = store.block(fabricated, "a" * 64, "ambiguous-outcome", 111)
    assert result.code is StoreResultCode.CONFLICT
    assert result.reason_code == "outbox-claim-binding-conflict"
    assert store.load_outbox(claim.intent.intent_id).state is OutboxState.CLAIMED  # type: ignore[union-attr]

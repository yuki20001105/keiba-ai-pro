from __future__ import annotations

import ast
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python-api"))

from scraping.cross_store_saga_contract import (  # noqa: E402
    AppliedEvent,
    EffectAction,
    EventKind,
    SagaBinding,
    SagaEvent,
    SagaSnapshot,
    SagaState,
    apply_event,
    create_saga,
    recover,
    validate_snapshot,
)


OPERATION_ID = "11111111-1111-4111-8111-111111111111"
REVIEW_ID = "22222222-2222-4222-8222-222222222222"
OWNER_ID = "33333333-3333-4333-8333-333333333333"
JOB_ID = "44444444-4444-4444-8444-444444444444"
RESERVATION_ID = "55555555-5555-4555-8555-555555555555"
REQUEST_HASH = "a" * 64
RECEIPT_HASH = "b" * 64


def _event_id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


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


def _initial() -> SagaSnapshot:
    created = create_saga(_binding())
    assert created.accepted
    return created.snapshot


def _grant(snapshot: SagaSnapshot, *, event_number: int = 1, expiry: int = 200) -> SagaSnapshot:
    assert snapshot.pending_intent is not None
    result = apply_event(
        snapshot,
        SagaEvent(
            kind=EventKind.RESERVATION_GRANTED,
            event_id=_event_id(event_number),
            binding_hash=snapshot.binding.binding_hash,
            intent_id=snapshot.pending_intent.intent_id,
            observed_at_epoch=100,
            reservation_id=RESERVATION_ID,
            reservation_fencing_token=7,
            reservation_expires_at_epoch=expiry,
        ),
    )
    assert result.accepted, result.failure_code
    return result.snapshot


def _prepare(snapshot: SagaSnapshot, *, event_number: int = 2) -> SagaSnapshot:
    assert snapshot.pending_intent is not None
    result = apply_event(
        snapshot,
        SagaEvent(
            kind=EventKind.LOCAL_PREPARE_SUCCEEDED,
            event_id=_event_id(event_number),
            binding_hash=snapshot.binding.binding_hash,
            intent_id=snapshot.pending_intent.intent_id,
            observed_at_epoch=110,
        ),
    )
    assert result.accepted, result.failure_code
    return result.snapshot


def _consume(snapshot: SagaSnapshot, *, event_number: int = 3) -> SagaSnapshot:
    assert snapshot.pending_intent is not None
    result = apply_event(
        snapshot,
        SagaEvent(
            kind=EventKind.RESERVATION_CONSUMED,
            event_id=_event_id(event_number),
            binding_hash=snapshot.binding.binding_hash,
            intent_id=snapshot.pending_intent.intent_id,
            observed_at_epoch=120,
            consume_receipt_hash=RECEIPT_HASH,
        ),
    )
    assert result.accepted, result.failure_code
    return result.snapshot


def _lease(
    snapshot: SagaSnapshot,
    *,
    event_number: int = 4,
    fencing_token: int = 1,
    expiry: int = 180,
) -> SagaSnapshot:
    assert snapshot.pending_intent is not None
    result = apply_event(
        snapshot,
        SagaEvent(
            kind=EventKind.WORKER_LEASE_GRANTED,
            event_id=_event_id(event_number),
            binding_hash=snapshot.binding.binding_hash,
            intent_id=snapshot.pending_intent.intent_id,
            observed_at_epoch=130,
            worker_fencing_token=fencing_token,
            worker_lease_expires_at_epoch=expiry,
        ),
    )
    assert result.accepted, result.failure_code
    return result.snapshot


def test_binding_hashes_separate_review_execution_and_combined_identity() -> None:
    original = _binding()
    changed_review = _binding(review_version=4)
    changed_job = _binding(job_id="66666666-6666-4666-8666-666666666666")

    assert original.review_binding_hash != changed_review.review_binding_hash
    assert original.execution_binding_hash == changed_review.execution_binding_hash
    assert original.binding_hash != changed_review.binding_hash

    assert original.review_binding_hash == changed_job.review_binding_hash
    assert original.execution_binding_hash != changed_job.execution_binding_hash
    assert original.binding_hash != changed_job.binding_hash
    assert len({original.review_binding_hash, original.execution_binding_hash, original.binding_hash}) == 3


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("operation_id", "not-a-uuid"),
        ("review_id", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
        ("review_version", True),
        ("owner_user_id", ""),
        ("job_id", "44444444"),
        ("request_hash", "A" * 64),
    ],
)
def test_binding_rejects_noncanonical_or_ambiguous_values(field_name: str, value: object) -> None:
    with pytest.raises(ValueError):
        _binding(**{field_name: value})


def test_create_is_deterministic_immutable_and_emits_only_reserve_intent() -> None:
    first = create_saga(_binding())
    second = create_saga(_binding())

    assert first == second
    assert first.snapshot.state is SagaState.RESERVE_PENDING
    assert [intent.action for intent in first.emitted_intents] == [EffectAction.RESERVE_REVIEW]
    assert first.snapshot.pending_intent == first.emitted_intents[0]
    assert validate_snapshot(first.snapshot) == ()
    with pytest.raises(FrozenInstanceError):
        first.snapshot.state = SagaState.RUNNING  # type: ignore[misc]


def test_happy_path_never_emits_dispatch_before_consume_receipt() -> None:
    created = create_saga(_binding())
    assert created.emitted_intents[0].action is EffectAction.RESERVE_REVIEW

    granted = _grant(created.snapshot)
    assert granted.pending_intent is not None
    assert granted.pending_intent.action is EffectAction.PREPARE_LOCAL_TRANSACTION
    assert granted.consume_receipt_hash is None

    prepared = _prepare(granted)
    assert prepared.pending_intent is not None
    assert prepared.pending_intent.action is EffectAction.CONSUME_RESERVATION
    assert prepared.consume_receipt_hash is None

    consumed = _consume(prepared)
    assert consumed.pending_intent is not None
    assert consumed.pending_intent.action is EffectAction.DISPATCH_WORKER
    assert consumed.consume_receipt_hash == RECEIPT_HASH

    running = _lease(consumed)
    assert running.state is SagaState.RUNNING
    succeeded = apply_event(
        running,
        SagaEvent(
            kind=EventKind.WORKER_SUCCEEDED,
            event_id=_event_id(5),
            binding_hash=running.binding.binding_hash,
            observed_at_epoch=140,
            worker_fencing_token=1,
        ),
    )
    assert succeeded.accepted
    assert succeeded.snapshot.state is SagaState.SUCCEEDED
    assert succeeded.snapshot.terminal
    assert succeeded.emitted_intents == ()
    assert validate_snapshot(succeeded.snapshot) == ()


def test_review_approval_or_any_unknown_event_fails_closed_without_effect() -> None:
    snapshot = _initial()
    result = apply_event(
        snapshot,
        SagaEvent(
            kind="review_approved",
            event_id=_event_id(10),
            binding_hash=snapshot.binding.binding_hash,
        ),
    )
    assert not result.accepted
    assert result.failure_code == "unknown-event-kind"
    assert result.snapshot == snapshot
    assert result.emitted_intents == ()


def test_wrong_binding_and_wrong_pending_intent_fail_closed() -> None:
    snapshot = _initial()
    wrong_binding = apply_event(
        snapshot,
        SagaEvent(
            kind=EventKind.RESERVATION_REJECTED,
            event_id=_event_id(11),
            binding_hash="c" * 64,
            intent_id=snapshot.pending_intent.intent_id,  # type: ignore[union-attr]
            reason_code="not-authorized",
        ),
    )
    assert not wrong_binding.accepted
    assert wrong_binding.failure_code == "event-binding-mismatch"
    assert wrong_binding.emitted_intents == ()

    wrong_intent = apply_event(
        snapshot,
        SagaEvent(
            kind=EventKind.RESERVATION_REJECTED,
            event_id=_event_id(12),
            binding_hash=snapshot.binding.binding_hash,
            intent_id="d" * 64,
            reason_code="not-authorized",
        ),
    )
    assert not wrong_intent.accepted
    assert wrong_intent.emitted_intents == ()
    assert wrong_intent.snapshot == snapshot


def test_worker_claim_before_consume_is_rejected_and_emits_nothing() -> None:
    prepared = _prepare(_grant(_initial()))
    assert prepared.state is SagaState.CONSUME_PENDING
    result = apply_event(
        prepared,
        SagaEvent(
            kind=EventKind.WORKER_LEASE_GRANTED,
            event_id=_event_id(13),
            binding_hash=prepared.binding.binding_hash,
            intent_id=prepared.pending_intent.intent_id,  # type: ignore[union-attr]
            observed_at_epoch=120,
            worker_fencing_token=1,
            worker_lease_expires_at_epoch=180,
        ),
    )
    assert not result.accepted
    assert result.failure_code == "worker-lease-grant-invalid"
    assert result.snapshot == prepared
    assert result.emitted_intents == ()


def test_duplicate_event_is_idempotent_but_payload_conflict_is_rejected() -> None:
    initial = _initial()
    intent_id = initial.pending_intent.intent_id  # type: ignore[union-attr]
    event = SagaEvent(
        kind=EventKind.RESERVATION_GRANTED,
        event_id=_event_id(14),
        binding_hash=initial.binding.binding_hash,
        intent_id=intent_id,
        observed_at_epoch=100,
        reservation_id=RESERVATION_ID,
        reservation_fencing_token=7,
        reservation_expires_at_epoch=200,
    )
    granted = apply_event(initial, event)
    replay = apply_event(granted.snapshot, event)
    assert replay.accepted and replay.duplicate
    assert replay.snapshot == granted.snapshot
    assert replay.emitted_intents == ()

    conflict = apply_event(granted.snapshot, replace(event, observed_at_epoch=101))
    assert not conflict.accepted
    assert conflict.failure_code == "event-id-payload-conflict"
    assert conflict.emitted_intents == ()


def test_local_prepare_failure_compensates_without_dispatch() -> None:
    granted = _grant(_initial())
    failed = apply_event(
        granted,
        SagaEvent(
            kind=EventKind.LOCAL_PREPARE_FAILED,
            event_id=_event_id(20),
            binding_hash=granted.binding.binding_hash,
            intent_id=granted.pending_intent.intent_id,  # type: ignore[union-attr]
            observed_at_epoch=110,
            reason_code="sqlite-transaction-failed",
        ),
    )
    assert failed.accepted
    assert failed.snapshot.state is SagaState.COMPENSATION_PENDING
    assert [intent.action for intent in failed.emitted_intents] == [EffectAction.RELEASE_RESERVATION]
    assert all(intent.action is not EffectAction.DISPATCH_WORKER for intent in failed.emitted_intents)

    released = apply_event(
        failed.snapshot,
        SagaEvent(
            kind=EventKind.RELEASE_CONFIRMED,
            event_id=_event_id(21),
            binding_hash=failed.snapshot.binding.binding_hash,
            intent_id=failed.snapshot.pending_intent.intent_id,  # type: ignore[union-attr]
            observed_at_epoch=120,
        ),
    )
    assert released.accepted
    assert released.snapshot.state is SagaState.COMPENSATED
    assert released.snapshot.terminal
    assert released.emitted_intents == ()


def test_expired_reservation_cannot_be_consumed_and_recovery_compensates() -> None:
    prepared = _prepare(_grant(_initial(), expiry=115))
    assert prepared.state is SagaState.CONSUME_PENDING
    consume = apply_event(
        prepared,
        SagaEvent(
            kind=EventKind.RESERVATION_CONSUMED,
            event_id=_event_id(22),
            binding_hash=prepared.binding.binding_hash,
            intent_id=prepared.pending_intent.intent_id,  # type: ignore[union-attr]
            observed_at_epoch=115,
            consume_receipt_hash=RECEIPT_HASH,
        ),
    )
    assert not consume.accepted
    assert consume.emitted_intents == ()

    recovered = recover(prepared, 115)
    assert recovered.accepted
    assert recovered.snapshot.state is SagaState.COMPENSATION_PENDING
    assert recovered.emitted_intents[0].action is EffectAction.RELEASE_RESERVATION


def test_recovery_reemits_same_pending_intent_without_advancing_state() -> None:
    prepared = _prepare(_grant(_initial()))
    recovered = recover(prepared, 150)
    assert recovered.accepted
    assert recovered.snapshot == prepared
    assert recovered.emitted_intents == (prepared.pending_intent,)
    assert recovered.emitted_intents[0].action is EffectAction.CONSUME_RESERVATION


def test_lease_expiry_reissues_dispatch_with_higher_fence_and_rejects_stale_worker() -> None:
    running = _lease(_consume(_prepare(_grant(_initial()))), fencing_token=4, expiry=150)
    recovered = recover(running, 150)
    assert recovered.accepted
    assert recovered.snapshot.state is SagaState.DISPATCH_PENDING
    assert recovered.emitted_intents[0].action is EffectAction.DISPATCH_WORKER
    assert recovered.emitted_intents[0].minimum_worker_fencing_token == 5

    stale = apply_event(
        recovered.snapshot,
        SagaEvent(
            kind=EventKind.WORKER_LEASE_GRANTED,
            event_id=_event_id(30),
            binding_hash=recovered.snapshot.binding.binding_hash,
            intent_id=recovered.snapshot.pending_intent.intent_id,  # type: ignore[union-attr]
            observed_at_epoch=151,
            worker_fencing_token=4,
            worker_lease_expires_at_epoch=190,
        ),
    )
    assert not stale.accepted
    assert stale.emitted_intents == ()

    fresh = apply_event(
        recovered.snapshot,
        SagaEvent(
            kind=EventKind.WORKER_LEASE_GRANTED,
            event_id=_event_id(31),
            binding_hash=recovered.snapshot.binding.binding_hash,
            intent_id=recovered.snapshot.pending_intent.intent_id,  # type: ignore[union-attr]
            observed_at_epoch=151,
            worker_fencing_token=5,
            worker_lease_expires_at_epoch=190,
        ),
    )
    assert fresh.accepted
    assert fresh.snapshot.worker_fencing_token == 5

    old_worker = apply_event(
        fresh.snapshot,
        SagaEvent(
            kind=EventKind.WORKER_SUCCEEDED,
            event_id=_event_id(32),
            binding_hash=fresh.snapshot.binding.binding_hash,
            observed_at_epoch=160,
            worker_fencing_token=4,
        ),
    )
    assert not old_worker.accepted
    assert old_worker.emitted_intents == ()


@pytest.mark.parametrize("terminal_path", ["succeeded", "worker_failed", "compensated", "manual"])
def test_terminal_states_are_immutable(terminal_path: str) -> None:
    if terminal_path == "succeeded":
        running = _lease(_consume(_prepare(_grant(_initial()))))
        terminal = apply_event(
            running,
            SagaEvent(
                kind=EventKind.WORKER_SUCCEEDED,
                event_id=_event_id(40),
                binding_hash=running.binding.binding_hash,
                observed_at_epoch=140,
                worker_fencing_token=1,
            ),
        ).snapshot
    elif terminal_path == "worker_failed":
        running = _lease(_consume(_prepare(_grant(_initial()))))
        terminal = apply_event(
            running,
            SagaEvent(
                kind=EventKind.WORKER_FAILED,
                event_id=_event_id(41),
                binding_hash=running.binding.binding_hash,
                observed_at_epoch=140,
                worker_fencing_token=1,
                reason_code="bounded-worker-failure",
            ),
        ).snapshot
    elif terminal_path == "compensated":
        granted = _grant(_initial())
        pending = apply_event(
            granted,
            SagaEvent(
                kind=EventKind.LOCAL_PREPARE_FAILED,
                event_id=_event_id(42),
                binding_hash=granted.binding.binding_hash,
                intent_id=granted.pending_intent.intent_id,  # type: ignore[union-attr]
                observed_at_epoch=110,
                reason_code="sqlite-transaction-failed",
            ),
        ).snapshot
        terminal = apply_event(
            pending,
            SagaEvent(
                kind=EventKind.RELEASE_CONFIRMED,
                event_id=_event_id(43),
                binding_hash=pending.binding.binding_hash,
                intent_id=pending.pending_intent.intent_id,  # type: ignore[union-attr]
                observed_at_epoch=120,
            ),
        ).snapshot
    else:
        initial = _initial()
        terminal = apply_event(
            initial,
            SagaEvent(
                kind=EventKind.SAFETY_VIOLATION,
                event_id=_event_id(44),
                binding_hash=initial.binding.binding_hash,
                reason_code="adapter-contract-violation",
            ),
        ).snapshot

    assert terminal.terminal
    rejected = apply_event(
        terminal,
        SagaEvent(
            kind=EventKind.SAFETY_VIOLATION,
            event_id=_event_id(45),
            binding_hash=terminal.binding.binding_hash,
            reason_code="late-conflicting-event",
        ),
    )
    assert not rejected.accepted
    assert rejected.failure_code == "terminal-state-immutable"
    assert rejected.snapshot == terminal
    assert rejected.emitted_intents == ()
    assert recover(terminal, 999).snapshot == terminal


def test_malformed_snapshot_fails_closed_in_apply_and_recovery() -> None:
    valid = _initial()
    malformed = replace(valid, pending_intent=None)
    assert validate_snapshot(malformed) == ("pending-intent-required",)

    applied = apply_event(
        malformed,
        SagaEvent(
            kind=EventKind.RESERVATION_REJECTED,
            event_id=_event_id(50),
            binding_hash=malformed.binding.binding_hash,
            reason_code="not-authorized",
        ),
    )
    assert not applied.accepted
    assert applied.emitted_intents == ()
    recovered = recover(malformed, 100)
    assert not recovered.accepted
    assert recovered.emitted_intents == ()


@pytest.mark.parametrize(
    "malformed_state",
    [
        "unknown",
        ["running"],
        {"state": "running"},
        object(),
    ],
)
def test_unknown_or_unhashable_snapshot_state_is_rejected_without_exception(
    malformed_state: object,
) -> None:
    valid = _initial()
    malformed = replace(valid, state=malformed_state)  # type: ignore[arg-type]

    errors = validate_snapshot(malformed)
    assert "state-invalid" in errors
    assert malformed.terminal is False

    applied = apply_event(
        malformed,
        SagaEvent(
            kind=EventKind.RESERVATION_REJECTED,
            event_id=_event_id(51),
            binding_hash=malformed.binding.binding_hash,
            intent_id=malformed.pending_intent.intent_id,  # type: ignore[union-attr]
            observed_at_epoch=100,
            reason_code="not-authorized",
        ),
    )
    assert applied.accepted is False
    assert applied.failure_code == "snapshot-invalid:state-invalid"
    assert applied.snapshot is malformed
    assert applied.emitted_intents == ()

    recovered = recover(malformed, 100)
    assert recovered.accepted is False
    assert recovered.failure_code == "snapshot-invalid:state-invalid"
    assert recovered.snapshot is malformed
    assert recovered.emitted_intents == ()


def test_malformed_snapshot_structure_is_stably_rejected_without_effect() -> None:
    valid = _initial()
    assert valid.pending_intent is not None
    malformed_intent_action = replace(valid.pending_intent, action={"action": "reserve_review"})  # type: ignore[arg-type]
    malformed_intent_sequence = replace(valid.pending_intent, sequence=[1])  # type: ignore[arg-type]
    malformed_intent_binding = replace(valid.pending_intent, binding_hash={"hash": "bad"})  # type: ignore[arg-type]
    malformed_cases: list[tuple[SagaSnapshot, str]] = [
        (replace(valid, binding=None), "binding-invalid"),  # type: ignore[arg-type]
        (replace(valid, binding={"binding_hash": valid.binding.binding_hash}), "binding-invalid"),  # type: ignore[arg-type]
        (replace(valid, pending_intent={}), "pending-intent-invalid"),  # type: ignore[arg-type]
        (replace(valid, pending_intent=[valid.pending_intent]), "pending-intent-invalid"),  # type: ignore[arg-type]
        (replace(valid, pending_intent=malformed_intent_action), "pending-intent-action-invalid"),
        (replace(valid, pending_intent=malformed_intent_sequence), "pending-intent-sequence-invalid"),
        (replace(valid, pending_intent=malformed_intent_binding), "pending-intent-binding-hash-invalid"),
        (replace(valid, applied_events=[]), "applied-events-invalid"),  # type: ignore[arg-type]
        (replace(valid, applied_events=({},)), "applied-event-invalid"),  # type: ignore[arg-type]
        (
            replace(valid, applied_events=(AppliedEvent(event_id=["bad"], event_hash="c" * 64),)),  # type: ignore[arg-type]
            "applied-event-id-invalid",
        ),
        (
            replace(valid, applied_events=(AppliedEvent(event_id=_event_id(52), event_hash={"hash": "bad"}),)),  # type: ignore[arg-type]
            "applied-event-hash-invalid",
        ),
    ]

    for index, (malformed, expected_code) in enumerate(malformed_cases, start=60):
        errors = validate_snapshot(malformed)
        assert expected_code in errors

        applied = apply_event(
            malformed,
            SagaEvent(
                kind=EventKind.RESERVATION_REJECTED,
                event_id=_event_id(index),
                binding_hash=valid.binding.binding_hash,
                intent_id=valid.pending_intent.intent_id,
                observed_at_epoch=100,
                reason_code="not-authorized",
            ),
        )
        assert applied.accepted is False
        assert applied.failure_code is not None
        assert applied.failure_code.startswith("snapshot-invalid:")
        assert applied.snapshot is malformed
        assert applied.emitted_intents == ()

        recovered = recover(malformed, 100)
        assert recovered.accepted is False
        assert recovered.failure_code is not None
        assert recovered.failure_code.startswith("snapshot-invalid:")
        assert recovered.snapshot is malformed
        assert recovered.emitted_intents == ()


def test_nested_dispatch_fence_corruption_does_not_escape_validation() -> None:
    dispatch_pending = _consume(_prepare(_grant(_initial())))
    assert dispatch_pending.state is SagaState.DISPATCH_PENDING
    malformed = replace(dispatch_pending, worker_fencing_token={"fence": 1})  # type: ignore[arg-type]

    errors = validate_snapshot(malformed)
    assert "worker-fencing-token-invalid" in errors
    applied = apply_event(
        malformed,
        SagaEvent(
            kind=EventKind.WORKER_LEASE_GRANTED,
            event_id=_event_id(80),
            binding_hash=dispatch_pending.binding.binding_hash,
            intent_id=dispatch_pending.pending_intent.intent_id,  # type: ignore[union-attr]
            observed_at_epoch=130,
            worker_fencing_token=1,
            worker_lease_expires_at_epoch=180,
        ),
    )
    assert not applied.accepted
    assert applied.snapshot is malformed
    assert applied.emitted_intents == ()
    recovered = recover(malformed, 130)
    assert not recovered.accepted
    assert recovered.snapshot is malformed
    assert recovered.emitted_intents == ()


@pytest.mark.parametrize("invalid_binding", [None, {}, [], object(), True])
def test_create_saga_rejects_non_binding_inputs_without_effect(invalid_binding: object) -> None:
    result = create_saga(invalid_binding)  # type: ignore[arg-type]
    assert result.accepted is False
    assert result.failure_code == "binding-invalid"
    assert result.emitted_intents == ()
    assert "binding-invalid" in validate_snapshot(result.snapshot)


def test_mutated_binding_identity_and_derived_hash_fail_closed() -> None:
    identity_snapshot = _initial()
    original_identity_hash = identity_snapshot.binding.binding_hash
    object.__setattr__(identity_snapshot.binding, "operation_id", [OPERATION_ID])
    identity_errors = validate_snapshot(identity_snapshot)
    assert "binding-operation-id-invalid" in identity_errors
    identity_result = apply_event(
        identity_snapshot,
        SagaEvent(
            kind=EventKind.RESERVATION_REJECTED,
            event_id=_event_id(95),
            binding_hash=original_identity_hash,
            intent_id=identity_snapshot.pending_intent.intent_id,  # type: ignore[union-attr]
            reason_code="not-authorized",
        ),
    )
    assert not identity_result.accepted
    assert identity_result.snapshot is identity_snapshot
    assert identity_result.emitted_intents == ()

    hash_snapshot = _initial()
    object.__setattr__(hash_snapshot.binding, "binding_hash", "0" * 64)
    hash_errors = validate_snapshot(hash_snapshot)
    assert "binding-hash-mismatch" in hash_errors
    hash_result = recover(hash_snapshot, 100)
    assert not hash_result.accepted
    assert hash_result.failure_code == "snapshot-invalid:binding-hash-mismatch"
    assert hash_result.snapshot is hash_snapshot
    assert hash_result.emitted_intents == ()


def test_create_saga_rejects_a_runtime_mutated_binding_without_effect() -> None:
    binding = _binding()
    object.__setattr__(binding, "request_hash", [])
    result = create_saga(binding)
    assert not result.accepted
    assert result.failure_code == "binding-request-hash-invalid"
    assert result.emitted_intents == ()


@pytest.mark.parametrize("invalid_snapshot", [None, {}, [], object(), True])
def test_public_transition_apis_reject_non_snapshot_inputs_without_effect(
    invalid_snapshot: object,
) -> None:
    valid = _initial()
    event = SagaEvent(
        kind=EventKind.RESERVATION_REJECTED,
        event_id=_event_id(90),
        binding_hash=valid.binding.binding_hash,
        intent_id=valid.pending_intent.intent_id,  # type: ignore[union-attr]
        observed_at_epoch=100,
        reason_code="not-authorized",
    )
    applied = apply_event(invalid_snapshot, event)  # type: ignore[arg-type]
    assert applied.accepted is False
    assert applied.failure_code == "snapshot-invalid:snapshot-type-invalid"
    assert applied.snapshot is invalid_snapshot
    assert applied.emitted_intents == ()

    recovered = recover(invalid_snapshot, 100)  # type: ignore[arg-type]
    assert recovered.accepted is False
    assert recovered.failure_code == "snapshot-invalid:snapshot-type-invalid"
    assert recovered.snapshot is invalid_snapshot
    assert recovered.emitted_intents == ()


@pytest.mark.parametrize("invalid_event", [None, {}, [], object(), True])
def test_apply_event_rejects_non_event_inputs_without_effect(invalid_event: object) -> None:
    snapshot = _initial()
    result = apply_event(snapshot, invalid_event)  # type: ignore[arg-type]
    assert result.accepted is False
    assert result.failure_code == "event-type-invalid"
    assert result.snapshot is snapshot
    assert result.emitted_intents == ()


@pytest.mark.parametrize("bad_value", [object(), [], {}, True])
@pytest.mark.parametrize(
    ("field_name", "failure_code"),
    [
        ("intent_id", "event-intent-id-invalid"),
        ("reservation_id", "event-reservation-id-invalid"),
        ("reason_code", "event-reason-code-invalid"),
        ("consume_receipt_hash", "event-consume-receipt-hash-invalid"),
        ("reservation_fencing_token", "event-reservation-fencing-token-invalid"),
        ("reservation_expires_at_epoch", "event-reservation-expiry-invalid"),
        ("worker_fencing_token", "event-worker-fencing-token-invalid"),
        ("worker_lease_expires_at_epoch", "event-worker-lease-expiry-invalid"),
        ("observed_at_epoch", "event-observed-at-invalid"),
        ("binding_hash", "event-binding-hash-invalid"),
    ],
)
def test_event_payload_structural_types_fail_before_event_hashing(
    field_name: str,
    failure_code: str,
    bad_value: object,
) -> None:
    snapshot = _initial()
    assert snapshot.pending_intent is not None
    valid_event = SagaEvent(
        kind=EventKind.RESERVATION_REJECTED,
        event_id=_event_id(91),
        binding_hash=snapshot.binding.binding_hash,
        intent_id=snapshot.pending_intent.intent_id,
        observed_at_epoch=100,
        reason_code="not-authorized",
    )
    malformed = replace(valid_event, **{field_name: bad_value})
    result = apply_event(snapshot, malformed)
    assert result.accepted is False
    assert result.failure_code == failure_code
    assert result.snapshot is snapshot
    assert result.emitted_intents == ()


@pytest.mark.parametrize("bad_kind", [object(), [], {}, True])
def test_non_contract_event_kind_types_fail_closed(bad_kind: object) -> None:
    snapshot = _initial()
    event = SagaEvent(
        kind=bad_kind,  # type: ignore[arg-type]
        event_id=_event_id(92),
        binding_hash=snapshot.binding.binding_hash,
    )
    result = apply_event(snapshot, event)
    assert result.accepted is False
    assert result.failure_code == "event-kind-invalid"
    assert result.snapshot is snapshot
    assert result.emitted_intents == ()


def test_worker_failed_snapshot_with_non_string_receipt_fails_closed() -> None:
    running = _lease(_consume(_prepare(_grant(_initial()))))
    failed = apply_event(
        running,
        SagaEvent(
            kind=EventKind.WORKER_FAILED,
            event_id=_event_id(93),
            binding_hash=running.binding.binding_hash,
            observed_at_epoch=140,
            worker_fencing_token=1,
            reason_code="bounded-worker-failure",
        ),
    ).snapshot
    assert failed.state is SagaState.FAILED_TERMINAL
    malformed = replace(failed, consume_receipt_hash=[])

    assert "unexpected-consume-receipt" in validate_snapshot(malformed)
    result = apply_event(
        malformed,
        SagaEvent(
            kind=EventKind.SAFETY_VIOLATION,
            event_id=_event_id(94),
            binding_hash=failed.binding.binding_hash,
            reason_code="late-conflicting-event",
        ),
    )
    assert result.accepted is False
    assert result.failure_code == "snapshot-invalid:unexpected-consume-receipt"
    assert result.snapshot is malformed
    assert result.emitted_intents == ()
    recovered = recover(malformed, 150)
    assert recovered.accepted is False
    assert recovered.snapshot is malformed
    assert recovered.emitted_intents == ()


def test_contract_has_no_effectful_import_or_call_boundary() -> None:
    source_path = ROOT / "python-api" / "scraping" / "cross_store_saga_contract.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported_roots <= {"__future__", "dataclasses", "enum", "hashlib", "json", "re", "typing", "uuid"}
    assert not ({"asyncio", "httpx", "requests", "socket", "sqlite3", "subprocess", "threading", "time"} & imported_roots)
    assert "Thread(" not in source
    assert "connect(" not in source
    assert "urlopen(" not in source

"""Pure Phase 3I cross-store saga/outbox contract.

This module deliberately has no database, network, subprocess, thread, clock,
or random-number dependency.  It only validates immutable inputs and returns
effect *intents*.  Adapters that may eventually persist or execute an intent
are outside the Phase 3I boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Final


SCHEMA_VERSION: Final = 1
SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
REASON_CODE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
TERMINAL_STATES: Final = frozenset(
    {
        "succeeded",
        "compensated",
        "failed_terminal",
        "manual_intervention",
    }
)
_RECOVERY_NAMESPACE: Final = uuid.UUID("305c50d4-b94c-44da-afb6-1046fc90c278")


class SagaState(str, Enum):
    RESERVE_PENDING = "reserve_pending"
    LOCAL_PREPARE_PENDING = "local_prepare_pending"
    CONSUME_PENDING = "consume_pending"
    DISPATCH_PENDING = "dispatch_pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    COMPENSATION_PENDING = "compensation_pending"
    COMPENSATED = "compensated"
    FAILED_TERMINAL = "failed_terminal"
    MANUAL_INTERVENTION = "manual_intervention"


class EffectAction(str, Enum):
    RESERVE_REVIEW = "reserve_review"
    PREPARE_LOCAL_TRANSACTION = "prepare_local_transaction"
    CONSUME_RESERVATION = "consume_reservation"
    DISPATCH_WORKER = "dispatch_worker"
    RELEASE_RESERVATION = "release_reservation"


class EventKind(str, Enum):
    RESERVATION_GRANTED = "reservation_granted"
    RESERVATION_REJECTED = "reservation_rejected"
    LOCAL_PREPARE_SUCCEEDED = "local_prepare_succeeded"
    LOCAL_PREPARE_FAILED = "local_prepare_failed"
    RESERVATION_EXPIRED = "reservation_expired"
    RESERVATION_CONSUMED = "reservation_consumed"
    CONSUME_REJECTED = "consume_rejected"
    WORKER_LEASE_GRANTED = "worker_lease_granted"
    WORKER_LEASE_EXPIRED = "worker_lease_expired"
    WORKER_SUCCEEDED = "worker_succeeded"
    WORKER_FAILED = "worker_failed"
    RELEASE_CONFIRMED = "release_confirmed"
    SAFETY_VIOLATION = "safety_violation"


def _canonical_json_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_uuid(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name}-invalid")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{field_name}-invalid") from exc
    if value != canonical:
        raise ValueError(f"{field_name}-not-canonical")
    return canonical


def _positive_int(value: object) -> bool:
    return type(value) is int and value >= 1


def _epoch(value: object) -> bool:
    return type(value) is int and value >= 0


def _reason_code(value: object) -> bool:
    return isinstance(value, str) and REASON_CODE_PATTERN.fullmatch(value) is not None


@dataclass(frozen=True)
class SagaBinding:
    """Immutable separation of review, execution, and combined bindings."""

    operation_id: str
    review_id: str
    review_version: int
    owner_user_id: str
    job_id: str
    request_hash: str
    review_binding_hash: str = field(init=False)
    execution_binding_hash: str = field(init=False)
    binding_hash: str = field(init=False)

    def __post_init__(self) -> None:
        operation_id = _canonical_uuid(self.operation_id, "operation-id")
        review_id = _canonical_uuid(self.review_id, "review-id")
        owner_user_id = _canonical_uuid(self.owner_user_id, "owner-user-id")
        job_id = _canonical_uuid(self.job_id, "job-id")
        if not _positive_int(self.review_version):
            raise ValueError("review-version-invalid")
        if not isinstance(self.request_hash, str) or SHA256_PATTERN.fullmatch(self.request_hash) is None:
            raise ValueError("request-hash-invalid")

        review_hash = _canonical_json_hash(
            {
                "owner_user_id": owner_user_id,
                "request_hash": self.request_hash,
                "review_id": review_id,
                "review_version": self.review_version,
                "schema_version": SCHEMA_VERSION,
            }
        )
        execution_hash = _canonical_json_hash(
            {
                "job_id": job_id,
                "operation_id": operation_id,
                "owner_user_id": owner_user_id,
                "request_hash": self.request_hash,
                "schema_version": SCHEMA_VERSION,
            }
        )
        binding_hash = _canonical_json_hash(
            {
                "execution_binding_hash": execution_hash,
                "review_binding_hash": review_hash,
                "schema_version": SCHEMA_VERSION,
            }
        )
        object.__setattr__(self, "review_binding_hash", review_hash)
        object.__setattr__(self, "execution_binding_hash", execution_hash)
        object.__setattr__(self, "binding_hash", binding_hash)


def _validate_binding(value: object) -> tuple[str, ...]:
    if not isinstance(value, SagaBinding):
        return ("binding-invalid",)

    errors: list[str] = []
    identity_valid = True
    for field_name, code in (
        ("operation_id", "binding-operation-id-invalid"),
        ("review_id", "binding-review-id-invalid"),
        ("owner_user_id", "binding-owner-user-id-invalid"),
        ("job_id", "binding-job-id-invalid"),
    ):
        try:
            _canonical_uuid(getattr(value, field_name), field_name.replace("_", "-"))
        except (ValueError, AttributeError, TypeError):
            errors.append(code)
            identity_valid = False
    review_version = getattr(value, "review_version", None)
    request_hash = getattr(value, "request_hash", None)
    if not _positive_int(review_version):
        errors.append("binding-review-version-invalid")
        identity_valid = False
    if not isinstance(request_hash, str) or SHA256_PATTERN.fullmatch(request_hash) is None:
        errors.append("binding-request-hash-invalid")
        identity_valid = False

    for field_name, code in (
        ("review_binding_hash", "review-binding-hash-invalid"),
        ("execution_binding_hash", "execution-binding-hash-invalid"),
        ("binding_hash", "binding-hash-invalid"),
    ):
        candidate = getattr(value, field_name, None)
        if not isinstance(candidate, str) or SHA256_PATTERN.fullmatch(candidate) is None:
            errors.append(code)

    if identity_valid:
        try:
            expected = SagaBinding(
                operation_id=getattr(value, "operation_id", None),
                review_id=getattr(value, "review_id", None),
                review_version=review_version,
                owner_user_id=getattr(value, "owner_user_id", None),
                job_id=getattr(value, "job_id", None),
                request_hash=request_hash,
            )
        except (ValueError, TypeError, AttributeError):
            errors.append("binding-recalculation-failed")
        else:
            if getattr(value, "review_binding_hash", None) != expected.review_binding_hash:
                errors.append("review-binding-hash-mismatch")
            if getattr(value, "execution_binding_hash", None) != expected.execution_binding_hash:
                errors.append("execution-binding-hash-mismatch")
            if getattr(value, "binding_hash", None) != expected.binding_hash:
                errors.append("binding-hash-mismatch")
    return tuple(dict.fromkeys(errors))


@dataclass(frozen=True)
class EffectIntent:
    """A durable-outbox candidate; constructing it performs no effect."""

    action: EffectAction
    intent_id: str
    sequence: int
    binding_hash: str
    operation_id: str
    review_id: str
    job_id: str
    reservation_id: str | None = None
    minimum_worker_fencing_token: int | None = None


@dataclass(frozen=True)
class AppliedEvent:
    event_id: str
    event_hash: str


@dataclass(frozen=True)
class SagaSnapshot:
    schema_version: int
    binding: SagaBinding
    state: SagaState
    version: int
    next_intent_sequence: int
    pending_intent: EffectIntent | None
    reservation_id: str | None = None
    reservation_fencing_token: int | None = None
    reservation_expires_at_epoch: int | None = None
    consume_receipt_hash: str | None = None
    worker_fencing_token: int | None = None
    worker_lease_expires_at_epoch: int | None = None
    terminal_code: str | None = None
    applied_events: tuple[AppliedEvent, ...] = ()

    @property
    def terminal(self) -> bool:
        return isinstance(self.state, SagaState) and self.state.value in TERMINAL_STATES


@dataclass(frozen=True)
class SagaEvent:
    kind: EventKind | str
    event_id: str
    binding_hash: str
    intent_id: str | None = None
    observed_at_epoch: int = 0
    reservation_id: str | None = None
    reservation_fencing_token: int | None = None
    reservation_expires_at_epoch: int | None = None
    consume_receipt_hash: str | None = None
    worker_fencing_token: int | None = None
    worker_lease_expires_at_epoch: int | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class TransitionResult:
    accepted: bool
    duplicate: bool
    snapshot: SagaSnapshot
    emitted_intents: tuple[EffectIntent, ...]
    failure_code: str | None


_PENDING_ACTION = {
    SagaState.RESERVE_PENDING: EffectAction.RESERVE_REVIEW,
    SagaState.LOCAL_PREPARE_PENDING: EffectAction.PREPARE_LOCAL_TRANSACTION,
    SagaState.CONSUME_PENDING: EffectAction.CONSUME_RESERVATION,
    SagaState.DISPATCH_PENDING: EffectAction.DISPATCH_WORKER,
    SagaState.COMPENSATION_PENDING: EffectAction.RELEASE_RESERVATION,
}


def _intent_id(binding_hash: str, sequence: int, action: EffectAction) -> str:
    return _canonical_json_hash(
        {
            "action": action.value,
            "binding_hash": binding_hash,
            "sequence": sequence,
        }
    )


def _make_intent(snapshot: SagaSnapshot, action: EffectAction) -> EffectIntent:
    sequence = snapshot.next_intent_sequence
    return EffectIntent(
        action=action,
        intent_id=_intent_id(snapshot.binding.binding_hash, sequence, action),
        sequence=sequence,
        binding_hash=snapshot.binding.binding_hash,
        operation_id=snapshot.binding.operation_id,
        review_id=snapshot.binding.review_id,
        job_id=snapshot.binding.job_id,
        reservation_id=snapshot.reservation_id,
        minimum_worker_fencing_token=(snapshot.worker_fencing_token or 0) + 1
        if action is EffectAction.DISPATCH_WORKER
        else None,
    )


def _reject(snapshot: SagaSnapshot, code: str) -> TransitionResult:
    return TransitionResult(False, False, snapshot, (), code)


def _accept_unchanged(snapshot: SagaSnapshot, *, duplicate: bool = False) -> TransitionResult:
    return TransitionResult(True, duplicate, snapshot, (), None)


def _event_kind_value(kind: EventKind | str) -> str:
    return kind.value if isinstance(kind, EventKind) else str(kind)


def _event_hash(event: SagaEvent) -> str:
    return _canonical_json_hash(
        {
            "binding_hash": event.binding_hash,
            "consume_receipt_hash": event.consume_receipt_hash,
            "event_id": event.event_id,
            "intent_id": event.intent_id,
            "kind": _event_kind_value(event.kind),
            "observed_at_epoch": event.observed_at_epoch,
            "reason_code": event.reason_code,
            "reservation_expires_at_epoch": event.reservation_expires_at_epoch,
            "reservation_fencing_token": event.reservation_fencing_token,
            "reservation_id": event.reservation_id,
            "worker_fencing_token": event.worker_fencing_token,
            "worker_lease_expires_at_epoch": event.worker_lease_expires_at_epoch,
        }
    )


def _event_payload_is_exact(event: SagaEvent, allowed: frozenset[str]) -> bool:
    optional = {
        "intent_id": event.intent_id,
        "reservation_id": event.reservation_id,
        "reservation_fencing_token": event.reservation_fencing_token,
        "reservation_expires_at_epoch": event.reservation_expires_at_epoch,
        "consume_receipt_hash": event.consume_receipt_hash,
        "worker_fencing_token": event.worker_fencing_token,
        "worker_lease_expires_at_epoch": event.worker_lease_expires_at_epoch,
        "reason_code": event.reason_code,
    }
    return all(name in allowed or value is None for name, value in optional.items())


def _correlates_pending(snapshot: SagaSnapshot, event: SagaEvent) -> bool:
    return snapshot.pending_intent is not None and event.intent_id == snapshot.pending_intent.intent_id


def _transition(
    snapshot: SagaSnapshot,
    event: SagaEvent,
    *,
    state: SagaState,
    action: EffectAction | None = None,
    **changes: object,
) -> TransitionResult:
    next_snapshot = replace(
        snapshot,
        state=state,
        version=snapshot.version + 1,
        pending_intent=None,
        applied_events=snapshot.applied_events
        + (AppliedEvent(event.event_id, _event_hash(event)),),
        **changes,
    )
    emitted: tuple[EffectIntent, ...] = ()
    if action is not None:
        intent = _make_intent(next_snapshot, action)
        next_snapshot = replace(
            next_snapshot,
            pending_intent=intent,
            next_intent_sequence=next_snapshot.next_intent_sequence + 1,
        )
        emitted = (intent,)
    errors = validate_snapshot(next_snapshot)
    if errors:
        return _reject(snapshot, f"transition-produced-invalid-snapshot:{errors[0]}")
    return TransitionResult(True, False, next_snapshot, emitted, None)


def create_saga(binding: SagaBinding) -> TransitionResult:
    """Create a saga and emit only the initial reservation intent."""

    binding_errors = _validate_binding(binding)
    if binding_errors:
        invalid_snapshot = SagaSnapshot(
            schema_version=SCHEMA_VERSION,
            binding=binding,  # type: ignore[arg-type]
            state=SagaState.RESERVE_PENDING,
            version=1,
            next_intent_sequence=1,
            pending_intent=None,
        )
        return _reject(invalid_snapshot, binding_errors[0])

    empty = SagaSnapshot(
        schema_version=SCHEMA_VERSION,
        binding=binding,
        state=SagaState.RESERVE_PENDING,
        version=1,
        next_intent_sequence=1,
        pending_intent=None,
    )
    intent = _make_intent(empty, EffectAction.RESERVE_REVIEW)
    snapshot = replace(empty, pending_intent=intent, next_intent_sequence=2)
    errors = validate_snapshot(snapshot)
    if errors:  # defensive: valid SagaBinding should make this unreachable
        return _reject(snapshot, f"initial-snapshot-invalid:{errors[0]}")
    return TransitionResult(True, False, snapshot, (intent,), None)


def validate_snapshot(snapshot: SagaSnapshot) -> tuple[str, ...]:
    """Return stable failure codes; never repair or execute a malformed state."""

    if not isinstance(snapshot, SagaSnapshot):
        return ("snapshot-type-invalid",)

    errors: list[str] = []
    binding_errors = _validate_binding(snapshot.binding)
    binding_valid = not binding_errors
    errors.extend(binding_errors)
    if snapshot.schema_version != SCHEMA_VERSION:
        errors.append("schema-version-invalid")
    if not _positive_int(snapshot.version):
        errors.append("version-invalid")
    if not _positive_int(snapshot.next_intent_sequence):
        errors.append("next-intent-sequence-invalid")
    state_valid = isinstance(snapshot.state, SagaState)
    if not state_valid:
        errors.append("state-invalid")

    expected_action = _PENDING_ACTION.get(snapshot.state) if state_valid else None
    if state_valid and expected_action is None:
        if snapshot.pending_intent is not None:
            errors.append("unexpected-pending-intent")
    elif state_valid and snapshot.pending_intent is None:
        errors.append("pending-intent-required")
    elif state_valid and not isinstance(snapshot.pending_intent, EffectIntent):
        errors.append("pending-intent-invalid")
    elif state_valid:
        assert isinstance(snapshot.pending_intent, EffectIntent)
        intent = snapshot.pending_intent
        action_valid = isinstance(intent.action, EffectAction)
        if not action_valid:
            errors.append("pending-intent-action-invalid")
        elif intent.action is not expected_action:
            errors.append("pending-intent-action-mismatch")
        intent_binding_hash_valid = (
            isinstance(intent.binding_hash, str)
            and SHA256_PATTERN.fullmatch(intent.binding_hash) is not None
        )
        if not intent_binding_hash_valid:
            errors.append("pending-intent-binding-hash-invalid")
        elif binding_valid and intent.binding_hash != snapshot.binding.binding_hash:
            errors.append("pending-intent-binding-mismatch")
        if binding_valid and (intent.operation_id, intent.review_id, intent.job_id) != (
            snapshot.binding.operation_id,
            snapshot.binding.review_id,
            snapshot.binding.job_id,
        ):
            errors.append("pending-intent-identity-mismatch")
        sequence_valid = _positive_int(intent.sequence)
        if not sequence_valid or (
            _positive_int(snapshot.next_intent_sequence)
            and intent.sequence >= snapshot.next_intent_sequence
        ):
            errors.append("pending-intent-sequence-invalid")
        intent_id_valid = isinstance(intent.intent_id, str) and SHA256_PATTERN.fullmatch(intent.intent_id) is not None
        if not intent_id_valid:
            errors.append("pending-intent-id-invalid")
        elif action_valid and intent_binding_hash_valid and sequence_valid:
            if intent.intent_id != _intent_id(intent.binding_hash, intent.sequence, intent.action):
                errors.append("pending-intent-id-invalid")
        if expected_action is EffectAction.RESERVE_REVIEW:
            if intent.reservation_id is not None or intent.minimum_worker_fencing_token is not None:
                errors.append("reserve-intent-payload-invalid")
        elif expected_action in {
            EffectAction.PREPARE_LOCAL_TRANSACTION,
            EffectAction.CONSUME_RESERVATION,
            EffectAction.RELEASE_RESERVATION,
        }:
            if intent.reservation_id != snapshot.reservation_id or intent.minimum_worker_fencing_token is not None:
                errors.append("reservation-intent-payload-invalid")
        elif expected_action is EffectAction.DISPATCH_WORKER:
            current_fence = snapshot.worker_fencing_token if _positive_int(snapshot.worker_fencing_token) else 0
            expected_floor = current_fence + 1
            if (
                intent.reservation_id != snapshot.reservation_id
                or not _positive_int(intent.minimum_worker_fencing_token)
                or intent.minimum_worker_fencing_token != expected_floor
            ):
                errors.append("dispatch-intent-fence-invalid")

    reservation_states = {
        SagaState.LOCAL_PREPARE_PENDING,
        SagaState.CONSUME_PENDING,
        SagaState.DISPATCH_PENDING,
        SagaState.RUNNING,
        SagaState.SUCCEEDED,
        SagaState.COMPENSATION_PENDING,
        SagaState.COMPENSATED,
    }
    reservation_required = state_valid and (
        snapshot.state in reservation_states
        or (
            snapshot.state is SagaState.FAILED_TERMINAL
            and snapshot.terminal_code == "worker-failed"
        )
    )
    if reservation_required:
        try:
            _canonical_uuid(snapshot.reservation_id, "reservation-id")
        except ValueError:
            errors.append("reservation-id-invalid")
        if not _positive_int(snapshot.reservation_fencing_token):
            errors.append("reservation-fencing-token-invalid")
        if not _epoch(snapshot.reservation_expires_at_epoch):
            errors.append("reservation-expiry-invalid")
    elif state_valid and any(
        value is not None
        for value in (
            snapshot.reservation_id,
            snapshot.reservation_fencing_token,
            snapshot.reservation_expires_at_epoch,
        )
    ):
        errors.append("unexpected-reservation-binding")

    consumed_states = {SagaState.DISPATCH_PENDING, SagaState.RUNNING, SagaState.SUCCEEDED}
    if state_valid and snapshot.state in consumed_states:
        if not isinstance(snapshot.consume_receipt_hash, str) or SHA256_PATTERN.fullmatch(snapshot.consume_receipt_hash) is None:
            errors.append("consume-receipt-invalid")
    elif state_valid and snapshot.consume_receipt_hash is not None:
        # A terminal worker failure is the one non-success state that remains consumed.
        if not (
            snapshot.state is SagaState.FAILED_TERMINAL
            and snapshot.terminal_code == "worker-failed"
            and isinstance(snapshot.consume_receipt_hash, str)
            and SHA256_PATTERN.fullmatch(snapshot.consume_receipt_hash) is not None
        ):
            errors.append("unexpected-consume-receipt")

    worker_states = {SagaState.RUNNING, SagaState.SUCCEEDED}
    if state_valid and snapshot.state in worker_states:
        if not _positive_int(snapshot.worker_fencing_token):
            errors.append("worker-fencing-token-invalid")
        if not _epoch(snapshot.worker_lease_expires_at_epoch):
            errors.append("worker-lease-expiry-invalid")
    elif state_valid and snapshot.state is SagaState.FAILED_TERMINAL and snapshot.terminal_code == "worker-failed":
        if not _positive_int(snapshot.worker_fencing_token):
            errors.append("worker-fencing-token-invalid")
        if not _epoch(snapshot.worker_lease_expires_at_epoch):
            errors.append("worker-lease-expiry-invalid")
    elif state_valid and snapshot.state is SagaState.DISPATCH_PENDING:
        if snapshot.worker_lease_expires_at_epoch is not None:
            errors.append("stale-worker-lease-present")
        if snapshot.worker_fencing_token is not None and not _positive_int(snapshot.worker_fencing_token):
            errors.append("worker-fencing-token-invalid")
    elif state_valid and (snapshot.worker_fencing_token is not None or snapshot.worker_lease_expires_at_epoch is not None):
        errors.append("unexpected-worker-lease")

    if state_valid and snapshot.terminal:
        if not _reason_code(snapshot.terminal_code):
            errors.append("terminal-code-invalid")
    elif state_valid and snapshot.state is SagaState.COMPENSATION_PENDING:
        if not _reason_code(snapshot.terminal_code):
            errors.append("compensation-code-invalid")
    elif state_valid and snapshot.terminal_code is not None:
        errors.append("unexpected-terminal-code")

    seen: dict[str, str] = {}
    if not isinstance(snapshot.applied_events, tuple):
        errors.append("applied-events-invalid")
    else:
        for applied in snapshot.applied_events:
            if not isinstance(applied, AppliedEvent):
                errors.append("applied-event-invalid")
                continue
            try:
                _canonical_uuid(applied.event_id, "applied-event-id")
            except ValueError:
                errors.append("applied-event-id-invalid")
                continue
            if not isinstance(applied.event_hash, str) or SHA256_PATTERN.fullmatch(applied.event_hash) is None:
                errors.append("applied-event-hash-invalid")
                continue
            previous = seen.setdefault(applied.event_id, applied.event_hash)
            if previous != applied.event_hash or sum(
                1
                for item in snapshot.applied_events
                if isinstance(item, AppliedEvent) and item.event_id == applied.event_id
            ) > 1:
                errors.append("duplicate-applied-event")
    return tuple(dict.fromkeys(errors))


def _validate_event_base(snapshot: SagaSnapshot, event: SagaEvent) -> str | None:
    if not isinstance(event, SagaEvent):
        return "event-type-invalid"
    if not isinstance(event.kind, (EventKind, str)):
        return "event-kind-invalid"
    try:
        _canonical_uuid(event.event_id, "event-id")
    except ValueError as exc:
        return str(exc)
    if not isinstance(event.binding_hash, str) or SHA256_PATTERN.fullmatch(event.binding_hash) is None:
        return "event-binding-hash-invalid"
    if event.binding_hash != snapshot.binding.binding_hash:
        return "event-binding-mismatch"
    if not _epoch(event.observed_at_epoch):
        return "event-observed-at-invalid"
    try:
        EventKind(_event_kind_value(event.kind))
    except ValueError:
        return "unknown-event-kind"
    if event.intent_id is not None and (
        not isinstance(event.intent_id, str)
        or SHA256_PATTERN.fullmatch(event.intent_id) is None
    ):
        return "event-intent-id-invalid"
    if event.reservation_id is not None:
        try:
            _canonical_uuid(event.reservation_id, "event-reservation-id")
        except ValueError:
            return "event-reservation-id-invalid"
    if event.reservation_fencing_token is not None and not _positive_int(event.reservation_fencing_token):
        return "event-reservation-fencing-token-invalid"
    if event.reservation_expires_at_epoch is not None and not _epoch(event.reservation_expires_at_epoch):
        return "event-reservation-expiry-invalid"
    if event.consume_receipt_hash is not None and (
        not isinstance(event.consume_receipt_hash, str)
        or SHA256_PATTERN.fullmatch(event.consume_receipt_hash) is None
    ):
        return "event-consume-receipt-hash-invalid"
    if event.worker_fencing_token is not None and not _positive_int(event.worker_fencing_token):
        return "event-worker-fencing-token-invalid"
    if event.worker_lease_expires_at_epoch is not None and not _epoch(event.worker_lease_expires_at_epoch):
        return "event-worker-lease-expiry-invalid"
    if event.reason_code is not None and not _reason_code(event.reason_code):
        return "event-reason-code-invalid"
    return None


def apply_event(snapshot: SagaSnapshot, event: SagaEvent) -> TransitionResult:
    """Apply one fact, rejecting unknown/unsafe input without emitting effects."""

    snapshot_errors = validate_snapshot(snapshot)
    if snapshot_errors:
        return _reject(snapshot, f"snapshot-invalid:{snapshot_errors[0]}")
    if not isinstance(event, SagaEvent):
        return _reject(snapshot, "event-type-invalid")
    event_error = _validate_event_base(snapshot, event)
    if event_error:
        return _reject(snapshot, event_error)

    event_hash = _event_hash(event)
    for applied in snapshot.applied_events:
        if applied.event_id == event.event_id:
            if applied.event_hash == event_hash:
                return _accept_unchanged(snapshot, duplicate=True)
            return _reject(snapshot, "event-id-payload-conflict")

    if snapshot.terminal:
        return _reject(snapshot, "terminal-state-immutable")

    kind = EventKind(_event_kind_value(event.kind))
    if kind is EventKind.RESERVATION_GRANTED:
        allowed = frozenset({"intent_id", "reservation_id", "reservation_fencing_token", "reservation_expires_at_epoch"})
        try:
            reservation_id = _canonical_uuid(event.reservation_id, "reservation-id")
        except ValueError:
            reservation_id = ""
        if (
            snapshot.state is not SagaState.RESERVE_PENDING
            or not _correlates_pending(snapshot, event)
            or not _event_payload_is_exact(event, allowed)
            or not reservation_id
            or not _positive_int(event.reservation_fencing_token)
            or not _epoch(event.reservation_expires_at_epoch)
            or event.reservation_expires_at_epoch <= event.observed_at_epoch
        ):
            return _reject(snapshot, "reservation-grant-invalid")
        return _transition(
            snapshot,
            event,
            state=SagaState.LOCAL_PREPARE_PENDING,
            action=EffectAction.PREPARE_LOCAL_TRANSACTION,
            reservation_id=reservation_id,
            reservation_fencing_token=event.reservation_fencing_token,
            reservation_expires_at_epoch=event.reservation_expires_at_epoch,
        )

    if kind is EventKind.RESERVATION_REJECTED:
        allowed = frozenset({"intent_id", "reason_code"})
        if (
            snapshot.state is not SagaState.RESERVE_PENDING
            or not _correlates_pending(snapshot, event)
            or not _event_payload_is_exact(event, allowed)
            or not _reason_code(event.reason_code)
        ):
            return _reject(snapshot, "reservation-rejection-invalid")
        return _transition(
            snapshot,
            event,
            state=SagaState.FAILED_TERMINAL,
            terminal_code="reservation-rejected",
        )

    if kind in {EventKind.LOCAL_PREPARE_SUCCEEDED, EventKind.LOCAL_PREPARE_FAILED}:
        allowed = frozenset({"intent_id"}) if kind is EventKind.LOCAL_PREPARE_SUCCEEDED else frozenset({"intent_id", "reason_code"})
        if (
            snapshot.state is not SagaState.LOCAL_PREPARE_PENDING
            or not _correlates_pending(snapshot, event)
            or not _event_payload_is_exact(event, allowed)
            or (kind is EventKind.LOCAL_PREPARE_FAILED and not _reason_code(event.reason_code))
        ):
            return _reject(snapshot, "local-prepare-event-invalid")
        if kind is EventKind.LOCAL_PREPARE_SUCCEEDED:
            return _transition(
                snapshot,
                event,
                state=SagaState.CONSUME_PENDING,
                action=EffectAction.CONSUME_RESERVATION,
            )
        return _transition(
            snapshot,
            event,
            state=SagaState.COMPENSATION_PENDING,
            action=EffectAction.RELEASE_RESERVATION,
            terminal_code="local-prepare-failed",
        )

    if kind is EventKind.RESERVATION_EXPIRED:
        if (
            snapshot.state not in {SagaState.LOCAL_PREPARE_PENDING, SagaState.CONSUME_PENDING}
            or not _event_payload_is_exact(event, frozenset())
            or snapshot.reservation_expires_at_epoch is None
            or event.observed_at_epoch < snapshot.reservation_expires_at_epoch
        ):
            return _reject(snapshot, "reservation-expiry-invalid")
        return _transition(
            snapshot,
            event,
            state=SagaState.COMPENSATION_PENDING,
            action=EffectAction.RELEASE_RESERVATION,
            terminal_code="reservation-expired",
        )

    if kind in {EventKind.RESERVATION_CONSUMED, EventKind.CONSUME_REJECTED}:
        allowed = frozenset({"intent_id", "consume_receipt_hash"}) if kind is EventKind.RESERVATION_CONSUMED else frozenset({"intent_id", "reason_code"})
        if (
            snapshot.state is not SagaState.CONSUME_PENDING
            or not _correlates_pending(snapshot, event)
            or not _event_payload_is_exact(event, allowed)
            or snapshot.reservation_expires_at_epoch is None
            or event.observed_at_epoch >= snapshot.reservation_expires_at_epoch
        ):
            return _reject(snapshot, "consume-event-invalid")
        if kind is EventKind.RESERVATION_CONSUMED:
            if not isinstance(event.consume_receipt_hash, str) or SHA256_PATTERN.fullmatch(event.consume_receipt_hash) is None:
                return _reject(snapshot, "consume-receipt-invalid")
            return _transition(
                snapshot,
                event,
                state=SagaState.DISPATCH_PENDING,
                action=EffectAction.DISPATCH_WORKER,
                consume_receipt_hash=event.consume_receipt_hash,
            )
        if not _reason_code(event.reason_code):
            return _reject(snapshot, "consume-rejection-invalid")
        return _transition(
            snapshot,
            event,
            state=SagaState.COMPENSATION_PENDING,
            action=EffectAction.RELEASE_RESERVATION,
            terminal_code="consume-rejected",
        )

    if kind is EventKind.WORKER_LEASE_GRANTED:
        allowed = frozenset({"intent_id", "worker_fencing_token", "worker_lease_expires_at_epoch"})
        floor = snapshot.pending_intent.minimum_worker_fencing_token if snapshot.pending_intent else None
        if (
            snapshot.state is not SagaState.DISPATCH_PENDING
            or not _correlates_pending(snapshot, event)
            or not _event_payload_is_exact(event, allowed)
            or not _positive_int(event.worker_fencing_token)
            or floor is None
            or event.worker_fencing_token < floor
            or not _epoch(event.worker_lease_expires_at_epoch)
            or event.worker_lease_expires_at_epoch <= event.observed_at_epoch
        ):
            return _reject(snapshot, "worker-lease-grant-invalid")
        return _transition(
            snapshot,
            event,
            state=SagaState.RUNNING,
            worker_fencing_token=event.worker_fencing_token,
            worker_lease_expires_at_epoch=event.worker_lease_expires_at_epoch,
        )

    if kind is EventKind.WORKER_LEASE_EXPIRED:
        allowed = frozenset({"worker_fencing_token"})
        if (
            snapshot.state is not SagaState.RUNNING
            or not _event_payload_is_exact(event, allowed)
            or event.worker_fencing_token != snapshot.worker_fencing_token
            or snapshot.worker_lease_expires_at_epoch is None
            or event.observed_at_epoch < snapshot.worker_lease_expires_at_epoch
        ):
            return _reject(snapshot, "worker-lease-expiry-invalid")
        return _transition(
            snapshot,
            event,
            state=SagaState.DISPATCH_PENDING,
            action=EffectAction.DISPATCH_WORKER,
            worker_lease_expires_at_epoch=None,
        )

    if kind in {EventKind.WORKER_SUCCEEDED, EventKind.WORKER_FAILED}:
        allowed = frozenset({"worker_fencing_token"}) if kind is EventKind.WORKER_SUCCEEDED else frozenset({"worker_fencing_token", "reason_code"})
        if (
            snapshot.state is not SagaState.RUNNING
            or not _event_payload_is_exact(event, allowed)
            or event.worker_fencing_token != snapshot.worker_fencing_token
            or snapshot.worker_lease_expires_at_epoch is None
            or event.observed_at_epoch >= snapshot.worker_lease_expires_at_epoch
            or (kind is EventKind.WORKER_FAILED and not _reason_code(event.reason_code))
        ):
            return _reject(snapshot, "worker-result-invalid")
        if kind is EventKind.WORKER_SUCCEEDED:
            return _transition(snapshot, event, state=SagaState.SUCCEEDED, terminal_code="worker-succeeded")
        return _transition(snapshot, event, state=SagaState.FAILED_TERMINAL, terminal_code="worker-failed")

    if kind is EventKind.RELEASE_CONFIRMED:
        if (
            snapshot.state is not SagaState.COMPENSATION_PENDING
            or not _correlates_pending(snapshot, event)
            or not _event_payload_is_exact(event, frozenset({"intent_id"}))
        ):
            return _reject(snapshot, "release-confirmation-invalid")
        return _transition(snapshot, event, state=SagaState.COMPENSATED)

    if kind is EventKind.SAFETY_VIOLATION:
        if not _event_payload_is_exact(event, frozenset({"reason_code"})) or not _reason_code(event.reason_code):
            return _reject(snapshot, "safety-violation-invalid")
        return _transition(
            snapshot,
            event,
            state=SagaState.MANUAL_INTERVENTION,
            reservation_id=None,
            reservation_fencing_token=None,
            reservation_expires_at_epoch=None,
            consume_receipt_hash=None,
            worker_fencing_token=None,
            worker_lease_expires_at_epoch=None,
            terminal_code="safety-violation",
        )

    return _reject(snapshot, "unknown-event-kind")


def _recovery_event_id(snapshot: SagaSnapshot, kind: EventKind, observed_at_epoch: int) -> str:
    return str(
        uuid.uuid5(
            _RECOVERY_NAMESPACE,
            f"{snapshot.binding.binding_hash}:{snapshot.version}:{kind.value}:{observed_at_epoch}",
        )
    )


def recover(snapshot: SagaSnapshot, observed_at_epoch: int) -> TransitionResult:
    """Pure recovery: re-emit one stable intent or derive an expiry event."""

    errors = validate_snapshot(snapshot)
    if errors:
        return _reject(snapshot, f"snapshot-invalid:{errors[0]}")
    if not _epoch(observed_at_epoch):
        return _reject(snapshot, "recovery-observed-at-invalid")
    if snapshot.terminal:
        return _accept_unchanged(snapshot)

    if (
        snapshot.state in {SagaState.LOCAL_PREPARE_PENDING, SagaState.CONSUME_PENDING}
        and snapshot.reservation_expires_at_epoch is not None
        and observed_at_epoch >= snapshot.reservation_expires_at_epoch
    ):
        return apply_event(
            snapshot,
            SagaEvent(
                kind=EventKind.RESERVATION_EXPIRED,
                event_id=_recovery_event_id(snapshot, EventKind.RESERVATION_EXPIRED, observed_at_epoch),
                binding_hash=snapshot.binding.binding_hash,
                observed_at_epoch=observed_at_epoch,
            ),
        )
    if (
        snapshot.state is SagaState.RUNNING
        and snapshot.worker_lease_expires_at_epoch is not None
        and observed_at_epoch >= snapshot.worker_lease_expires_at_epoch
    ):
        return apply_event(
            snapshot,
            SagaEvent(
                kind=EventKind.WORKER_LEASE_EXPIRED,
                event_id=_recovery_event_id(snapshot, EventKind.WORKER_LEASE_EXPIRED, observed_at_epoch),
                binding_hash=snapshot.binding.binding_hash,
                observed_at_epoch=observed_at_epoch,
                worker_fencing_token=snapshot.worker_fencing_token,
            ),
        )
    if snapshot.pending_intent is not None:
        return TransitionResult(True, False, snapshot, (snapshot.pending_intent,), None)
    return _accept_unchanged(snapshot)


__all__ = [
    "AppliedEvent",
    "EffectAction",
    "EffectIntent",
    "EventKind",
    "SagaBinding",
    "SagaEvent",
    "SagaSnapshot",
    "SagaState",
    "TransitionResult",
    "apply_event",
    "create_saga",
    "recover",
    "validate_snapshot",
]

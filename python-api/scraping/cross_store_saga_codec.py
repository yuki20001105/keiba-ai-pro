"""Exact canonical codec for the Phase 3I saga contract.

The durable Phase 3J layer persists only values which can be losslessly
round-tripped and revalidated by the pure Phase 3I contract.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Mapping

from .cross_store_saga_contract import (
    SCHEMA_VERSION,
    AppliedEvent,
    EffectAction,
    EffectIntent,
    EventKind,
    SagaBinding,
    SagaEvent,
    SagaSnapshot,
    SagaState,
    validate_snapshot,
)


class SagaCodecError(ValueError):
    """Stable fail-closed codec error."""


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


_BINDING_KEYS = frozenset(
    {
        "operation_id",
        "review_id",
        "review_version",
        "owner_user_id",
        "job_id",
        "request_hash",
        "review_binding_hash",
        "execution_binding_hash",
        "binding_hash",
    }
)
_INTENT_KEYS = frozenset(
    {
        "action",
        "intent_id",
        "sequence",
        "binding_hash",
        "operation_id",
        "review_id",
        "job_id",
        "reservation_id",
        "minimum_worker_fencing_token",
    }
)
_APPLIED_EVENT_KEYS = frozenset({"event_id", "event_hash"})
_SNAPSHOT_KEYS = frozenset(
    {
        "schema_version",
        "binding",
        "state",
        "version",
        "next_intent_sequence",
        "pending_intent",
        "reservation_id",
        "reservation_fencing_token",
        "reservation_expires_at_epoch",
        "consume_receipt_hash",
        "worker_fencing_token",
        "worker_lease_expires_at_epoch",
        "terminal_code",
        "applied_events",
    }
)
_EVENT_KEYS = frozenset(
    {
        "kind",
        "event_id",
        "binding_hash",
        "intent_id",
        "observed_at_epoch",
        "reservation_id",
        "reservation_fencing_token",
        "reservation_expires_at_epoch",
        "consume_receipt_hash",
        "worker_fencing_token",
        "worker_lease_expires_at_epoch",
        "reason_code",
    }
)


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise SagaCodecError("canonical-json-invalid") from exc


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _object(value: object, keys: frozenset[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != keys:
        raise SagaCodecError(code)
    return value


def _parse_json(payload: object, code: str) -> object:
    if not isinstance(payload, str) or not payload:
        raise SagaCodecError(code)
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise SagaCodecError(code) from exc
    if _canonical_json(value) != payload:
        raise SagaCodecError("non-canonical-json")
    return value


def binding_to_object(binding: SagaBinding) -> dict[str, object]:
    return {
        "operation_id": binding.operation_id,
        "review_id": binding.review_id,
        "review_version": binding.review_version,
        "owner_user_id": binding.owner_user_id,
        "job_id": binding.job_id,
        "request_hash": binding.request_hash,
        "review_binding_hash": binding.review_binding_hash,
        "execution_binding_hash": binding.execution_binding_hash,
        "binding_hash": binding.binding_hash,
    }


def _decode_binding(value: object) -> SagaBinding:
    item = _object(value, _BINDING_KEYS, "binding-schema-invalid")
    try:
        binding = SagaBinding(
            operation_id=item["operation_id"],
            review_id=item["review_id"],
            review_version=item["review_version"],
            owner_user_id=item["owner_user_id"],
            job_id=item["job_id"],
            request_hash=item["request_hash"],
        )
    except (TypeError, ValueError) as exc:
        raise SagaCodecError("binding-invalid") from exc
    for field in ("review_binding_hash", "execution_binding_hash", "binding_hash"):
        if item[field] != getattr(binding, field):
            raise SagaCodecError(f"{field.replace('_', '-')}-mismatch")
    return binding


def intent_to_object(intent: EffectIntent) -> dict[str, object]:
    errors = validate_intent(intent)
    if errors:
        raise SagaCodecError(f"intent-invalid:{errors[0]}")
    return {
        "action": intent.action.value,
        "intent_id": intent.intent_id,
        "sequence": intent.sequence,
        "binding_hash": intent.binding_hash,
        "operation_id": intent.operation_id,
        "review_id": intent.review_id,
        "job_id": intent.job_id,
        "reservation_id": intent.reservation_id,
        "minimum_worker_fencing_token": intent.minimum_worker_fencing_token,
    }


def _decode_intent(value: object) -> EffectIntent:
    item = _object(value, _INTENT_KEYS, "intent-schema-invalid")
    try:
        action = EffectAction(item["action"])
        intent = EffectIntent(
            action=action,
            intent_id=item["intent_id"],
            sequence=item["sequence"],
            binding_hash=item["binding_hash"],
            operation_id=item["operation_id"],
            review_id=item["review_id"],
            job_id=item["job_id"],
            reservation_id=item["reservation_id"],
            minimum_worker_fencing_token=item["minimum_worker_fencing_token"],
        )
    except (TypeError, ValueError) as exc:
        raise SagaCodecError("intent-invalid") from exc
    errors = validate_intent(intent)
    if errors:
        raise SagaCodecError(f"intent-invalid:{errors[0]}")
    return intent


def validate_intent(
    intent: object,
    *,
    binding: SagaBinding | None = None,
) -> tuple[str, ...]:
    """Validate a standalone durable intent and optional immutable binding."""

    if not isinstance(intent, EffectIntent):
        return ("intent-type-invalid",)
    errors: list[str] = []
    if not isinstance(intent.action, EffectAction):
        errors.append("intent-action-invalid")
    if type(intent.sequence) is not int or intent.sequence < 1:
        errors.append("intent-sequence-invalid")
    for field_name in ("binding_hash", "intent_id"):
        value = getattr(intent, field_name)
        if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
            errors.append(f"intent-{field_name.replace('_', '-')}-invalid")
    for field_name in ("operation_id", "review_id", "job_id"):
        value = getattr(intent, field_name)
        try:
            if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            errors.append(f"intent-{field_name.replace('_', '-')}-invalid")
    if (
        isinstance(intent.action, EffectAction)
        and type(intent.sequence) is int
        and intent.sequence >= 1
        and isinstance(intent.binding_hash, str)
        and _SHA256_PATTERN.fullmatch(intent.binding_hash) is not None
    ):
        expected_id = canonical_sha256(
            {
                "action": intent.action.value,
                "binding_hash": intent.binding_hash,
                "sequence": intent.sequence,
            }
        )
        if intent.intent_id != expected_id:
            errors.append("intent-id-mismatch")
    if binding is not None:
        if not isinstance(binding, SagaBinding):
            errors.append("intent-binding-invalid")
        elif (
            intent.binding_hash != binding.binding_hash
            or intent.operation_id != binding.operation_id
            or intent.review_id != binding.review_id
            or intent.job_id != binding.job_id
        ):
            errors.append("intent-binding-mismatch")

    reservation_required = intent.action in {
        EffectAction.PREPARE_LOCAL_TRANSACTION,
        EffectAction.CONSUME_RESERVATION,
        EffectAction.DISPATCH_WORKER,
        EffectAction.RELEASE_RESERVATION,
    }
    if reservation_required:
        try:
            if not isinstance(intent.reservation_id, str) or str(uuid.UUID(intent.reservation_id)) != intent.reservation_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            errors.append("intent-reservation-id-invalid")
    elif intent.reservation_id is not None:
        errors.append("intent-unexpected-reservation-id")
    if intent.action is EffectAction.DISPATCH_WORKER:
        if type(intent.minimum_worker_fencing_token) is not int or intent.minimum_worker_fencing_token < 1:
            errors.append("intent-worker-fence-invalid")
    elif intent.minimum_worker_fencing_token is not None:
        errors.append("intent-unexpected-worker-fence")
    return tuple(dict.fromkeys(errors))


def encode_intent(intent: EffectIntent) -> str:
    return _canonical_json(intent_to_object(intent))


def decode_intent(payload: str, *, expected_hash: str | None = None) -> EffectIntent:
    value = _parse_json(payload, "intent-json-invalid")
    if expected_hash is not None and canonical_sha256(value) != expected_hash:
        raise SagaCodecError("intent-hash-mismatch")
    intent = _decode_intent(value)
    if encode_intent(intent) != payload:
        raise SagaCodecError("intent-roundtrip-mismatch")
    return intent


def snapshot_to_object(snapshot: SagaSnapshot) -> dict[str, object]:
    errors = validate_snapshot(snapshot)
    if errors:
        raise SagaCodecError(f"snapshot-invalid:{errors[0]}")
    return {
        "schema_version": snapshot.schema_version,
        "binding": binding_to_object(snapshot.binding),
        "state": snapshot.state.value,
        "version": snapshot.version,
        "next_intent_sequence": snapshot.next_intent_sequence,
        "pending_intent": None if snapshot.pending_intent is None else intent_to_object(snapshot.pending_intent),
        "reservation_id": snapshot.reservation_id,
        "reservation_fencing_token": snapshot.reservation_fencing_token,
        "reservation_expires_at_epoch": snapshot.reservation_expires_at_epoch,
        "consume_receipt_hash": snapshot.consume_receipt_hash,
        "worker_fencing_token": snapshot.worker_fencing_token,
        "worker_lease_expires_at_epoch": snapshot.worker_lease_expires_at_epoch,
        "terminal_code": snapshot.terminal_code,
        "applied_events": [
            {"event_id": event.event_id, "event_hash": event.event_hash}
            for event in snapshot.applied_events
        ],
    }


def encode_snapshot(snapshot: SagaSnapshot) -> str:
    return _canonical_json(snapshot_to_object(snapshot))


def snapshot_sha256(snapshot_or_payload: SagaSnapshot | str) -> str:
    payload = encode_snapshot(snapshot_or_payload) if isinstance(snapshot_or_payload, SagaSnapshot) else snapshot_or_payload
    value = _parse_json(payload, "snapshot-json-invalid")
    return canonical_sha256(value)


def decode_snapshot(payload: str, *, expected_hash: str | None = None) -> SagaSnapshot:
    value = _parse_json(payload, "snapshot-json-invalid")
    if expected_hash is not None and canonical_sha256(value) != expected_hash:
        raise SagaCodecError("snapshot-hash-mismatch")
    item = _object(value, _SNAPSHOT_KEYS, "snapshot-schema-invalid")
    applied_raw = item["applied_events"]
    if not isinstance(applied_raw, list):
        raise SagaCodecError("applied-events-invalid")
    applied: list[AppliedEvent] = []
    for raw in applied_raw:
        event = _object(raw, _APPLIED_EVENT_KEYS, "applied-event-schema-invalid")
        applied.append(AppliedEvent(event_id=event["event_id"], event_hash=event["event_hash"]))
    try:
        snapshot = SagaSnapshot(
            schema_version=item["schema_version"],
            binding=_decode_binding(item["binding"]),
            state=SagaState(item["state"]),
            version=item["version"],
            next_intent_sequence=item["next_intent_sequence"],
            pending_intent=None if item["pending_intent"] is None else _decode_intent(item["pending_intent"]),
            reservation_id=item["reservation_id"],
            reservation_fencing_token=item["reservation_fencing_token"],
            reservation_expires_at_epoch=item["reservation_expires_at_epoch"],
            consume_receipt_hash=item["consume_receipt_hash"],
            worker_fencing_token=item["worker_fencing_token"],
            worker_lease_expires_at_epoch=item["worker_lease_expires_at_epoch"],
            terminal_code=item["terminal_code"],
            applied_events=tuple(applied),
        )
    except SagaCodecError:
        raise
    except (TypeError, ValueError) as exc:
        raise SagaCodecError("snapshot-fields-invalid") from exc
    errors = validate_snapshot(snapshot)
    if errors:
        raise SagaCodecError(f"snapshot-invalid:{errors[0]}")
    if encode_snapshot(snapshot) != payload:
        raise SagaCodecError("snapshot-roundtrip-mismatch")
    return snapshot


def event_to_object(event: SagaEvent) -> dict[str, object]:
    kind = event.kind.value if isinstance(event.kind, EventKind) else event.kind
    return {
        "kind": kind,
        "event_id": event.event_id,
        "binding_hash": event.binding_hash,
        "intent_id": event.intent_id,
        "observed_at_epoch": event.observed_at_epoch,
        "reservation_id": event.reservation_id,
        "reservation_fencing_token": event.reservation_fencing_token,
        "reservation_expires_at_epoch": event.reservation_expires_at_epoch,
        "consume_receipt_hash": event.consume_receipt_hash,
        "worker_fencing_token": event.worker_fencing_token,
        "worker_lease_expires_at_epoch": event.worker_lease_expires_at_epoch,
        "reason_code": event.reason_code,
    }


def encode_event(event: SagaEvent) -> str:
    return _canonical_json(event_to_object(event))


def event_sha256(event_or_payload: SagaEvent | str) -> str:
    payload = encode_event(event_or_payload) if isinstance(event_or_payload, SagaEvent) else event_or_payload
    return canonical_sha256(_parse_json(payload, "event-json-invalid"))


def decode_event(payload: str, *, expected_hash: str | None = None) -> SagaEvent:
    value = _parse_json(payload, "event-json-invalid")
    if expected_hash is not None and canonical_sha256(value) != expected_hash:
        raise SagaCodecError("event-hash-mismatch")
    item = _object(value, _EVENT_KEYS, "event-schema-invalid")
    try:
        event = SagaEvent(
            kind=EventKind(item["kind"]),
            event_id=item["event_id"],
            binding_hash=item["binding_hash"],
            intent_id=item["intent_id"],
            observed_at_epoch=item["observed_at_epoch"],
            reservation_id=item["reservation_id"],
            reservation_fencing_token=item["reservation_fencing_token"],
            reservation_expires_at_epoch=item["reservation_expires_at_epoch"],
            consume_receipt_hash=item["consume_receipt_hash"],
            worker_fencing_token=item["worker_fencing_token"],
            worker_lease_expires_at_epoch=item["worker_lease_expires_at_epoch"],
            reason_code=item["reason_code"],
        )
    except (TypeError, ValueError) as exc:
        raise SagaCodecError("event-fields-invalid") from exc
    if encode_event(event) != payload:
        raise SagaCodecError("event-roundtrip-mismatch")
    return event

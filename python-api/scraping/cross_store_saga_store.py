"""Disposable SQLite persistence for the Phase 3I saga contract.

This schema is intentionally isolated from every existing job and operational
table.  A store can only be opened with a CI-disposable Phase 3J
configuration whose database path is below the operating-system temp root.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .cross_store_saga_codec import (
    SagaCodecError,
    canonical_sha256,
    decode_intent,
    decode_snapshot,
    encode_event,
    encode_intent,
    encode_snapshot,
    event_sha256,
    snapshot_sha256,
    validate_intent,
)
from .cross_store_saga_contract import (
    EffectIntent,
    EventKind,
    SagaBinding,
    SagaEvent,
    SagaSnapshot,
    SagaState,
    apply_event,
    create_saga,
    recover,
)
from .saga_runtime_config import SagaRuntimeConfig


SCHEMA_SQL = """CREATE TABLE IF NOT EXISTS phase3j_jobs (
    job_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    binding_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL CHECK(created_at_epoch >= 0),
    updated_at_epoch INTEGER NOT NULL CHECK(updated_at_epoch >= 0)
);
CREATE TABLE IF NOT EXISTS phase3j_sagas (
    operation_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES phase3j_jobs(job_id),
    binding_hash TEXT NOT NULL UNIQUE,
    version INTEGER NOT NULL CHECK(version >= 1),
    snapshot_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    updated_at_epoch INTEGER NOT NULL CHECK(updated_at_epoch >= 0)
);
CREATE TABLE IF NOT EXISTS phase3j_saga_events (
    event_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES phase3j_sagas(operation_id),
    event_hash TEXT NOT NULL,
    event_json TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL CHECK(created_at_epoch >= 0)
);
CREATE TABLE IF NOT EXISTS phase3j_outbox (
    intent_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES phase3j_sagas(operation_id),
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    action TEXT NOT NULL,
    binding_hash TEXT NOT NULL,
    intent_json TEXT NOT NULL,
    intent_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','claimed','acknowledged','blocked')),
    lease_owner TEXT,
    lease_expires_at_epoch INTEGER,
    fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(fencing_token >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    settlement_fingerprint TEXT,
    settlement_reason TEXT,
    created_at_epoch INTEGER NOT NULL CHECK(created_at_epoch >= 0),
    updated_at_epoch INTEGER NOT NULL CHECK(updated_at_epoch >= 0),
    UNIQUE(operation_id, sequence)
);
CREATE TRIGGER IF NOT EXISTS phase3j_saga_events_no_update
BEFORE UPDATE ON phase3j_saga_events
BEGIN SELECT RAISE(ABORT, 'phase3j-events-append-only'); END;
CREATE TRIGGER IF NOT EXISTS phase3j_saga_events_no_delete
BEFORE DELETE ON phase3j_saga_events
BEGIN SELECT RAISE(ABORT, 'phase3j-events-append-only'); END;"""


class SagaStoreError(RuntimeError):
    pass


class SagaStoreDisabledError(SagaStoreError):
    pass


class SagaStoreCorruptionError(SagaStoreError):
    pass


class StoreResultCode(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    REJECTED = "rejected"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


class OutboxState(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    ACKNOWLEDGED = "acknowledged"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class OutboxRecord:
    intent: EffectIntent
    state: OutboxState
    lease_owner: str | None
    lease_expires_at_epoch: int | None
    fencing_token: int
    version: int
    settlement_fingerprint: str | None
    settlement_reason: str | None
    updated_at_epoch: int


@dataclass(frozen=True)
class OutboxClaim:
    record: OutboxRecord
    owner: str

    @property
    def intent(self) -> EffectIntent:
        return self.record.intent

    @property
    def fencing_token(self) -> int:
        return self.record.fencing_token

    @property
    def version(self) -> int:
        return self.record.version


@dataclass(frozen=True)
class StoreMutation:
    code: StoreResultCode
    reason_code: str | None = None
    snapshot: SagaSnapshot | None = None
    outbox: OutboxRecord | None = None
    claim: OutboxClaim | None = None
    recovered_count: int = 0


_OWNER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _epoch(value: object, code: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(code)
    return value


def _owner(value: object) -> str:
    if not isinstance(value, str) or _OWNER_PATTERN.fullmatch(value) is None:
        raise ValueError("lease-owner-invalid")
    return value


def _fingerprint(value: object) -> str:
    if not isinstance(value, str) or _HEX64_PATTERN.fullmatch(value) is None:
        raise ValueError("settlement-fingerprint-invalid")
    return value


def _reason(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or not value[0].isalpha()
        or not value.replace("-", "").isalnum()
    ):
        raise ValueError("settlement-reason-invalid")
    return value


class SagaStore:
    """Transactional store for a single disposable SQLite file."""

    def __init__(
        self,
        config: SagaRuntimeConfig,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(config, SagaRuntimeConfig) or not config.executable or config.sqlite_path is None:
            raise SagaStoreDisabledError("phase3j-runtime-disabled")
        self._config = config
        self._path = config.sqlite_path
        self._fault_injector = fault_injector

    @property
    def database_path(self) -> str:
        return str(self._path)

    def _fault(self, checkpoint: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(checkpoint)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._path,
            timeout=self._config.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self._config.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def initialize(self) -> StoreMutation:
        connection = self._connect()
        try:
            connection.executescript(SCHEMA_SQL)
            values = {
                "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0],
                "journal_mode": str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "synchronous": connection.execute("PRAGMA synchronous").fetchone()[0],
                "busy_timeout": connection.execute("PRAGMA busy_timeout").fetchone()[0],
            }
            if values != {
                "foreign_keys": 1,
                "journal_mode": "wal",
                "synchronous": 2,
                "busy_timeout": self._config.busy_timeout_ms,
            }:
                raise SagaStoreError("sqlite-pragmas-not-enforced")
        except sqlite3.Error as exc:
            raise SagaStoreError("sqlite-initialize-failed") from exc
        finally:
            connection.close()
        return StoreMutation(StoreResultCode.APPLIED)

    @staticmethod
    def _intent_hash(payload: str) -> str:
        try:
            return canonical_sha256(json.loads(payload))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SagaStoreCorruptionError("intent-json-corrupt") from exc

    def _insert_outbox(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        intent: EffectIntent,
        now_epoch: int,
    ) -> None:
        payload = encode_intent(intent)
        connection.execute(
            """
            INSERT INTO phase3j_outbox(
                intent_id, operation_id, sequence, action, binding_hash,
                intent_json, intent_hash, state, created_at_epoch, updated_at_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                intent.intent_id,
                operation_id,
                intent.sequence,
                intent.action.value,
                intent.binding_hash,
                payload,
                self._intent_hash(payload),
                now_epoch,
                now_epoch,
            ),
        )

    def prepare(self, binding: SagaBinding, now_epoch: int) -> StoreMutation:
        now = _epoch(now_epoch, "created-at-invalid")
        created = create_saga(binding)
        if not created.accepted or len(created.emitted_intents) != 1:
            return StoreMutation(StoreResultCode.REJECTED, created.failure_code or "saga-create-rejected")
        snapshot = created.snapshot
        intent = created.emitted_intents[0]
        payload = encode_snapshot(snapshot)
        digest = snapshot_sha256(payload)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT binding_hash FROM phase3j_sagas WHERE operation_id=?",
                (binding.operation_id,),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                if existing["binding_hash"] != binding.binding_hash:
                    return StoreMutation(StoreResultCode.CONFLICT, "operation-binding-conflict")
                return StoreMutation(
                    StoreResultCode.DUPLICATE,
                    snapshot=self.load_snapshot(binding.operation_id),
                    outbox=self.load_outbox(intent.intent_id),
                )

            connection.execute(
                "INSERT INTO phase3j_jobs VALUES (?, ?, ?, ?, ?, ?)",
                (binding.job_id, binding.operation_id, binding.binding_hash, snapshot.state.value, now, now),
            )
            self._fault("after-job-insert")
            connection.execute(
                "INSERT INTO phase3j_sagas VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    binding.operation_id,
                    binding.job_id,
                    binding.binding_hash,
                    snapshot.version,
                    payload,
                    digest,
                    now,
                ),
            )
            self._fault("after-saga-insert")
            self._insert_outbox(connection, binding.operation_id, intent, now)
            self._fault("after-outbox-insert")
            connection.commit()
        except sqlite3.IntegrityError:
            connection.rollback()
            return StoreMutation(StoreResultCode.CONFLICT, "prepare-identity-conflict")
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return StoreMutation(
            StoreResultCode.APPLIED,
            snapshot=snapshot,
            outbox=self.load_outbox(intent.intent_id),
        )

    def _decode_snapshot_row(self, row: sqlite3.Row) -> SagaSnapshot:
        try:
            snapshot = decode_snapshot(row["snapshot_json"], expected_hash=row["snapshot_hash"])
        except SagaCodecError as exc:
            raise SagaStoreCorruptionError(str(exc)) from exc
        if (
            snapshot.binding.operation_id != row["operation_id"]
            or snapshot.binding.job_id != row["job_id"]
            or snapshot.binding.binding_hash != row["binding_hash"]
            or snapshot.version != row["version"]
        ):
            raise SagaStoreCorruptionError("snapshot-row-binding-mismatch")
        return snapshot

    def load_snapshot(self, operation_id: str) -> SagaSnapshot | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM phase3j_sagas WHERE operation_id=?", (operation_id,)
            ).fetchone()
            return None if row is None else self._decode_snapshot_row(row)
        finally:
            connection.close()

    def _decode_outbox_row(
        self,
        row: sqlite3.Row,
        connection: sqlite3.Connection | None = None,
    ) -> OutboxRecord:
        try:
            intent = decode_intent(row["intent_json"], expected_hash=row["intent_hash"])
            state = OutboxState(row["state"])
        except (SagaCodecError, ValueError) as exc:
            raise SagaStoreCorruptionError("outbox-row-corrupt") from exc
        if (
            intent.intent_id != row["intent_id"]
            or intent.operation_id != row["operation_id"]
            or intent.sequence != row["sequence"]
            or intent.action.value != row["action"]
            or intent.binding_hash != row["binding_hash"]
        ):
            raise SagaStoreCorruptionError("outbox-row-binding-mismatch")
        if connection is not None:
            saga_row = connection.execute(
                "SELECT * FROM phase3j_sagas WHERE operation_id=?",
                (intent.operation_id,),
            ).fetchone()
            if saga_row is None:
                raise SagaStoreCorruptionError("outbox-saga-missing")
            snapshot = self._decode_snapshot_row(saga_row)
            intent_errors = validate_intent(intent, binding=snapshot.binding)
            if intent_errors:
                raise SagaStoreCorruptionError(intent_errors[0])
        if state is OutboxState.CLAIMED:
            if row["lease_owner"] is None or row["lease_expires_at_epoch"] is None:
                raise SagaStoreCorruptionError("outbox-claim-incomplete")
        elif row["lease_owner"] is not None or row["lease_expires_at_epoch"] is not None:
            raise SagaStoreCorruptionError("outbox-unexpected-lease")
        return OutboxRecord(
            intent=intent,
            state=state,
            lease_owner=row["lease_owner"],
            lease_expires_at_epoch=row["lease_expires_at_epoch"],
            fencing_token=row["fencing_token"],
            version=row["version"],
            settlement_fingerprint=row["settlement_fingerprint"],
            settlement_reason=row["settlement_reason"],
            updated_at_epoch=row["updated_at_epoch"],
        )

    def load_outbox(self, intent_id: str) -> OutboxRecord | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM phase3j_outbox WHERE intent_id=?", (intent_id,)
            ).fetchone()
            return None if row is None else self._decode_outbox_row(row, connection)
        finally:
            connection.close()

    def claim(
        self,
        intent_id: str,
        owner: str,
        now_epoch: int,
        lease_seconds: int,
    ) -> StoreMutation:
        lease_owner = _owner(owner)
        now = _epoch(now_epoch, "claim-time-invalid")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3_600:
            raise ValueError("lease-seconds-invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM phase3j_outbox WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                return StoreMutation(StoreResultCode.NOT_FOUND, "outbox-not-found")
            record = self._decode_outbox_row(row, connection)
            if now < record.updated_at_epoch:
                connection.rollback()
                return StoreMutation(StoreResultCode.REJECTED, "outbox-clock-regression", outbox=record)
            claimable = record.state is OutboxState.PENDING or (
                record.state is OutboxState.CLAIMED
                and record.lease_expires_at_epoch is not None
                and record.lease_expires_at_epoch <= now
            )
            if not claimable:
                connection.rollback()
                return StoreMutation(StoreResultCode.CONFLICT, "outbox-not-claimable", outbox=record)
            next_fence = record.fencing_token + 1
            cursor = connection.execute(
                """
                UPDATE phase3j_outbox
                SET state='claimed', lease_owner=?, lease_expires_at_epoch=?,
                    fencing_token=?, version=version+1, updated_at_epoch=?
                WHERE intent_id=? AND version=? AND state=?
                """,
                (
                    lease_owner,
                    now + lease_seconds,
                    next_fence,
                    now,
                    intent_id,
                    record.version,
                    record.state.value,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return StoreMutation(StoreResultCode.CONFLICT, "outbox-claim-cas-conflict")
            connection.commit()
        finally:
            connection.close()
        claimed = self.load_outbox(intent_id)
        assert claimed is not None
        claim = OutboxClaim(claimed, lease_owner)
        return StoreMutation(StoreResultCode.APPLIED, outbox=claimed, claim=claim)

    def _settle(
        self,
        claim: OutboxClaim,
        *,
        target: OutboxState,
        fingerprint: str,
        reason_code: str,
        now_epoch: int,
    ) -> StoreMutation:
        if target not in {OutboxState.ACKNOWLEDGED, OutboxState.BLOCKED}:
            raise ValueError("settlement-state-invalid")
        digest = _fingerprint(fingerprint)
        reason = _reason(reason_code)
        now = _epoch(now_epoch, "settlement-time-invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM phase3j_outbox WHERE intent_id=?", (claim.intent.intent_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                return StoreMutation(StoreResultCode.NOT_FOUND, "outbox-not-found")
            current = self._decode_outbox_row(row, connection)
            claim_record_valid = (
                isinstance(claim, OutboxClaim)
                and claim.record.state is OutboxState.CLAIMED
                and claim.record.lease_owner == claim.owner
                and claim.record.intent == claim.intent
                and claim.record.fencing_token == claim.fencing_token
                and claim.record.version == claim.version
            )
            if not claim_record_valid or current.intent != claim.intent:
                connection.rollback()
                return StoreMutation(StoreResultCode.CONFLICT, "outbox-claim-binding-conflict", outbox=current)
            if now < current.updated_at_epoch:
                connection.rollback()
                return StoreMutation(StoreResultCode.REJECTED, "outbox-clock-regression", outbox=current)
            if current.state in {OutboxState.ACKNOWLEDGED, OutboxState.BLOCKED}:
                connection.rollback()
                if (
                    current.state is target
                    and current.settlement_fingerprint == digest
                    and current.settlement_reason == reason
                ):
                    return StoreMutation(StoreResultCode.DUPLICATE, outbox=current)
                return StoreMutation(StoreResultCode.CONFLICT, "settlement-replay-conflict", outbox=current)
            if (
                current.state is not OutboxState.CLAIMED
                or current.lease_owner != claim.owner
                or current.fencing_token != claim.fencing_token
                or current.version != claim.version
                or current.lease_expires_at_epoch is None
                or current.lease_expires_at_epoch <= now
            ):
                connection.rollback()
                return StoreMutation(StoreResultCode.CONFLICT, "stale-outbox-claim", outbox=current)
            cursor = connection.execute(
                """
                UPDATE phase3j_outbox
                SET state=?, lease_owner=NULL, lease_expires_at_epoch=NULL,
                    settlement_fingerprint=?, settlement_reason=?, version=version+1,
                    updated_at_epoch=?
                WHERE intent_id=? AND state='claimed' AND lease_owner=?
                  AND fencing_token=? AND version=?
                """,
                (
                    target.value,
                    digest,
                    reason,
                    now,
                    claim.intent.intent_id,
                    claim.owner,
                    claim.fencing_token,
                    claim.version,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return StoreMutation(StoreResultCode.CONFLICT, "outbox-settlement-cas-conflict")
            connection.commit()
        finally:
            connection.close()
        settled = self.load_outbox(claim.intent.intent_id)
        return StoreMutation(StoreResultCode.APPLIED, outbox=settled)

    def acknowledge(
        self,
        claim: OutboxClaim,
        fingerprint: str,
        reason_code: str,
        now_epoch: int,
    ) -> StoreMutation:
        return self._settle(
            claim,
            target=OutboxState.ACKNOWLEDGED,
            fingerprint=fingerprint,
            reason_code=reason_code,
            now_epoch=now_epoch,
        )

    def block(
        self,
        claim: OutboxClaim,
        fingerprint: str,
        reason_code: str,
        now_epoch: int,
    ) -> StoreMutation:
        return self._settle(
            claim,
            target=OutboxState.BLOCKED,
            fingerprint=fingerprint,
            reason_code=reason_code,
            now_epoch=now_epoch,
        )

    def release(self, claim: OutboxClaim, now_epoch: int) -> StoreMutation:
        now = _epoch(now_epoch, "release-time-invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM phase3j_outbox WHERE intent_id=?", (claim.intent.intent_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                return StoreMutation(StoreResultCode.NOT_FOUND, "outbox-not-found")
            current = self._decode_outbox_row(row, connection)
            claim_record_valid = (
                isinstance(claim, OutboxClaim)
                and claim.record.state is OutboxState.CLAIMED
                and claim.record.lease_owner == claim.owner
                and claim.record.intent == claim.intent
                and claim.record.fencing_token == claim.fencing_token
                and claim.record.version == claim.version
            )
            if not claim_record_valid or current.intent != claim.intent:
                connection.rollback()
                return StoreMutation(StoreResultCode.CONFLICT, "outbox-claim-binding-conflict", outbox=current)
            if now < current.updated_at_epoch:
                connection.rollback()
                return StoreMutation(StoreResultCode.REJECTED, "outbox-clock-regression", outbox=current)
            cursor = connection.execute(
                """
                UPDATE phase3j_outbox
                SET state='pending', lease_owner=NULL, lease_expires_at_epoch=NULL,
                    version=version+1, updated_at_epoch=?
                WHERE intent_id=? AND state='claimed' AND lease_owner=?
                  AND fencing_token=? AND version=?
                """,
                (now, claim.intent.intent_id, claim.owner, claim.fencing_token, claim.version),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return StoreMutation(StoreResultCode.CONFLICT, "stale-outbox-claim")
            connection.commit()
        finally:
            connection.close()
        return StoreMutation(StoreResultCode.APPLIED, outbox=self.load_outbox(claim.intent.intent_id))

    def recover_outbox(self, operation_id: str, now_epoch: int) -> StoreMutation:
        now = _epoch(now_epoch, "recovery-time-invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            saga = connection.execute(
                "SELECT operation_id FROM phase3j_sagas WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if saga is None:
                connection.rollback()
                return StoreMutation(StoreResultCode.NOT_FOUND, "saga-not-found")
            latest = connection.execute(
                "SELECT MAX(updated_at_epoch) FROM phase3j_outbox WHERE operation_id=?",
                (operation_id,),
            ).fetchone()[0]
            if latest is not None and now < latest:
                connection.rollback()
                return StoreMutation(StoreResultCode.REJECTED, "outbox-clock-regression")
            cursor = connection.execute(
                """
                UPDATE phase3j_outbox
                SET state='pending', lease_owner=NULL, lease_expires_at_epoch=NULL,
                    version=version+1, updated_at_epoch=?
                WHERE operation_id=? AND state='claimed' AND lease_expires_at_epoch<=?
                """,
                (now, operation_id, now),
            )
            connection.commit()
        finally:
            connection.close()
        return StoreMutation(StoreResultCode.APPLIED, recovered_count=cursor.rowcount)

    def _persist_transition(
        self,
        connection: sqlite3.Connection,
        previous: SagaSnapshot,
        current: SagaSnapshot,
        emitted: tuple[EffectIntent, ...],
        now_epoch: int,
        event_payload: str | None,
    ) -> StoreMutation:
        payload = encode_snapshot(current)
        cursor = connection.execute(
            """
            UPDATE phase3j_sagas
            SET version=?, snapshot_json=?, snapshot_hash=?, updated_at_epoch=?
            WHERE operation_id=? AND version=? AND binding_hash=?
            """,
            (
                current.version,
                payload,
                snapshot_sha256(payload),
                now_epoch,
                previous.binding.operation_id,
                previous.version,
                previous.binding.binding_hash,
            ),
        )
        if cursor.rowcount != 1:
            return StoreMutation(StoreResultCode.CONFLICT, "saga-version-cas-conflict")
        connection.execute(
            "UPDATE phase3j_jobs SET state=?, updated_at_epoch=? WHERE job_id=? AND binding_hash=?",
            (current.state.value, now_epoch, current.binding.job_id, current.binding.binding_hash),
        )
        previous_ids = {item.event_id for item in previous.applied_events}
        for applied in current.applied_events:
            if applied.event_id in previous_ids:
                continue
            if event_payload is None:
                raise SagaStoreCorruptionError("recovery-event-payload-missing")
            evidence = event_payload
            connection.execute(
                "INSERT INTO phase3j_saga_events VALUES (?, ?, ?, ?, ?)",
                (applied.event_id, current.binding.operation_id, applied.event_hash, evidence, now_epoch),
            )
        for intent in emitted:
            try:
                self._insert_outbox(connection, current.binding.operation_id, intent, now_epoch)
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT intent_hash FROM phase3j_outbox WHERE intent_id=?", (intent.intent_id,)
                ).fetchone()
                encoded = encode_intent(intent)
                if row is None or row["intent_hash"] != self._intent_hash(encoded):
                    return StoreMutation(StoreResultCode.CONFLICT, "outbox-replay-conflict")
        return StoreMutation(StoreResultCode.APPLIED, snapshot=current)

    @staticmethod
    def _recovery_event(
        previous: SagaSnapshot,
        current: SagaSnapshot,
        observed_at_epoch: int,
    ) -> SagaEvent:
        if len(current.applied_events) != len(previous.applied_events) + 1:
            raise SagaStoreCorruptionError("recovery-event-count-invalid")
        applied = current.applied_events[-1]
        if previous.state is SagaState.RUNNING:
            event = SagaEvent(
                kind=EventKind.WORKER_LEASE_EXPIRED,
                event_id=applied.event_id,
                binding_hash=previous.binding.binding_hash,
                observed_at_epoch=observed_at_epoch,
                worker_fencing_token=previous.worker_fencing_token,
            )
        elif previous.state in {
            SagaState.LOCAL_PREPARE_PENDING,
            SagaState.CONSUME_PENDING,
        }:
            event = SagaEvent(
                kind=EventKind.RESERVATION_EXPIRED,
                event_id=applied.event_id,
                binding_hash=previous.binding.binding_hash,
                observed_at_epoch=observed_at_epoch,
            )
        else:
            raise SagaStoreCorruptionError("recovery-event-kind-invalid")
        payload = encode_event(event)
        if event_sha256(payload) != applied.event_hash:
            raise SagaStoreCorruptionError("recovery-event-hash-mismatch")
        return event

    def apply(self, operation_id: str, event: SagaEvent) -> StoreMutation:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM phase3j_sagas WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                return StoreMutation(StoreResultCode.NOT_FOUND, "saga-not-found")
            snapshot = self._decode_snapshot_row(row)
            result = apply_event(snapshot, event)
            if not result.accepted:
                connection.rollback()
                code = (
                    StoreResultCode.CONFLICT
                    if result.failure_code
                    in {
                        "event-id-reused-with-different-payload",
                        "event-id-payload-conflict",
                    }
                    else StoreResultCode.REJECTED
                )
                return StoreMutation(code, result.failure_code, snapshot=snapshot)
            if event.observed_at_epoch < row["updated_at_epoch"]:
                connection.rollback()
                return StoreMutation(StoreResultCode.REJECTED, "saga-clock-regression", snapshot=snapshot)
            if result.duplicate:
                connection.rollback()
                return StoreMutation(StoreResultCode.DUPLICATE, snapshot=snapshot)
            event_payload = encode_event(event)
            if result.snapshot.applied_events[-1].event_hash != event_sha256(event_payload):
                connection.rollback()
                return StoreMutation(StoreResultCode.REJECTED, "event-hash-contract-mismatch")
            persisted = self._persist_transition(
                connection,
                snapshot,
                result.snapshot,
                result.emitted_intents,
                event.observed_at_epoch,
                event_payload,
            )
            if persisted.code is not StoreResultCode.APPLIED:
                connection.rollback()
                return persisted
            connection.commit()
            return StoreMutation(StoreResultCode.APPLIED, snapshot=result.snapshot)
        except sqlite3.IntegrityError:
            connection.rollback()
            existing = self.load_snapshot(operation_id)
            return StoreMutation(StoreResultCode.CONFLICT, "event-append-conflict", snapshot=existing)
        finally:
            connection.close()

    def recover(self, operation_id: str, observed_at_epoch: int) -> StoreMutation:
        observed = _epoch(observed_at_epoch, "recovery-time-invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM phase3j_sagas WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                return StoreMutation(StoreResultCode.NOT_FOUND, "saga-not-found")
            snapshot = self._decode_snapshot_row(row)
            if observed < row["updated_at_epoch"]:
                connection.rollback()
                return StoreMutation(StoreResultCode.REJECTED, "saga-clock-regression", snapshot=snapshot)
            latest_outbox_update = connection.execute(
                "SELECT MAX(updated_at_epoch) FROM phase3j_outbox WHERE operation_id=?",
                (operation_id,),
            ).fetchone()[0]
            if latest_outbox_update is not None and observed < latest_outbox_update:
                connection.rollback()
                return StoreMutation(StoreResultCode.REJECTED, "outbox-clock-regression", snapshot=snapshot)
            result = recover(snapshot, observed)
            if not result.accepted:
                connection.rollback()
                return StoreMutation(StoreResultCode.REJECTED, result.failure_code, snapshot=snapshot)
            recovered_claims = connection.execute(
                """
                UPDATE phase3j_outbox
                SET state='pending', lease_owner=NULL, lease_expires_at_epoch=NULL,
                    version=version+1, updated_at_epoch=?
                WHERE operation_id=? AND state='claimed' AND lease_expires_at_epoch<=?
                """,
                (observed, operation_id, observed),
            ).rowcount
            if result.snapshot == snapshot:
                for intent in result.emitted_intents:
                    existing = connection.execute(
                        "SELECT intent_hash FROM phase3j_outbox WHERE intent_id=?", (intent.intent_id,)
                    ).fetchone()
                    encoded = encode_intent(intent)
                    if existing is None:
                        self._insert_outbox(connection, operation_id, intent, observed)
                    elif existing["intent_hash"] != self._intent_hash(encoded):
                        connection.rollback()
                        return StoreMutation(StoreResultCode.CONFLICT, "outbox-replay-conflict")
                connection.commit()
                return StoreMutation(
                    StoreResultCode.DUPLICATE,
                    snapshot=snapshot,
                    recovered_count=recovered_claims,
                )
            recovery_event = self._recovery_event(snapshot, result.snapshot, observed)
            persisted = self._persist_transition(
                connection,
                snapshot,
                result.snapshot,
                result.emitted_intents,
                observed,
                encode_event(recovery_event),
            )
            if persisted.code is not StoreResultCode.APPLIED:
                connection.rollback()
                return persisted
            connection.commit()
            return StoreMutation(
                persisted.code,
                persisted.reason_code,
                snapshot=persisted.snapshot,
                outbox=persisted.outbox,
                claim=persisted.claim,
                recovered_count=recovered_claims,
            )
        finally:
            connection.close()

    def table_counts(self) -> dict[str, int]:
        connection = self._connect()
        try:
            return {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "phase3j_jobs",
                    "phase3j_sagas",
                    "phase3j_saga_events",
                    "phase3j_outbox",
                )
            }
        finally:
            connection.close()

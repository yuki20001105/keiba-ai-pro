"""Phase 3N operational scrape outbox and worker runtime.

The Phase 3J runtime is intentionally sealed as a disposable evidence slice.
This module is the operational boundary used by the HTTP scrape endpoint.  It
keeps all scheduling state durable, claims work with a lease and monotonically
increasing fencing token, and gives the downstream executor a stable
idempotency key on every recovery attempt.

Production and staging use the Supabase/PostgreSQL RPC adapter.  The SQLite
adapter exists only for explicitly enabled local/test/CI use.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol


class OperationalSagaError(RuntimeError):
    """Base class for fail-closed operational runtime errors."""


class OperationalSagaConfigError(OperationalSagaError):
    pass


class OperationalSagaUnavailable(OperationalSagaError):
    pass


class OperationalSagaConflict(OperationalSagaError):
    pass


class OperationalSagaMode(str, Enum):
    DISABLED = "disabled"
    LOCAL_SQLITE = "local-sqlite"
    SUPABASE = "supabase"


class MutationCode(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


_LOCAL_ENVS = frozenset({"local", "test", "ci"})
_DEPLOYED_ENVS = frozenset({"staging", "stage", "production", "prod", "prd", "live"})
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})
_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _bool(values: Mapping[str, str], name: str, *, default: bool = False) -> bool:
    raw = values.get(name, "true" if default else "false").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise OperationalSagaConfigError(f"{name.lower()}-invalid")


def _bounded_int(values: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(values.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise OperationalSagaConfigError(f"{name.lower()}-invalid") from exc
    if not low <= value <= high:
        raise OperationalSagaConfigError(f"{name.lower()}-invalid")
    return value


@dataclass(frozen=True)
class OperationalSagaConfig:
    mode: OperationalSagaMode = OperationalSagaMode.DISABLED
    environment: str = "local"
    sqlite_path: Path | None = None
    worker_enabled: bool = False
    remote_effects_enabled: bool = False
    execution_unlock_enabled: bool = False
    lease_seconds: int = 30
    poll_interval_ms: int = 1_000
    max_attempts: int = 5

    def __post_init__(self) -> None:
        if not isinstance(self.mode, OperationalSagaMode):
            raise OperationalSagaConfigError("mode-invalid")
        env = str(self.environment or "").strip().lower()
        if not env or len(env) > 32 or not env.replace("-", "").isalnum():
            raise OperationalSagaConfigError("environment-invalid")
        object.__setattr__(self, "environment", env)
        if type(self.lease_seconds) is not int or not 5 <= self.lease_seconds <= 300:
            raise OperationalSagaConfigError("lease-seconds-invalid")
        if type(self.poll_interval_ms) is not int or not 50 <= self.poll_interval_ms <= 30_000:
            raise OperationalSagaConfigError("poll-interval-invalid")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 20:
            raise OperationalSagaConfigError("max-attempts-invalid")

        if self.mode is OperationalSagaMode.DISABLED:
            if self.sqlite_path is not None or any(
                (self.worker_enabled, self.remote_effects_enabled, self.execution_unlock_enabled)
            ):
                raise OperationalSagaConfigError("disabled-runtime-widened")
            return
        if not all((self.worker_enabled, self.remote_effects_enabled, self.execution_unlock_enabled)):
            raise OperationalSagaConfigError("operational-flags-not-enabled")
        if self.mode is OperationalSagaMode.LOCAL_SQLITE:
            if env not in _LOCAL_ENVS:
                raise OperationalSagaConfigError("local-sqlite-environment-forbidden")
            if self.sqlite_path is None:
                raise OperationalSagaConfigError("sqlite-path-required")
            candidate = self.sqlite_path.expanduser().resolve(strict=False)
            temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
            try:
                candidate.relative_to(temp_root)
            except ValueError as exc:
                raise OperationalSagaConfigError("sqlite-path-not-temporary") from exc
            if candidate == temp_root or candidate.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
                raise OperationalSagaConfigError("sqlite-path-invalid")
            object.__setattr__(self, "sqlite_path", candidate)
        elif self.mode is OperationalSagaMode.SUPABASE:
            if env not in _DEPLOYED_ENVS:
                raise OperationalSagaConfigError("supabase-environment-required")
            if self.sqlite_path is not None:
                raise OperationalSagaConfigError("supabase-runtime-has-sqlite-path")

    @property
    def enabled(self) -> bool:
        return self.mode is not OperationalSagaMode.DISABLED


def load_operational_saga_config(
    environ: Mapping[str, str] | None = None,
) -> OperationalSagaConfig:
    values = os.environ if environ is None else environ
    raw_mode = values.get("PHASE3N_OPERATIONAL_SAGA_MODE", "disabled").strip().lower()
    try:
        mode = OperationalSagaMode(raw_mode)
    except ValueError as exc:
        raise OperationalSagaConfigError("mode-invalid") from exc
    environment = values.get("APP_ENV", "local")
    worker = _bool(values, "PHASE3N_WORKER_ENABLED")
    effects = _bool(values, "PHASE3N_REMOTE_EFFECTS_ENABLED")
    unlock = _bool(values, "PHASE3N_EXECUTION_UNLOCK_ENABLED")
    lease = _bounded_int(values, "PHASE3N_LEASE_SECONDS", 30, 5, 300)
    poll = _bounded_int(values, "PHASE3N_POLL_INTERVAL_MS", 1_000, 50, 30_000)
    attempts = _bounded_int(values, "PHASE3N_MAX_ATTEMPTS", 5, 1, 20)
    path = values.get("PHASE3N_SAGA_SQLITE_PATH")
    return OperationalSagaConfig(
        mode=mode,
        environment=environment,
        sqlite_path=Path(path) if path else None,
        worker_enabled=worker,
        remote_effects_enabled=effects,
        execution_unlock_enabled=unlock,
        lease_seconds=lease,
        poll_interval_ms=poll,
        max_attempts=attempts,
    )


@dataclass(frozen=True)
class EnqueueRequest:
    job_id: str
    operation_id: str
    owner_user_id: str
    request_hash: str
    request_payload: dict[str, Any]
    authorization_id: str | None = None
    reservation_id: str | None = None
    review_id: str | None = None
    review_version: int | None = None
    expected_authorization_version: int | None = None
    consume_request_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("job_id", "operation_id"):
            try:
                canonical = str(uuid.UUID(str(getattr(self, name))))
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError(f"{name.replace('_', '-')}-invalid") from exc
            if canonical != str(getattr(self, name)).lower():
                raise ValueError(f"{name.replace('_', '-')}-invalid")
        if _OWNER_RE.fullmatch(self.owner_user_id) is None:
            raise ValueError("owner-user-id-invalid")
        if _HEX64_RE.fullmatch(self.request_hash) is None:
            raise ValueError("request-hash-invalid")
        if not isinstance(self.request_payload, dict):
            raise ValueError("request-payload-invalid")

    @property
    def idempotency_key(self) -> str:
        return hashlib.sha256(
            f"scrape-operational-effect-v1|{self.operation_id}|{self.job_id}|{self.request_hash}".encode()
        ).hexdigest()


@dataclass(frozen=True)
class OperationalClaim:
    job_id: str
    operation_id: str
    owner_user_id: str
    request_hash: str
    request_payload: dict[str, Any]
    idempotency_key: str
    worker_owner: str
    fencing_token: int
    lease_expires_at_epoch: int
    attempt_count: int


@dataclass(frozen=True)
class EffectResult:
    result: dict[str, Any]
    receipt_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.result, dict):
            raise ValueError("effect-result-invalid")
        if _HEX64_RE.fullmatch(self.receipt_hash) is None:
            raise ValueError("effect-receipt-invalid")


@dataclass(frozen=True)
class Mutation:
    code: MutationCode
    reason: str | None = None
    job: dict[str, Any] | None = None
    claim: OperationalClaim | None = None


class OperationalSagaStore(Protocol):
    def initialize(self) -> None: ...
    def enqueue(self, request: EnqueueRequest, now_epoch: int) -> Mutation: ...
    def claim_next(self, worker_owner: str, now_epoch: int, lease_seconds: int) -> Mutation: ...
    def heartbeat(self, claim: OperationalClaim, now_epoch: int, lease_seconds: int) -> Mutation: ...
    def complete(self, claim: OperationalClaim, effect: EffectResult, now_epoch: int) -> Mutation: ...
    def fail(self, claim: OperationalClaim, reason: str, now_epoch: int) -> Mutation: ...
    def get_job(self, job_id: str, owner_user_id: str) -> dict[str, Any] | None: ...
    def list_jobs(self, owner_user_id: str, limit: int) -> list[dict[str, Any]]: ...


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS operational_scrape_jobs (
    job_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    owner_user_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    request_payload TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','error')),
    progress TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    error TEXT,
    created_at_epoch INTEGER NOT NULL,
    updated_at_epoch INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS operational_one_active_job_per_owner
ON operational_scrape_jobs(owner_user_id) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS operational_scrape_outbox (
    job_id TEXT PRIMARY KEY REFERENCES operational_scrape_jobs(job_id) ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK(state IN ('pending','claimed','acknowledged','blocked')),
    worker_owner TEXT,
    lease_expires_at_epoch INTEGER,
    fencing_token INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    effect_receipt_hash TEXT UNIQUE,
    settlement_reason TEXT,
    created_at_epoch INTEGER NOT NULL,
    updated_at_epoch INTEGER NOT NULL
);
"""


class SQLiteOperationalSagaStore:
    """Explicit local/test adapter mirroring the PostgreSQL RPC semantics."""

    def __init__(self, config: OperationalSagaConfig) -> None:
        if config.mode is not OperationalSagaMode.LOCAL_SQLITE or config.sqlite_path is None:
            raise OperationalSagaConfigError("local-sqlite-config-required")
        self._path = config.sqlite_path
        self._max_attempts = config.max_attempts

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.executescript(_SQLITE_SCHEMA)
        finally:
            connection.close()

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "operation_id": row["operation_id"],
            "owner_user_id": row["owner_user_id"],
            "request_hash": row["request_hash"],
            "status": row["status"],
            "progress": json.loads(row["progress"] or "{}"),
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": json.loads(row["error"]) if row["error"] else None,
        }

    @staticmethod
    def _claim(row: sqlite3.Row, worker_owner: str) -> OperationalClaim:
        return OperationalClaim(
            job_id=row["job_id"],
            operation_id=row["operation_id"],
            owner_user_id=row["owner_user_id"],
            request_hash=row["request_hash"],
            request_payload=json.loads(row["request_payload"]),
            idempotency_key=row["idempotency_key"],
            worker_owner=worker_owner,
            fencing_token=int(row["fencing_token"]),
            lease_expires_at_epoch=int(row["lease_expires_at_epoch"]),
            attempt_count=int(row["attempt_count"]),
        )

    def enqueue(self, request: EnqueueRequest, now_epoch: int) -> Mutation:
        payload = json.dumps(request.request_payload, sort_keys=True, separators=(",", ":"))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM operational_scrape_jobs WHERE job_id=?", (request.job_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["operation_id"] == request.operation_id
                    and existing["owner_user_id"] == request.owner_user_id
                    and existing["request_hash"] == request.request_hash
                    and existing["request_payload"] == payload
                ):
                    connection.commit()
                    return Mutation(MutationCode.DUPLICATE, job=self._job(existing))
                connection.rollback()
                return Mutation(MutationCode.CONFLICT, "job-id-binding-conflict")
            active = connection.execute(
                "SELECT 1 FROM operational_scrape_jobs WHERE owner_user_id=? "
                "AND status IN ('queued','running') LIMIT 1",
                (request.owner_user_id,),
            ).fetchone()
            if active is not None:
                connection.rollback()
                return Mutation(MutationCode.CONFLICT, "owner-active-job")
            connection.execute(
                "INSERT INTO operational_scrape_jobs "
                "(job_id,operation_id,owner_user_id,request_hash,request_payload,idempotency_key,status,created_at_epoch,updated_at_epoch) "
                "VALUES (?,?,?,?,?,?,'queued',?,?)",
                (
                    request.job_id,
                    request.operation_id,
                    request.owner_user_id,
                    request.request_hash,
                    payload,
                    request.idempotency_key,
                    now_epoch,
                    now_epoch,
                ),
            )
            connection.execute(
                "INSERT INTO operational_scrape_outbox (job_id,state,created_at_epoch,updated_at_epoch) "
                "VALUES (?,'pending',?,?)",
                (request.job_id, now_epoch, now_epoch),
            )
            row = connection.execute(
                "SELECT * FROM operational_scrape_jobs WHERE job_id=?", (request.job_id,)
            ).fetchone()
            connection.commit()
            return Mutation(MutationCode.APPLIED, job=self._job(row))
        except sqlite3.Error as exc:
            connection.rollback()
            raise OperationalSagaUnavailable("operational-enqueue-failed") from exc
        finally:
            connection.close()

    def claim_next(self, worker_owner: str, now_epoch: int, lease_seconds: int) -> Mutation:
        if _OWNER_RE.fullmatch(worker_owner) is None:
            return Mutation(MutationCode.CONFLICT, "worker-owner-invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            exhausted = connection.execute(
                "SELECT job_id FROM operational_scrape_outbox WHERE state='claimed' "
                "AND lease_expires_at_epoch<=? AND attempt_count>=? "
                "ORDER BY created_at_epoch,job_id LIMIT 1",
                (now_epoch, self._max_attempts),
            ).fetchone()
            if exhausted is not None:
                connection.execute(
                    "UPDATE operational_scrape_outbox SET state='blocked',worker_owner=NULL,"
                    "lease_expires_at_epoch=NULL,settlement_reason='max-attempts-exhausted',"
                    "version=version+1,updated_at_epoch=? WHERE job_id=?",
                    (now_epoch, exhausted["job_id"]),
                )
                connection.execute(
                    "UPDATE operational_scrape_jobs SET status='error',result=NULL,"
                    "error='\"max-attempts-exhausted\"',updated_at_epoch=? WHERE job_id=?",
                    (now_epoch, exhausted["job_id"]),
                )
            row = connection.execute(
                "SELECT j.*,o.state,o.fencing_token,o.attempt_count FROM operational_scrape_outbox o "
                "JOIN operational_scrape_jobs j ON j.job_id=o.job_id "
                "WHERE (o.state='pending' OR (o.state='claimed' AND o.lease_expires_at_epoch<=?)) "
                "AND o.attempt_count<? ORDER BY o.created_at_epoch,o.job_id LIMIT 1",
                (now_epoch, self._max_attempts),
            ).fetchone()
            if row is None:
                connection.commit()
                return Mutation(MutationCode.NOT_FOUND)
            expires = now_epoch + lease_seconds
            updated = connection.execute(
                "UPDATE operational_scrape_outbox SET state='claimed',worker_owner=?,lease_expires_at_epoch=?,"
                "fencing_token=fencing_token+1,version=version+1,attempt_count=attempt_count+1,updated_at_epoch=? "
                "WHERE job_id=? AND (state='pending' OR (state='claimed' AND lease_expires_at_epoch<=?))",
                (worker_owner, expires, now_epoch, row["job_id"], now_epoch),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return Mutation(MutationCode.CONFLICT, "claim-raced")
            connection.execute(
                "UPDATE operational_scrape_jobs SET status='running',updated_at_epoch=? WHERE job_id=?",
                (now_epoch, row["job_id"]),
            )
            claimed = connection.execute(
                "SELECT j.*,o.fencing_token,o.lease_expires_at_epoch,o.attempt_count "
                "FROM operational_scrape_jobs j JOIN operational_scrape_outbox o USING(job_id) WHERE j.job_id=?",
                (row["job_id"],),
            ).fetchone()
            connection.commit()
            return Mutation(MutationCode.APPLIED, claim=self._claim(claimed, worker_owner))
        except sqlite3.Error as exc:
            connection.rollback()
            raise OperationalSagaUnavailable("operational-claim-failed") from exc
        finally:
            connection.close()

    def heartbeat(self, claim: OperationalClaim, now_epoch: int, lease_seconds: int) -> Mutation:
        connection = self._connect()
        try:
            expires = now_epoch + lease_seconds
            cursor = connection.execute(
                "UPDATE operational_scrape_outbox SET lease_expires_at_epoch=?,version=version+1,updated_at_epoch=? "
                "WHERE job_id=? AND state='claimed' AND worker_owner=? AND fencing_token=? "
                "AND lease_expires_at_epoch>?",
                (expires, now_epoch, claim.job_id, claim.worker_owner, claim.fencing_token, now_epoch),
            )
            if cursor.rowcount != 1:
                return Mutation(MutationCode.CONFLICT, "lease-lost")
            return Mutation(
                MutationCode.APPLIED,
                claim=OperationalClaim(**{**claim.__dict__, "lease_expires_at_epoch": expires}),
            )
        except sqlite3.Error as exc:
            raise OperationalSagaUnavailable("operational-heartbeat-failed") from exc
        finally:
            connection.close()

    def complete(self, claim: OperationalClaim, effect: EffectResult, now_epoch: int) -> Mutation:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state,worker_owner,fencing_token,lease_expires_at_epoch,effect_receipt_hash "
                "FROM operational_scrape_outbox WHERE job_id=?",
                (claim.job_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return Mutation(MutationCode.NOT_FOUND)
            if row["state"] == "acknowledged":
                connection.commit()
                code = MutationCode.DUPLICATE if row["effect_receipt_hash"] == effect.receipt_hash else MutationCode.CONFLICT
                return Mutation(code, None if code is MutationCode.DUPLICATE else "effect-receipt-conflict")
            if (
                row["state"] != "claimed"
                or row["worker_owner"] != claim.worker_owner
                or int(row["fencing_token"]) != claim.fencing_token
                or int(row["lease_expires_at_epoch"] or 0) <= now_epoch
            ):
                connection.rollback()
                return Mutation(MutationCode.CONFLICT, "stale-worker-fence")
            result_json = json.dumps(effect.result, ensure_ascii=False, sort_keys=True)
            connection.execute(
                "UPDATE operational_scrape_outbox SET state='acknowledged',worker_owner=NULL,lease_expires_at_epoch=NULL,"
                "effect_receipt_hash=?,settlement_reason='effect-confirmed',version=version+1,updated_at_epoch=? WHERE job_id=?",
                (effect.receipt_hash, now_epoch, claim.job_id),
            )
            connection.execute(
                "UPDATE operational_scrape_jobs SET status='completed',result=?,error=NULL,updated_at_epoch=? WHERE job_id=?",
                (result_json, now_epoch, claim.job_id),
            )
            connection.commit()
            return Mutation(MutationCode.APPLIED)
        except sqlite3.IntegrityError:
            connection.rollback()
            return Mutation(MutationCode.CONFLICT, "duplicate-effect-receipt")
        except sqlite3.Error as exc:
            connection.rollback()
            raise OperationalSagaUnavailable("operational-complete-failed") from exc
        finally:
            connection.close()

    def fail(self, claim: OperationalClaim, reason: str, now_epoch: int) -> Mutation:
        safe_reason = str(reason or "worker-failed")[:500]
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE operational_scrape_outbox SET state='blocked',worker_owner=NULL,lease_expires_at_epoch=NULL,"
                "settlement_reason=?,version=version+1,updated_at_epoch=? WHERE job_id=? AND state='claimed' "
                "AND worker_owner=? AND fencing_token=? AND lease_expires_at_epoch>?",
                (safe_reason, now_epoch, claim.job_id, claim.worker_owner, claim.fencing_token, now_epoch),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return Mutation(MutationCode.CONFLICT, "stale-worker-fence")
            connection.execute(
                "UPDATE operational_scrape_jobs SET status='error',result=NULL,error=?,updated_at_epoch=? WHERE job_id=?",
                (json.dumps(safe_reason), now_epoch, claim.job_id),
            )
            connection.commit()
            return Mutation(MutationCode.APPLIED)
        except sqlite3.Error as exc:
            connection.rollback()
            raise OperationalSagaUnavailable("operational-fail-failed") from exc
        finally:
            connection.close()

    def get_job(self, job_id: str, owner_user_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM operational_scrape_jobs WHERE job_id=? AND owner_user_id=?",
                (job_id, owner_user_id),
            ).fetchone()
            return self._job(row) if row is not None else None
        finally:
            connection.close()

    def list_jobs(self, owner_user_id: str, limit: int) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM operational_scrape_jobs WHERE owner_user_id=? "
                "ORDER BY updated_at_epoch DESC LIMIT ?",
                (owner_user_id, max(1, min(int(limit), 100))),
            ).fetchall()
            return [
                {
                    "job_id": item["job_id"],
                    "status": item["status"],
                    "progress": item["progress"],
                    "result": item["result"],
                    "error": item["error"],
                }
                for item in (self._job(row) for row in rows)
            ]
        finally:
            connection.close()


class SupabaseOperationalSagaStore:
    """Shared PostgreSQL adapter using service-role-only atomic RPCs."""

    def __init__(self, client: Any, *, max_attempts: int = 5) -> None:
        if client is None or not callable(getattr(client, "rpc", None)):
            raise OperationalSagaUnavailable("supabase-operational-client-unavailable")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise OperationalSagaConfigError("max-attempts-invalid")
        self._client = client
        self._max_attempts = max_attempts

    @staticmethod
    def _one(response: Any, code: str) -> dict[str, Any]:
        data = getattr(response, "data", None)
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise OperationalSagaUnavailable(code)
        return dict(data[0])

    def _rpc(self, name: str, params: dict[str, Any], code: str) -> dict[str, Any]:
        try:
            return self._one(self._client.rpc(name, params).execute(), code)
        except OperationalSagaUnavailable:
            raise
        except Exception as exc:
            raise OperationalSagaUnavailable(code) from exc

    def initialize(self) -> None:
        row = self._rpc("phase3n_operational_runtime_health", {}, "operational-schema-unavailable")
        if row.get("ready") is not True or row.get("schema_version") != 1:
            raise OperationalSagaUnavailable("operational-schema-invalid")

    @staticmethod
    def _mutation(row: dict[str, Any]) -> Mutation:
        code = str(row.get("mutation_code") or "")
        if code not in {item.value for item in MutationCode}:
            raise OperationalSagaUnavailable("operational-rpc-mutation-invalid")
        claim = None
        if code == MutationCode.APPLIED.value and row.get("worker_owner"):
            payload = row.get("request_payload")
            if not isinstance(payload, dict):
                raise OperationalSagaUnavailable("operational-rpc-payload-invalid")
            claim = OperationalClaim(
                job_id=str(row["job_id"]),
                operation_id=str(row["operation_id"]),
                owner_user_id=str(row["owner_user_id"]),
                request_hash=str(row["request_hash"]),
                request_payload=payload,
                idempotency_key=str(row["idempotency_key"]),
                worker_owner=str(row["worker_owner"]),
                fencing_token=int(row["fencing_token"]),
                lease_expires_at_epoch=int(row["lease_expires_at_epoch"]),
                attempt_count=int(row["attempt_count"]),
            )
        return Mutation(MutationCode(code), str(row.get("reason") or "") or None, row.get("job"), claim)

    def enqueue(self, request: EnqueueRequest, now_epoch: int) -> Mutation:
        required = (
            request.authorization_id,
            request.reservation_id,
            request.review_id,
            request.review_version,
            request.expected_authorization_version,
            request.consume_request_id,
        )
        if any(value is None for value in required):
            return Mutation(MutationCode.CONFLICT, "execution-authorization-required")
        row = self._rpc(
            "enqueue_scrape_operational_job",
            {
                "p_authorization_id": request.authorization_id,
                "p_reservation_id": request.reservation_id,
                "p_operation_id": request.operation_id,
                "p_job_id": request.job_id,
                "p_review_id": request.review_id,
                "p_review_version": request.review_version,
                "p_owner_user_id": request.owner_user_id,
                "p_execution_request_hash": request.request_hash,
                "p_expected_authorization_version": request.expected_authorization_version,
                "p_consume_request_id": request.consume_request_id,
                "p_request_payload": request.request_payload,
                "p_idempotency_key": request.idempotency_key,
            },
            "operational-enqueue-unavailable",
        )
        return self._mutation(row)

    def claim_next(self, worker_owner: str, now_epoch: int, lease_seconds: int) -> Mutation:
        row = self._rpc(
            "claim_scrape_operational_outbox",
            {
                "p_worker_owner": worker_owner,
                "p_lease_seconds": lease_seconds,
                "p_max_attempts": self._max_attempts,
            },
            "operational-claim-unavailable",
        )
        return self._mutation(row)

    def heartbeat(self, claim: OperationalClaim, now_epoch: int, lease_seconds: int) -> Mutation:
        row = self._rpc(
            "heartbeat_scrape_operational_outbox",
            {
                "p_job_id": claim.job_id,
                "p_worker_owner": claim.worker_owner,
                "p_fencing_token": claim.fencing_token,
                "p_lease_seconds": lease_seconds,
            },
            "operational-heartbeat-unavailable",
        )
        mutation = self._mutation(row)
        if mutation.code is MutationCode.APPLIED and mutation.claim is None:
            mutation = Mutation(
                mutation.code,
                mutation.reason,
                mutation.job,
                OperationalClaim(
                    **{
                        **claim.__dict__,
                        "lease_expires_at_epoch": int(row["lease_expires_at_epoch"]),
                    }
                ),
            )
        return mutation

    def complete(self, claim: OperationalClaim, effect: EffectResult, now_epoch: int) -> Mutation:
        row = self._rpc(
            "settle_scrape_operational_outbox",
            {
                "p_job_id": claim.job_id,
                "p_worker_owner": claim.worker_owner,
                "p_fencing_token": claim.fencing_token,
                "p_outcome": "completed",
                "p_result": effect.result,
                "p_error": None,
                "p_effect_receipt_hash": effect.receipt_hash,
            },
            "operational-complete-unavailable",
        )
        return self._mutation(row)

    def fail(self, claim: OperationalClaim, reason: str, now_epoch: int) -> Mutation:
        row = self._rpc(
            "settle_scrape_operational_outbox",
            {
                "p_job_id": claim.job_id,
                "p_worker_owner": claim.worker_owner,
                "p_fencing_token": claim.fencing_token,
                "p_outcome": "error",
                "p_result": None,
                "p_error": str(reason or "worker-failed")[:500],
                "p_effect_receipt_hash": None,
            },
            "operational-fail-unavailable",
        )
        return self._mutation(row)

    def get_job(self, job_id: str, owner_user_id: str) -> dict[str, Any] | None:
        try:
            response = (
                self._client.table("scrape_operational_jobs")
                .select("job_id,operation_id,owner_user_id,request_hash,status,progress,result,error")
                .eq("job_id", job_id)
                .eq("owner_user_id", owner_user_id)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            raise OperationalSagaUnavailable("operational-job-read-unavailable") from exc
        data = getattr(response, "data", None)
        if not isinstance(data, list):
            raise OperationalSagaUnavailable("operational-job-read-invalid")
        return dict(data[0]) if len(data) == 1 and isinstance(data[0], dict) else None

    def list_jobs(self, owner_user_id: str, limit: int) -> list[dict[str, Any]]:
        try:
            response = (
                self._client.table("scrape_operational_jobs")
                .select("job_id,status,progress,result,error,created_at,updated_at")
                .eq("owner_user_id", owner_user_id)
                .order("updated_at", desc=True)
                .limit(max(1, min(int(limit), 100)))
                .execute()
            )
        except Exception as exc:
            raise OperationalSagaUnavailable("operational-history-unavailable") from exc
        data = getattr(response, "data", None)
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise OperationalSagaUnavailable("operational-history-invalid")
        return [dict(item) for item in data]


class OperationalEffectExecutor(Protocol):
    idempotent: bool
    async def execute(self, claim: OperationalClaim) -> EffectResult: ...


class ScrapeJobEffectExecutor:
    """Existing scraper behind a stable job/idempotency boundary.

    Deployed write effects remain disabled until each destination write can
    atomically validate the shared lease/fence and deduplicate the stable
    idempotency key. Explicit local/test mode may retain the legacy SQLite
    upsert path; dry-run is side-effect-free in every mode.
    """

    idempotent = True

    def __init__(self, *, allow_unfenced_local_writes: bool = False) -> None:
        self._allow_unfenced_local_writes = allow_unfenced_local_writes

    async def execute(self, claim: OperationalClaim) -> EffectResult:
        from scraping.jobs import _JOBS_LOCK, _run_scrape_job, _scrape_jobs

        payload = claim.request_payload
        required = {"start_date", "end_date", "force_rescrape", "dry_run"}
        if set(payload) != required:
            raise OperationalSagaError("scrape-payload-contract-invalid")
        if payload.get("dry_run") is not True and not self._allow_unfenced_local_writes:
            raise OperationalSagaUnavailable("fenced-operational-execute-not-enabled")
        job = {
            "status": "queued",
            "progress": {"done": 0, "total": 0, "message": "queued"},
            "result": None,
            "error": None,
            "owner_user_id": claim.owner_user_id,
            "request_hash": claim.request_hash,
            "idempotency_key": claim.idempotency_key,
            "fencing_token": claim.fencing_token,
        }
        with _JOBS_LOCK:
            existing = _scrape_jobs.get(claim.job_id)
            if existing is not None and existing.get("request_hash") != claim.request_hash:
                raise OperationalSagaConflict("scrape-job-binding-conflict")
            _scrape_jobs[claim.job_id] = job
        await _run_scrape_job(
            claim.job_id,
            str(payload["start_date"]),
            str(payload["end_date"]),
            bool(payload["force_rescrape"]),
            bool(payload["dry_run"]),
        )
        with _JOBS_LOCK:
            completed = dict(_scrape_jobs.get(claim.job_id) or {})
        if completed.get("status") != "completed" or not isinstance(completed.get("result"), dict):
            raise OperationalSagaError(str(completed.get("error") or "scrape-worker-failed"))
        result = dict(completed["result"])
        receipt = hashlib.sha256(
            (
                claim.idempotency_key
                + "|"
                + json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            ).encode()
        ).hexdigest()
        return EffectResult(result=result, receipt_hash=receipt)


class OperationalSagaRuntime:
    def __init__(
        self,
        config: OperationalSagaConfig,
        store: OperationalSagaStore | None = None,
        executor: OperationalEffectExecutor | None = None,
        *,
        worker_owner: str | None = None,
    ) -> None:
        self.config = config
        self._store = store
        self._executor = executor or ScrapeJobEffectExecutor(
            allow_unfenced_local_writes=config.mode is OperationalSagaMode.LOCAL_SQLITE
        )
        self._worker_owner = worker_owner or f"worker-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        if config.enabled and store is None:
            if config.mode is OperationalSagaMode.LOCAL_SQLITE:
                self._store = SQLiteOperationalSagaStore(config)
            else:
                try:
                    from supabase_client import get_client

                    self._store = SupabaseOperationalSagaStore(
                        get_client(), max_attempts=config.max_attempts
                    )
                except Exception as exc:
                    raise OperationalSagaUnavailable("supabase-operational-client-unavailable") from exc
        if not config.enabled and store is not None:
            raise OperationalSagaConfigError("disabled-runtime-store-forbidden")

    @property
    def enabled(self) -> bool:
        return self.config.enabled and self._store is not None

    def initialize(self) -> None:
        if not self.enabled:
            return
        assert self._store is not None
        self._store.initialize()

    def enqueue(self, request: EnqueueRequest) -> Mutation:
        if not self.enabled:
            return Mutation(MutationCode.UNAVAILABLE, "operational-runtime-disabled")
        assert self._store is not None
        return self._store.enqueue(request, int(time.time()))

    def get_job(self, job_id: str, owner_user_id: str) -> dict[str, Any] | None:
        if not self.enabled:
            raise OperationalSagaUnavailable("operational-runtime-disabled")
        assert self._store is not None
        return self._store.get_job(job_id, owner_user_id)

    def list_jobs(self, owner_user_id: str, limit: int) -> list[dict[str, Any]]:
        if not self.enabled:
            raise OperationalSagaUnavailable("operational-runtime-disabled")
        assert self._store is not None
        return self._store.list_jobs(owner_user_id, limit)

    async def run_once(self) -> Mutation:
        if not self.enabled or not self.config.worker_enabled:
            return Mutation(MutationCode.UNAVAILABLE, "operational-worker-disabled")
        if getattr(self._executor, "idempotent", None) is not True:
            return Mutation(MutationCode.UNAVAILABLE, "idempotent-executor-required")
        assert self._store is not None
        now = int(time.time())
        claimed = await asyncio.to_thread(
            self._store.claim_next,
            self._worker_owner,
            now,
            self.config.lease_seconds,
        )
        if claimed.code is not MutationCode.APPLIED or claimed.claim is None:
            return claimed
        claim = claimed.claim
        effect_task = asyncio.create_task(self._executor.execute(claim))
        heartbeat_interval = max(1.0, self.config.lease_seconds / 3)
        try:
            while True:
                done, _ = await asyncio.wait({effect_task}, timeout=heartbeat_interval)
                if effect_task in done:
                    effect = effect_task.result()
                    return await asyncio.to_thread(
                        self._store.complete, claim, effect, int(time.time())
                    )
                renewed = await asyncio.to_thread(
                    self._store.heartbeat,
                    claim,
                    int(time.time()),
                    self.config.lease_seconds,
                )
                if renewed.code is not MutationCode.APPLIED or renewed.claim is None:
                    effect_task.cancel()
                    try:
                        await effect_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    return Mutation(MutationCode.CONFLICT, "worker-lease-lost")
                claim = renewed.claim
        except asyncio.CancelledError:
            effect_task.cancel()
            raise
        except Exception as exc:
            return await asyncio.to_thread(
                self._store.fail,
                claim,
                f"worker-error-{type(exc).__name__.lower()}",
                int(time.time()),
            )

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = await self.run_once()
            except Exception:
                # A transient shared-store failure must not silently terminate
                # the recovery pump. No effect is performed unless a valid
                # claim was obtained and the idempotent executor was accepted.
                result = Mutation(MutationCode.UNAVAILABLE, "operational-worker-cycle-failed")
            if result.code in {MutationCode.NOT_FOUND, MutationCode.UNAVAILABLE}:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.config.poll_interval_ms / 1000
                    )
                except asyncio.TimeoutError:
                    pass

    async def start(self) -> None:
        if not self.enabled:
            return
        await asyncio.to_thread(self.initialize)
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop(), name="phase3n-operational-saga-worker")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


_runtime: OperationalSagaRuntime | None = None


def get_operational_saga_runtime() -> OperationalSagaRuntime:
    global _runtime
    if _runtime is None:
        _runtime = OperationalSagaRuntime(load_operational_saga_config())
    return _runtime


def set_operational_saga_runtime_for_tests(runtime: OperationalSagaRuntime | None) -> None:
    global _runtime
    _runtime = runtime


async def start_operational_saga_worker() -> None:
    await get_operational_saga_runtime().start()


async def stop_operational_saga_worker() -> None:
    await get_operational_saga_runtime().stop()

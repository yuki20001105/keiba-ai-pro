"""Isolated, fail-closed ledger for offline model comparison experiments.

This module has no application imports, process management or model publication.
The scheduler owns process termination and artifact verification. Expired leases
are *unknown*, never automatically requeued: a timed-out worker may still live.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator


class ExperimentError(RuntimeError):
    """Invalid experiment operation; no work should be launched on this error."""


class ExperimentConflict(ExperimentError):
    pass


class LeaseLost(ExperimentError):
    pass


class ExperimentCancelled(LeaseLost):
    pass


@dataclass(frozen=True)
class TrialLease:
    experiment_id: str
    trial_id: str
    worker_id: str
    device: str
    memory_mb: int
    payload: dict[str, Any]
    fencing_token: int
    record_version: int
    lease_expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TrialLease":
        return cls(**value)


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_OPEN_EXPERIMENTS = ("active", "cancelling", "blocked")
_TERMINAL_TRIALS = ("completed", "failed", "cancelled")


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("Identifier must be 1-128 safe ASCII characters")
    return value


def _json(value: dict[str, Any]) -> str:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ttl(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Lease TTL must be a positive finite number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Lease TTL must be a positive finite number")
    return float(value)


class ExperimentStore:
    """A dedicated SQLite file; callers must never pass the application DB.

    Contract ``capacity`` defaults to ``{"cpu": 1, "gpu": 1}``; CPU capacity
    accepts 1 or 2 and GPU accepts 0 or 1. A single active/blocked experiment is
    admitted per ledger. Memory admission remains the scheduler's responsibility.
    All timestamps are UTC Unix seconds. ``clock`` exists for deterministic tests.
    """

    def __init__(self, db_path: str | Path, *, clock: Callable[[], float] = time.time):
        self.db_path = Path(db_path)
        self._clock = clock

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            expected = {"experiment_ledger_meta", "experiments", "experiment_trials", "experiment_fences"}
            if tables and (not tables.issubset(expected) or "experiment_ledger_meta" not in tables):
                raise ExperimentConflict("Refusing a database that is not an experiment ledger")
            statements = [
                "CREATE TABLE IF NOT EXISTS experiment_ledger_meta (version INTEGER NOT NULL)",
                """CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY, contract_json TEXT NOT NULL,
                    contract_sha256 TEXT NOT NULL, status TEXT NOT NULL,
                    cpu_capacity INTEGER NOT NULL, gpu_capacity INTEGER NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
                """CREATE UNIQUE INDEX IF NOT EXISTS one_open_experiment
                    ON experiments((1)) WHERE status IN ('active','cancelling','blocked')""",
                """CREATE TABLE IF NOT EXISTS experiment_trials (
                    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
                    trial_id TEXT NOT NULL, payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                    device TEXT NOT NULL CHECK(device IN ('cpu','gpu')), memory_mb INTEGER NOT NULL,
                    status TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    worker_id TEXT, fencing_token INTEGER NOT NULL DEFAULT 0,
                    record_version INTEGER NOT NULL DEFAULT 1, lease_expires_at REAL,
                    result_json TEXT, result_sha256 TEXT, artifact_json TEXT, error TEXT,
                    progress_stage TEXT, progress_pct REAL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    started_at REAL, finished_at REAL,
                    PRIMARY KEY(experiment_id,trial_id))""",
                """CREATE INDEX IF NOT EXISTS experiment_trial_admission
                    ON experiment_trials(experiment_id,status,device,created_at)""",
                """CREATE TABLE IF NOT EXISTS experiment_fences (
                    fencing_token INTEGER PRIMARY KEY AUTOINCREMENT)""",
            ]
            for statement in statements:
                connection.execute(statement)
            versions = connection.execute("SELECT version FROM experiment_ledger_meta").fetchall()
            if not versions:
                connection.execute("INSERT INTO experiment_ledger_meta(version) VALUES (1)")
            elif len(versions) != 1 or versions[0][0] != 1:
                raise ExperimentConflict("Unsupported experiment ledger schema")

    @staticmethod
    def _experiment(connection: sqlite3.Connection, experiment_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,)).fetchone()
        if row is None:
            raise ExperimentError("Experiment does not exist")
        return row

    @staticmethod
    def _expire(connection: sqlite3.Connection, now: float) -> None:
        connection.execute("""UPDATE experiment_trials SET status='unknown',
            error='lease-expired; worker termination must be confirmed manually',
            record_version=record_version+1, updated_at=?
            WHERE status IN ('running','cancelling') AND lease_expires_at<=?""", (now, now))
        connection.execute("""UPDATE experiments SET status='blocked', updated_at=?
            WHERE status IN ('active','cancelling') AND EXISTS (
                SELECT 1 FROM experiment_trials t WHERE t.experiment_id=experiments.experiment_id
                AND t.status='unknown')""", (now,))

    def create_experiment(self, experiment_id: str, contract: dict[str, Any]) -> None:
        _identifier(experiment_id)
        contract_json = _json(contract)
        capacity = contract.get("capacity", {"cpu": 1, "gpu": 1})
        if not isinstance(capacity, dict):
            raise ValueError("capacity must be a JSON object")
        cpu, gpu = capacity.get("cpu", 1), capacity.get("gpu", 1)
        if type(cpu) is not int or cpu not in (1, 2) or type(gpu) is not int or gpu not in (0, 1):
            raise ValueError("Supported capacity is CPU 1-2 and GPU 0-1")
        now = self._clock()
        with self._transaction() as connection:
            self._expire(connection, now)
            if connection.execute("SELECT 1 FROM experiments WHERE status IN ('active','cancelling','blocked')").fetchone():
                raise ExperimentConflict("An experiment is already active or requires investigation")
            if connection.execute("SELECT 1 FROM experiments WHERE experiment_id=?", (experiment_id,)).fetchone():
                raise ExperimentConflict("Experiment ID already exists")
            connection.execute("""INSERT INTO experiments
                (experiment_id,contract_json,contract_sha256,status,cpu_capacity,gpu_capacity,created_at,updated_at)
                VALUES (?,?,?,'active',?,?,?,?)""",
                (experiment_id, contract_json, _digest(contract_json), cpu, gpu, now, now))

    def enqueue(self, experiment_id: str, trial_id: str, payload: dict[str, Any],
                device: str, memory_mb: int = 0) -> None:
        _identifier(experiment_id)
        _identifier(trial_id)
        if device not in ("cpu", "gpu"):
            raise ValueError("device must be cpu or gpu")
        if type(memory_mb) is not int or memory_mb < 0:
            raise ValueError("memory_mb must be a nonnegative integer")
        payload_json = _json(payload)
        now = self._clock()
        with self._transaction() as connection:
            self._expire(connection, now)
            experiment = self._experiment(connection, experiment_id)
            if experiment["status"] != "active" or experiment["cancel_requested"]:
                raise ExperimentConflict("Experiment does not accept new trials")
            if not experiment[f"{device}_capacity"]:
                raise ExperimentConflict("Requested device is disabled by the experiment contract")
            if connection.execute("SELECT 1 FROM experiment_trials WHERE experiment_id=? AND trial_id=?",
                                  (experiment_id, trial_id)).fetchone():
                raise ExperimentConflict("Trial ID already exists in this experiment")
            connection.execute("""INSERT INTO experiment_trials
                (experiment_id,trial_id,payload_json,payload_sha256,device,memory_mb,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,'queued',?,?)""",
                (experiment_id, trial_id, payload_json, _digest(payload_json), device, memory_mb, now, now))

    @staticmethod
    def _lease(row: sqlite3.Row) -> TrialLease:
        return TrialLease(row["experiment_id"], row["trial_id"], row["worker_id"], row["device"],
                          row["memory_mb"], json.loads(row["payload_json"]), row["fencing_token"],
                          row["record_version"], row["lease_expires_at"])

    def claim(self, worker_id: str, device: str, ttl_seconds: float = 60) -> TrialLease | None:
        _identifier(worker_id)
        ttl = _ttl(ttl_seconds)
        if device not in ("cpu", "gpu"):
            raise ValueError("device must be cpu or gpu")
        now = self._clock()
        with self._transaction() as connection:
            self._expire(connection, now)
            experiment = connection.execute("SELECT * FROM experiments WHERE status='active' AND cancel_requested=0").fetchone()
            if experiment is None:
                return None
            active = connection.execute("""SELECT count(*) FROM experiment_trials
                WHERE experiment_id=? AND device=? AND status IN ('running','cancelling','unknown')""",
                (experiment["experiment_id"], device)).fetchone()[0]
            if active >= experiment[f"{device}_capacity"]:
                return None
            row = connection.execute("""SELECT * FROM experiment_trials
                WHERE experiment_id=? AND device=? AND status='queued' AND cancel_requested=0
                ORDER BY created_at, rowid LIMIT 1""", (experiment["experiment_id"], device)).fetchone()
            if row is None:
                return None
            fence = connection.execute("INSERT INTO experiment_fences DEFAULT VALUES").lastrowid
            connection.execute("""UPDATE experiment_trials SET status='running', worker_id=?,
                fencing_token=?,record_version=record_version+1,lease_expires_at=?,started_at=?,updated_at=?
                WHERE experiment_id=? AND trial_id=? AND status='queued' AND record_version=?""",
                (worker_id, fence, now + ttl, now, now, row["experiment_id"], row["trial_id"], row["record_version"]))
            claimed = connection.execute("SELECT * FROM experiment_trials WHERE experiment_id=? AND trial_id=?",
                                         (row["experiment_id"], row["trial_id"])).fetchone()
            return self._lease(claimed)

    @staticmethod
    def _current(connection: sqlite3.Connection, lease: TrialLease, now: float,
                 *, allow_cancel: bool = False) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM experiment_trials WHERE experiment_id=? AND trial_id=?",
                                 (lease.experiment_id, lease.trial_id)).fetchone()
        if row is None or row["worker_id"] != lease.worker_id or row["fencing_token"] != lease.fencing_token:
            raise LeaseLost("Worker does not own this trial")
        if row["status"] not in ("running", "cancelling") or row["lease_expires_at"] <= now:
            raise LeaseLost("Trial lease is expired or no longer running")
        if row["cancel_requested"]:
            if allow_cancel:
                # Cancel increments the version; the same fenced owner may only
                # acknowledge termination, never publish a result with its old lease.
                return row
            raise ExperimentCancelled("Trial cancellation has been requested")
        if row["record_version"] != lease.record_version:
            raise LeaseLost("Stale lease record version")
        return row

    def heartbeat(self, lease: TrialLease, ttl_seconds: float = 60) -> TrialLease:
        ttl = _ttl(ttl_seconds)
        now = self._clock()
        with self._transaction() as connection:
            self._expire(connection, now)
            row = self._current(connection, lease, now)
            connection.execute("""UPDATE experiment_trials SET lease_expires_at=?,updated_at=?,
                record_version=record_version+1 WHERE experiment_id=? AND trial_id=? AND record_version=?""",
                (now + ttl, now, lease.experiment_id, lease.trial_id, row["record_version"]))
            updated = connection.execute("SELECT * FROM experiment_trials WHERE experiment_id=? AND trial_id=?",
                                         (lease.experiment_id, lease.trial_id)).fetchone()
            return self._lease(updated)

    def update_progress(self, lease: TrialLease, stage: str, pct: float) -> None:
        if not isinstance(stage, str) or not stage.strip() or len(stage) > 200:
            raise ValueError("Progress stage must be a nonempty string of at most 200 characters")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not math.isfinite(pct) or not 0 <= pct <= 100:
            raise ValueError("Progress pct must be between 0 and 100")
        now = self._clock()
        with self._transaction() as connection:
            self._expire(connection, now)
            self._current(connection, lease, now)
            connection.execute("""UPDATE experiment_trials SET progress_stage=?,progress_pct=?,updated_at=?
                WHERE experiment_id=? AND trial_id=?""", (stage, pct, now, lease.experiment_id, lease.trial_id))

    def complete(self, lease: TrialLease, result: dict[str, Any], artifact: dict[str, Any] | None = None) -> None:
        result_json = _json(result)
        artifact_json = None if artifact is None else _json(artifact)
        if artifact is not None and "sha256" in artifact and not _DIGEST.fullmatch(str(artifact["sha256"])):
            raise ValueError("Artifact sha256 must be a lowercase SHA-256 digest")
        now = self._clock()
        with self._transaction() as connection:
            self._expire(connection, now)
            self._current(connection, lease, now)
            connection.execute("""UPDATE experiment_trials SET status='completed', result_json=?,
                result_sha256=?,artifact_json=?,record_version=record_version+1,updated_at=?,finished_at=?,
                lease_expires_at=NULL,progress_stage='completed',progress_pct=100
                WHERE experiment_id=? AND trial_id=? AND record_version=?""",
                (result_json, _digest(result_json), artifact_json, now, now,
                 lease.experiment_id, lease.trial_id, lease.record_version))

    def fail(self, lease: TrialLease, error: str) -> None:
        if not isinstance(error, str) or not error.strip():
            raise ValueError("Failure reason must be a nonempty string")
        now = self._clock()
        with self._transaction() as connection:
            self._expire(connection, now)
            row = self._current(connection, lease, now, allow_cancel=True)
            status = "cancelled" if row["cancel_requested"] else "failed"
            connection.execute("""UPDATE experiment_trials SET status=?,error=?,lease_expires_at=NULL,
                record_version=record_version+1,updated_at=?,finished_at=? WHERE experiment_id=? AND trial_id=?""",
                (status, error, now, now, lease.experiment_id, lease.trial_id))
            self._finish_cancelled(connection, lease.experiment_id, now)

    def cancel(self, experiment_id: str, trial_id: str | None = None) -> None:
        now = self._clock()
        with self._transaction() as connection:
            self._expire(connection, now)
            experiment = self._experiment(connection, experiment_id)
            if trial_id is not None:
                if not connection.execute("SELECT 1 FROM experiment_trials WHERE experiment_id=? AND trial_id=?",
                                          (experiment_id, trial_id)).fetchone():
                    raise ExperimentError("Trial does not exist")
            elif experiment["status"] in _OPEN_EXPERIMENTS:
                connection.execute("""UPDATE experiments SET cancel_requested=1,
                    status=CASE WHEN status='blocked' THEN 'blocked' ELSE 'cancelling' END,updated_at=?
                    WHERE experiment_id=?""", (now, experiment_id))
            clause, values = "experiment_id=?", [experiment_id]
            if trial_id is not None:
                clause += " AND trial_id=?"
                values.append(trial_id)
            connection.execute(f"""UPDATE experiment_trials SET cancel_requested=1,
                status=CASE WHEN status='queued' THEN 'cancelled' WHEN status='running' THEN 'cancelling' ELSE status END,
                finished_at=CASE WHEN status='queued' THEN ? ELSE finished_at END,
                record_version=record_version+1,updated_at=?
                WHERE {clause} AND status IN ('queued','running','cancelling','unknown') AND cancel_requested=0""",
                [now, now, *values])
            self._finish_cancelled(connection, experiment_id, now)

    @staticmethod
    def _finish_cancelled(connection: sqlite3.Connection, experiment_id: str, now: float) -> None:
        connection.execute("""UPDATE experiments SET status='cancelled',updated_at=?
            WHERE experiment_id=? AND cancel_requested=1 AND status='cancelling'
            AND NOT EXISTS (SELECT 1 FROM experiment_trials WHERE experiment_id=?
                AND status NOT IN ('completed','failed','cancelled'))""", (now, experiment_id, experiment_id))

    def acknowledge_cancel(self, lease: TrialLease) -> None:
        """Call only after the scheduler has confirmed its worker is terminated."""
        now = self._clock()
        with self._transaction() as connection:
            self._expire(connection, now)
            row = self._current(connection, lease, now, allow_cancel=True)
            if not row["cancel_requested"]:
                raise ExperimentConflict("Trial has no cancellation request")
            connection.execute("""UPDATE experiment_trials SET status='cancelled',lease_expires_at=NULL,
                record_version=record_version+1,updated_at=?,finished_at=? WHERE experiment_id=? AND trial_id=?""",
                (now, now, lease.experiment_id, lease.trial_id))
            self._finish_cancelled(connection, lease.experiment_id, now)

    def finish_experiment(self, experiment_id: str) -> None:
        now = self._clock()
        with self._transaction() as connection:
            self._expire(connection, now)
            experiment = self._experiment(connection, experiment_id)
            if experiment["status"] not in _OPEN_EXPERIMENTS:
                return
            states = [row[0] for row in connection.execute(
                "SELECT status FROM experiment_trials WHERE experiment_id=?", (experiment_id,))]
            if any(state not in _TERMINAL_TRIALS for state in states):
                raise ExperimentConflict("Experiment still has unfinished or unknown trials")
            status = "cancelled" if experiment["cancel_requested"] or "cancelled" in states else (
                "failed" if "failed" in states else "completed")
            connection.execute("UPDATE experiments SET status=?,updated_at=? WHERE experiment_id=?",
                               (status, now, experiment_id))

    def snapshot(self, experiment_id: str) -> dict[str, Any]:
        with self._transaction() as connection:
            self._expire(connection, self._clock())
            experiment = dict(self._experiment(connection, experiment_id))
            experiment["contract"] = json.loads(experiment.pop("contract_json"))
            trials = []
            for row in connection.execute("SELECT * FROM experiment_trials WHERE experiment_id=? ORDER BY created_at,rowid",
                                          (experiment_id,)):
                trial = dict(row)
                for key in ("payload", "result", "artifact"):
                    value = trial.pop(f"{key}_json")
                    trial[key] = None if value is None else json.loads(value)
                trial["state"] = trial["status"]
                trials.append(trial)
            experiment["trials"] = trials
            experiment["state"] = experiment["status"]
            return experiment

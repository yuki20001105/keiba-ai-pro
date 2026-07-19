from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Sequence
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = ROOT / "scripts" / "verify_phase3j_saga_outbox_runtime.py"
CONTRACT = ROOT / "python-api" / "tests" / "fixtures" / "phase3j_saga_outbox_failure_matrix_v1.json"
MIGRATION = ROOT / "supabase" / "migrations" / "20260720_scrape_execution_reservation.sql"
PHASE3G_BOOTSTRAP = ROOT / "supabase" / "tests" / "phase3g_review_ledger_bootstrap.sql"
PHASE3G_MIGRATION = ROOT / "supabase" / "migrations" / "20260718_scrape_uncertainty_review_ledger.sql"
PHASE3J_BOOTSTRAP = ROOT / "supabase" / "tests" / "phase3j_execution_reservation_bootstrap.sql"
PHASE3J_RUNTIME_CONTRACT = ROOT / "supabase" / "tests" / "phase3j_execution_reservation_runtime_contract.sql"
RUNTIME_DIR = ROOT / "python-api" / "scraping"
REPORT = ROOT / "reports" / "phase3j_saga_outbox_runtime.json"
IMAGE = "postgres:17.6-bookworm@sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3"
DATABASE = "phase3j_runtime"
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{12,64}$")
LOCAL_DOCKER_ENDPOINTS = ("unix://", "npipe://")


class GateFailure(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class OperationalEffectCounter:
    worker_dispatch: int = 0
    network_call: int = 0
    thread_start: int = 0
    operational_write: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "worker_dispatch": self.worker_dispatch,
            "network_call": self.network_call,
            "thread_start": self.thread_start,
            "operational_write": self.operational_write,
        }

    @property
    def total(self) -> int:
        return sum(self.as_dict().values())


def _safe_environment() -> dict[str, str]:
    allowed = (
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "HOME", "TMP", "TEMP",
        "DOCKER_CONFIG",
    )
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _command(args: Sequence[str], *, input_text: str | None = None, timeout: int = 60) -> CommandResult:
    try:
        result = subprocess.run(
            list(args), input=input_text, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
            shell=False, env=_safe_environment(),
        )
    except FileNotFoundError as exc:
        raise GateFailure("command-missing") from exc
    except subprocess.TimeoutExpired as exc:
        raise GateFailure("command-timeout") from exc
    return CommandResult(result.returncode, result.stdout, result.stderr)


def _require_success(result: CommandResult, code: str) -> str:
    if result.returncode != 0:
        raise GateFailure(code)
    return result.stdout.strip()


def _docker(*args: str, timeout: int = 60) -> CommandResult:
    return _command(("docker", *args), timeout=timeout)


def _psql(container: str, sql: str, *, timeout: int = 90) -> CommandResult:
    return _command(
        (
            "docker", "exec", "-i", container, "psql", "-X", "--no-psqlrc", "--quiet",
            "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1", "--set",
            "VERBOSITY=verbose", "--host", "/var/run/postgresql", "--port", "5432",
            "--username", "postgres", "--dbname", DATABASE,
        ),
        input_text=sql,
        timeout=timeout,
    )


def _load_module(path: Path, name: str) -> ModuleType:
    if not path.is_file():
        raise GateFailure(f"{name}-module-unavailable")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise GateFailure(f"{name}-module-unavailable")
    module = importlib.util.module_from_spec(spec)
    try:
        sys.modules[name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        raise GateFailure(f"{name}-module-unavailable") from exc
    return module


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    try:
        raw = path.read_bytes().replace(b"\r\n", b"\n")
    except OSError as exc:
        raise GateFailure("required-input-unavailable") from exc
    return hashlib.sha256(raw).hexdigest()


def _schema_sha256(store_path: Path) -> str:
    verifier = _load_module(VERIFIER_PATH, "phase3j_schema_verifier")
    value = verifier._schema_sha256(store_path)
    if not isinstance(value, str):
        raise GateFailure("sqlite-schema-unavailable")
    return value


def _repository_head(repository_root: Path = ROOT) -> str:
    result = _command(
        ("git", "-C", str(repository_root.resolve()), "rev-parse", "--verify", "HEAD"),
        timeout=15,
    )
    actual = _require_success(result, "checkout-head-unavailable").lower()
    if not COMMIT_PATTERN.fullmatch(actual):
        raise GateFailure("checkout-head-invalid")
    return actual


def _tested_commit(expected_commit: str | None, repository_root: Path = ROOT) -> str:
    candidate = (expected_commit or "").lower()
    if not COMMIT_PATTERN.fullmatch(candidate):
        raise GateFailure("expected-commit-required")
    if _repository_head(repository_root) != candidate:
        raise GateFailure("checkout-head-mismatch")
    return candidate


def _load_contract(path: Path) -> tuple[dict[str, Any], ModuleType]:
    verifier = _load_module(VERIFIER_PATH, "phase3j_contract_verifier")
    value, failures = verifier.load_contract(path)
    if failures or not verifier._contract_valid(value):
        raise GateFailure("contract-invalid")
    assert isinstance(value, dict)
    return value, verifier


def _wait_for_postgres(container: str, timeout_seconds: int = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ready = _docker(
            "exec", container, "pg_isready", "--host", "/var/run/postgresql", "--port", "5432",
            "--username", "postgres", "--dbname", DATABASE, timeout=10,
        )
        if ready.returncode == 0:
            return
        running = _docker("inspect", "--format", "{{.State.Running}}", container, timeout=10)
        if running.returncode != 0 or running.stdout.strip().lower() != "true":
            raise GateFailure("container-exited-before-ready")
        time.sleep(1)
    raise GateFailure("postgres-health-timeout")


def _container_absent(name: str) -> bool:
    result = _docker("ps", "--all", "--filter", f"name=^/{name}$", "--format", "{{.Names}}", timeout=15)
    return result.returncode == 0 and result.stdout.strip() == ""


@contextmanager
def _operational_effect_guard(counter: OperationalEffectCounter, workspace: Path):
    """Reject application effects while allowing the disposable SQLite file itself."""

    import _thread
    import builtins
    import io
    import multiprocessing
    import socket
    import sqlite3
    import threading
    import urllib.request

    original_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open
    original_unlink = os.unlink
    original_remove = os.remove
    original_rename = os.rename
    original_replace = os.replace
    original_sqlite_connect = sqlite3.connect

    def _inside_workspace(value: Any) -> bool:
        try:
            candidate = Path(value).resolve(strict=False)
            candidate.relative_to(workspace.resolve(strict=False))
            return True
        except (TypeError, ValueError, OSError):
            return False

    def guarded_open(file: Any, *args: Any, **kwargs: Any):
        mode = kwargs.get("mode", args[0] if args else "r")
        if any(marker in str(mode) for marker in ("w", "a", "+", "x")) and not _inside_workspace(file):
            counter.operational_write += 1
            raise GateFailure("operational-write-attempted")
        return original_open(file, *args, **kwargs)

    def guarded_os_open(path: Any, flags: int, *args: Any, **kwargs: Any):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
        if flags & write_flags and not _inside_workspace(path):
            counter.operational_write += 1
            raise GateFailure("operational-write-attempted")
        return original_os_open(path, flags, *args, **kwargs)

    def guarded_remove(path: Any, *args: Any, **kwargs: Any):
        if not _inside_workspace(path):
            counter.operational_write += 1
            raise GateFailure("operational-write-attempted")
        original = original_unlink if guarded_remove.operation == "unlink" else original_remove
        return original(path, *args, **kwargs)

    def guarded_unlink(path: Any, *args: Any, **kwargs: Any):
        if not _inside_workspace(path):
            counter.operational_write += 1
            raise GateFailure("operational-write-attempted")
        return original_unlink(path, *args, **kwargs)

    guarded_remove.operation = "remove"  # type: ignore[attr-defined]

    def guarded_move(source: Any, destination: Any, *args: Any, **kwargs: Any):
        if not _inside_workspace(source) or not _inside_workspace(destination):
            counter.operational_write += 1
            raise GateFailure("operational-write-attempted")
        original = original_replace if guarded_move.operation == "replace" else original_rename
        return original(source, destination, *args, **kwargs)

    def guarded_replace(source: Any, destination: Any, *args: Any, **kwargs: Any):
        if not _inside_workspace(source) or not _inside_workspace(destination):
            counter.operational_write += 1
            raise GateFailure("operational-write-attempted")
        return original_replace(source, destination, *args, **kwargs)

    def guarded_path_mutation(operation: Callable[..., Any]):
        def mutate(path: Any, *args: Any, **kwargs: Any):
            if not _inside_workspace(path):
                counter.operational_write += 1
                raise GateFailure("operational-write-attempted")
            return operation(path, *args, **kwargs)
        return mutate

    def guarded_two_path_mutation(operation: Callable[..., Any]):
        def mutate(source: Any, destination: Any, *args: Any, **kwargs: Any):
            if not _inside_workspace(source) or not _inside_workspace(destination):
                counter.operational_write += 1
                raise GateFailure("operational-write-attempted")
            return operation(source, destination, *args, **kwargs)
        return mutate

    def guarded_sqlite_connect(database: Any, *args: Any, **kwargs: Any):
        if database != ":memory:" and not _inside_workspace(database):
            counter.operational_write += 1
            raise GateFailure("operational-write-attempted")
        return original_sqlite_connect(database, *args, **kwargs)

    guarded_move.operation = "rename"  # type: ignore[attr-defined]

    def blocked(name: str, field: str):
        def reject(*_args: Any, **_kwargs: Any):
            setattr(counter, field, getattr(counter, field) + 1)
            raise GateFailure(name)
        return reject

    with ExitStack() as stack:
        stack.enter_context(patch.object(builtins, "open", guarded_open))
        stack.enter_context(patch.object(io, "open", guarded_open))
        stack.enter_context(patch.object(os, "open", guarded_os_open))
        stack.enter_context(patch.object(os, "remove", guarded_remove))
        stack.enter_context(patch.object(os, "unlink", guarded_unlink))
        stack.enter_context(patch.object(os, "rename", guarded_move))
        stack.enter_context(patch.object(os, "replace", guarded_replace))
        for path_api in (
            "mkdir", "makedirs", "rmdir", "removedirs", "chmod", "lchmod", "chown", "lchown",
            "truncate", "utime", "mkfifo", "mknod",
        ):
            if hasattr(os, path_api):
                stack.enter_context(
                    patch.object(os, path_api, guarded_path_mutation(getattr(os, path_api)))
                )
        for two_path_api in ("link", "symlink"):
            if hasattr(os, two_path_api):
                stack.enter_context(
                    patch.object(os, two_path_api, guarded_two_path_mutation(getattr(os, two_path_api)))
                )
        stack.enter_context(patch.object(sqlite3, "connect", guarded_sqlite_connect))
        stack.enter_context(patch.object(socket, "socket", blocked("network-call-attempted", "network_call")))
        stack.enter_context(patch.object(socket, "create_connection", blocked("network-call-attempted", "network_call")))
        stack.enter_context(patch.object(urllib.request, "urlopen", blocked("network-call-attempted", "network_call")))
        stack.enter_context(patch.object(threading.Thread, "start", blocked("thread-start-attempted", "thread_start")))
        stack.enter_context(patch.object(_thread, "start_new_thread", blocked("thread-start-attempted", "thread_start")))
        stack.enter_context(patch.object(subprocess, "Popen", blocked("process-dispatch-attempted", "worker_dispatch")))
        stack.enter_context(patch.object(subprocess, "run", blocked("process-dispatch-attempted", "worker_dispatch")))
        stack.enter_context(patch.object(os, "system", blocked("process-dispatch-attempted", "worker_dispatch")))
        stack.enter_context(patch.object(os, "popen", blocked("process-dispatch-attempted", "worker_dispatch")))
        for process_api in (
            "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
            "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
            "fork", "forkpty", "posix_spawn", "posix_spawnp",
        ):
            if hasattr(os, process_api):
                stack.enter_context(
                    patch.object(os, process_api, blocked("process-dispatch-attempted", "worker_dispatch"))
                )
        stack.enter_context(
            patch.object(
                multiprocessing.Process,
                "start",
                blocked("process-dispatch-attempted", "worker_dispatch"),
            )
        )
        yield


def _run_sqlite_contract(
    workspace: Path,
    runtime_dir: Path,
    counter: OperationalEffectCounter,
) -> tuple[dict[str, bool], dict[str, bool], int]:
    """Exercise only disposable stores through the repository-owned API."""

    python_api = runtime_dir.parent
    inserted = False
    if str(python_api) not in sys.path:
        sys.path.insert(0, str(python_api))
        inserted = True
    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        import importlib
        import sqlite3

        contract = importlib.import_module("scraping.cross_store_saga_contract")
        config_module = importlib.import_module("scraping.saga_runtime_config")
        store_module = importlib.import_module("scraping.cross_store_saga_store")
        ports_module = importlib.import_module("scraping.cross_store_saga_ports")
        runtime_module = importlib.import_module("scraping.cross_store_saga_runtime")
    except Exception as exc:
        raise GateFailure("sqlite-runtime-api-mismatch") from exc
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
        if inserted:
            sys.path.remove(str(python_api))

    required = {
        config_module: ("SagaRuntimeConfig",),
        store_module: (
            "SagaStore", "SagaStoreCorruptionError", "StoreResultCode", "OutboxState",
        ),
        ports_module: ("PortOutcome", "PortResult"),
        runtime_module: ("SagaRuntime",),
        contract: ("SagaBinding", "SagaEvent", "EventKind", "SagaState"),
    }
    if any(not hasattr(module, name) for module, names in required.items() for name in names):
        raise GateFailure("sqlite-runtime-api-mismatch")

    scenarios = {
        key: False
        for key in (
            "sqlite_prepare_rollback", "sqlite_crash_replay", "sqlite_claim_race",
            "sqlite_lease_fencing", "sqlite_stale_ack_rejected",
            "sqlite_ambiguous_remote_stops", "sqlite_compensation_replay",
            "sqlite_corruption_unavailable",
        )
    }
    facts = {
        "atomic_prepare": False,
        "stable_operation_job_binding": False,
        "replay_idempotency": False,
        "single_claim_winner": False,
        "lease_fencing": False,
        "stale_ack_rejected": False,
        "ambiguous_remote_no_dispatch": False,
        "compensation_idempotent": False,
        "corruption_unavailable_fail_closed": False,
        "zero_operational_effects": False,
    }
    writes = 0
    namespace = uuid.UUID("1fd8fd4c-e1bd-48d4-8fc0-12e560a99247")

    def stable(label: str) -> str:
        return str(uuid.uuid5(namespace, label))

    def binding(label: str):
        return contract.SagaBinding(
            operation_id=stable(f"{label}:operation"),
            review_id=stable(f"{label}:review"),
            review_version=1,
            owner_user_id=stable(f"{label}:owner"),
            job_id=stable(f"{label}:job"),
            request_hash=hashlib.sha256(f"{label}:request".encode("utf-8")).hexdigest(),
        )

    def make_store(label: str, fault: Callable[[str], None] | None = None):
        config = config_module.SagaRuntimeConfig.ci_disposable(workspace / f"{label}.sqlite")
        store = store_module.SagaStore(config, fault_injector=fault)
        initialized = store.initialize()
        if initialized.code is not store_module.StoreResultCode.APPLIED:
            raise GateFailure("sqlite-initialize-failed")
        return config, store

    def add_rows(store: Any) -> None:
        nonlocal writes
        counts = store.table_counts()
        if frozenset(counts) != frozenset({"phase3j_jobs", "phase3j_sagas", "phase3j_saga_events", "phase3j_outbox"}):
            raise GateFailure("sqlite-schema-drift")
        writes += sum(counts.values())

    # Atomic rollback at every preparation checkpoint.
    rollback_ok = True
    for checkpoint in ("after-job-insert", "after-saga-insert", "after-outbox-insert"):
        def fail(name: str, expected: str = checkpoint) -> None:
            if name == expected:
                raise RuntimeError("phase3j-injected-crash")
        _, store = make_store(f"rollback-{checkpoint}", fail)
        try:
            store.prepare(binding(f"rollback-{checkpoint}"), 100)
            rollback_ok = False
        except RuntimeError:
            pass
        rollback_ok = rollback_ok and all(value == 0 for value in store.table_counts().values())
    scenarios["sqlite_prepare_rollback"] = rollback_ok
    facts["atomic_prepare"] = rollback_ok

    # Reopen the same file and replay preparation after a modeled process crash.
    cfg, store = make_store("crash-replay")
    original_binding = binding("crash-replay")
    first = store.prepare(original_binding, 100)
    reopened = store_module.SagaStore(cfg)
    replay = reopened.prepare(original_binding, 101)
    scenarios["sqlite_crash_replay"] = (
        first.code is store_module.StoreResultCode.APPLIED
        and replay.code is store_module.StoreResultCode.DUPLICATE
        and replay.snapshot is not None
        and replay.snapshot.binding == original_binding
        and first.outbox is not None
        and replay.outbox is not None
        and replay.outbox.intent.intent_id == first.outbox.intent.intent_id
    )
    facts["stable_operation_job_binding"] = scenarios["sqlite_crash_replay"]
    facts["replay_idempotency"] = scenarios["sqlite_crash_replay"]
    add_rows(reopened)

    # Two independently opened stores contend without a Python worker thread.
    cfg, first_store = make_store("claim-race")
    prepared = first_store.prepare(binding("claim-race"), 100)
    second_store = store_module.SagaStore(cfg)
    first_claim = first_store.claim(prepared.outbox.intent.intent_id, "claim-a", 110, 10)
    second_claim = second_store.claim(prepared.outbox.intent.intent_id, "claim-b", 110, 10)
    scenarios["sqlite_claim_race"] = (
        first_claim.code is store_module.StoreResultCode.APPLIED
        and second_claim.code is store_module.StoreResultCode.CONFLICT
    )
    facts["single_claim_winner"] = scenarios["sqlite_claim_race"]
    add_rows(first_store)

    # Expiry increases fencing; a stale claimant cannot acknowledge.
    _, store = make_store("lease-fencing")
    prepared = store.prepare(binding("lease-fencing"), 100)
    first_claim = store.claim(prepared.outbox.intent.intent_id, "lease-a", 110, 5).claim
    recovered = store.recover_outbox(prepared.snapshot.binding.operation_id, 115)
    second_claim = store.claim(prepared.outbox.intent.intent_id, "lease-b", 116, 10).claim
    if first_claim is not None and second_claim is not None:
        stale = store.acknowledge(first_claim, "a" * 64, "confirmed", 117)
        accepted = store.acknowledge(second_claim, "b" * 64, "confirmed", 117)
        scenarios["sqlite_lease_fencing"] = (
            recovered.recovered_count == 1
            and second_claim.fencing_token == first_claim.fencing_token + 1
        )
        scenarios["sqlite_stale_ack_rejected"] = (
            stale.code is store_module.StoreResultCode.CONFLICT
            and accepted.code is store_module.StoreResultCode.APPLIED
        )
    facts["lease_fencing"] = scenarios["sqlite_lease_fencing"]
    facts["stale_ack_rejected"] = scenarios["sqlite_stale_ack_rejected"]
    add_rows(store)

    # Ambiguous observation is persisted as blocked, never worker dispatch.
    config, store = make_store("ambiguous")
    runtime = runtime_module.SagaRuntime(config, store)
    prepared = runtime.prepare(binding("ambiguous"), 100)
    claim = store.claim(prepared.outbox.intent.intent_id, "observer-a", 110, 30).claim
    if claim is not None:
        observed = runtime.settle_observation(
            claim,
            ports_module.PortResult(
                ports_module.PortOutcome.AMBIGUOUS,
                "ambiguous-remote",
                claim.intent.intent_id,
                claim.intent.binding_hash,
                claim.fencing_token,
            ),
            111,
        )
        record = store.load_outbox(claim.intent.intent_id)
        replay_claim = store.claim(claim.intent.intent_id, "observer-b", 112, 30)
        scenarios["sqlite_ambiguous_remote_stops"] = (
            observed.code is store_module.StoreResultCode.APPLIED
            and record is not None
            and record.state is store_module.OutboxState.BLOCKED
            and replay_claim.code is store_module.StoreResultCode.CONFLICT
            and counter.worker_dispatch == 0
        )
    facts["ambiguous_remote_no_dispatch"] = scenarios["sqlite_ambiguous_remote_stops"]
    add_rows(store)

    # Compensation event and release intent replay without a duplicate outbox row.
    _, store = make_store("compensation")
    prepared = store.prepare(binding("compensation"), 100)
    snapshot = prepared.snapshot
    if snapshot is not None and snapshot.pending_intent is not None:
        grant = contract.SagaEvent(
            kind=contract.EventKind.RESERVATION_GRANTED,
            event_id=stable("compensation:grant"),
            binding_hash=snapshot.binding.binding_hash,
            intent_id=snapshot.pending_intent.intent_id,
            observed_at_epoch=110,
            reservation_id=stable("compensation:reservation"),
            reservation_fencing_token=7,
            reservation_expires_at_epoch=200,
        )
        granted = store.apply(snapshot.binding.operation_id, grant).snapshot
        if granted is not None and granted.pending_intent is not None:
            failed_event = contract.SagaEvent(
                kind=contract.EventKind.LOCAL_PREPARE_FAILED,
                event_id=stable("compensation:failure"),
                binding_hash=granted.binding.binding_hash,
                intent_id=granted.pending_intent.intent_id,
                observed_at_epoch=120,
                reason_code="synthetic-prepare-failure",
            )
            failed = store.apply(granted.binding.operation_id, failed_event)
            before = store.table_counts()
            replay = store.apply(granted.binding.operation_id, failed_event)
            after = store.table_counts()
            persisted_release = (
                store.load_outbox(failed.snapshot.pending_intent.intent_id)
                if failed.snapshot is not None and failed.snapshot.pending_intent is not None
                else None
            )
            scenarios["sqlite_compensation_replay"] = (
                failed.code is store_module.StoreResultCode.APPLIED
                and replay.code is store_module.StoreResultCode.DUPLICATE
                and before == after
                and failed.snapshot is not None
                and failed.snapshot.pending_intent is not None
                and persisted_release is not None
                and failed.snapshot.pending_intent.intent_id == persisted_release.intent.intent_id
            )
    facts["compensation_idempotent"] = scenarios["sqlite_compensation_replay"]
    add_rows(store)

    # Persisted corruption and disabled mode both fail closed.
    _, store = make_store("corruption")
    prepared = store.prepare(binding("corruption"), 100)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE phase3j_sagas SET snapshot_hash=? WHERE operation_id=?",
            ("0" * 64, prepared.snapshot.binding.operation_id),
        )
    corrupt_rejected = False
    try:
        store.load_snapshot(prepared.snapshot.binding.operation_id)
    except store_module.SagaStoreCorruptionError:
        corrupt_rejected = True
    disabled = runtime_module.SagaRuntime(config_module.SagaRuntimeConfig()).initialize()
    scenarios["sqlite_corruption_unavailable"] = (
        corrupt_rejected and disabled.code is store_module.StoreResultCode.UNAVAILABLE
    )
    facts["corruption_unavailable_fail_closed"] = scenarios["sqlite_corruption_unavailable"]
    add_rows(store)

    facts["zero_operational_effects"] = counter.total == 0
    return scenarios, facts, writes


def _run_postgres_contract(container: str, migration: Path) -> tuple[dict[str, bool], dict[str, bool], int]:
    """Apply and exercise the reservation contract in the network-isolated container."""

    for path in (
        PHASE3G_BOOTSTRAP,
        PHASE3G_MIGRATION,
        migration,
        PHASE3J_BOOTSTRAP,
        PHASE3J_RUNTIME_CONTRACT,
    ):
        if not path.is_file():
            raise GateFailure("postgres-required-input-unavailable")
    _require_success(_psql(container, PHASE3G_BOOTSTRAP.read_text(encoding="utf-8")), "phase3g-bootstrap-failed")
    _require_success(_psql(container, PHASE3G_MIGRATION.read_text(encoding="utf-8")), "phase3g-migration-failed")
    migration_sql = migration.read_text(encoding="utf-8")
    _require_success(_psql(container, migration_sql), "phase3j-migration-failed")
    _require_success(_psql(container, migration_sql), "phase3j-migration-replay-failed")
    _require_success(
        _psql(container, PHASE3J_BOOTSTRAP.read_text(encoding="utf-8"), timeout=120),
        "phase3j-bootstrap-failed",
    )
    output = _require_success(
        _psql(container, PHASE3J_RUNTIME_CONTRACT.read_text(encoding="utf-8"), timeout=120),
        "phase3j-runtime-contract-failed",
    )
    markers = {
        line.strip()[len("phase3j_check:"):]
        for line in output.splitlines()
        if line.strip().startswith("phase3j_check:")
    }
    scenario_keys = {
        "postgres_approved_review_only_denied",
        "postgres_reservation_replay",
        "postgres_consume_cas",
        "postgres_release_replay",
        "postgres_expiry_fencing",
    }
    catalog_keys = {
        "authorization_table_present",
        "reservation_table_present",
        "event_table_present",
        "rls_enabled",
        "no_browser_policies",
        "server_read_only_tables",
        "reservation_rpc_signatures",
        "rpc_security_definer",
        "rpc_search_path_fixed",
        "append_only_events",
        "authorization_bootstrap_only",
    }
    scenarios = {key: key in markers for key in scenario_keys}
    facts = {
        "review_approval_not_execution_authority": scenarios["postgres_approved_review_only_denied"],
        "service_role_only_reservation": catalog_keys.issubset(markers),
    }
    if not all(scenarios.values()) or not all(facts.values()):
        raise GateFailure("postgres-runtime-markers-incomplete")
    write_count_text = _require_success(
        _psql(
            container,
            """
SELECT
  (SELECT count(*) FROM public.scrape_execution_authorizations) +
  (SELECT count(*) FROM public.scrape_execution_reservations) +
  (SELECT count(*) FROM public.scrape_execution_reservation_events);
""",
        ),
        "postgres-effect-count-failed",
    )
    try:
        write_count = int(write_count_text)
    except ValueError as exc:
        raise GateFailure("postgres-effect-count-invalid") from exc
    if write_count <= 0:
        raise GateFailure("postgres-effect-count-invalid")
    return scenarios, facts, write_count


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n"
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_gate(
    *,
    expected_commit: str | None,
    contract_path: Path = CONTRACT,
    migration_path: Path = MIGRATION,
    runtime_dir: Path = RUNTIME_DIR,
    report_path: Path = REPORT,
    sqlite_runner: Callable[[Path, Path, OperationalEffectCounter], tuple[dict[str, bool], dict[str, bool], int]] = _run_sqlite_contract,
    postgres_runner: Callable[[str, Path], tuple[dict[str, bool], dict[str, bool], int]] = _run_postgres_contract,
    docker_enabled: bool = True,
) -> int:
    scenarios: dict[str, bool] = {}
    invariants: dict[str, bool] = {}
    effects = OperationalEffectCounter()
    disposable_effects = {"sqlite_writes": 0, "postgres_writes": 0}
    cleanup = {"attempted": False, "container_absent": False, "workspace_absent": False}
    failure_code: str | None = None
    commit = ""
    contract_hash = ""
    migration_hash = ""
    schema_hash = ""
    runtime_hashes: dict[str, str] = {}
    workspace_path: Path | None = None
    container = f"keiba-phase3j-{secrets.token_hex(8)}"
    docker_environment = ExitStack()
    docker_environment_active = False

    try:
        commit = _tested_commit(expected_commit)
        contract, verifier = _load_contract(contract_path)
        contract_hash = _canonical_json_sha256(contract)
        migration_hash = _file_sha256(migration_path)
        store_path = runtime_dir / "cross_store_saga_store.py"
        schema_hash = _schema_sha256(store_path)
        runtime_hashes = {
            relative: _file_sha256(runtime_dir / Path(relative).name)
            for relative in verifier.RUNTIME_ASSETS
        }
        workspace_path = Path(tempfile.mkdtemp(prefix="phase3j-saga-"))
        cleanup["attempted"] = True
        docker_config = workspace_path / "docker-client-config"
        docker_home = workspace_path / "docker-client-home"
        docker_config.mkdir(mode=0o700)
        docker_home.mkdir(mode=0o700)
        (docker_config / "config.json").write_text("{}\n", encoding="utf-8")
        docker_environment.enter_context(
            patch.dict(
                os.environ,
                {"DOCKER_CONFIG": str(docker_config), "HOME": str(docker_home)},
                clear=False,
            )
        )
        docker_environment_active = True
        with _operational_effect_guard(effects, workspace_path):
            sqlite_scenarios, sqlite_invariants, sqlite_writes = sqlite_runner(workspace_path, runtime_dir, effects)
        scenarios.update(sqlite_scenarios)
        invariants.update(sqlite_invariants)
        disposable_effects["sqlite_writes"] = sqlite_writes

        if not docker_enabled:
            postgres_scenarios, postgres_invariants, postgres_writes = postgres_runner("test-container", migration_path)
            cleanup["container_absent"] = True
        else:
            endpoint = _require_success(
                _docker("context", "inspect", "--format", "{{(index .Endpoints \"docker\").Host}}", timeout=20),
                "docker-context-unavailable",
            )
            if not endpoint.startswith(LOCAL_DOCKER_ENDPOINTS):
                raise GateFailure("remote-docker-context-rejected")
            _require_success(_docker("version", "--format", "{{.Server.Version}}", timeout=20), "docker-daemon-unavailable")
            _require_success(_docker("pull", IMAGE, timeout=180), "docker-image-unavailable")
            password = secrets.token_urlsafe(24)
            started = _require_success(
                _docker(
                    "run", "--detach", "--name", container, "--network", "none",
                    "--label", "keiba-ai-pro.phase3j-runtime=true",
                    "--env", f"POSTGRES_DB={DATABASE}", "--env", "POSTGRES_USER=postgres",
                    "--env", f"POSTGRES_PASSWORD={password}", "--pull", "never", IMAGE,
                    timeout=30,
                ),
                "container-start-failed",
            )
            if not CONTAINER_ID_PATTERN.fullmatch(started):
                raise GateFailure("container-id-invalid")
            _wait_for_postgres(container)
            postgres_scenarios, postgres_invariants, postgres_writes = postgres_runner(container, migration_path)
        scenarios.update(postgres_scenarios)
        invariants.update(postgres_invariants)
        disposable_effects["postgres_writes"] = postgres_writes

        if frozenset(scenarios) != frozenset(verifier.SCENARIO_KEYS) or not all(scenarios.values()):
            raise GateFailure("scenario-checks-incomplete")
        if frozenset(invariants) != frozenset(verifier.INVARIANT_KEYS) or not all(invariants.values()):
            raise GateFailure("invariant-checks-incomplete")
        if effects.total != 0 or effects.worker_dispatch != 0:
            raise GateFailure("operational-effect-observed")
        if any(type(value) is not int or value <= 0 for value in disposable_effects.values()):
            raise GateFailure("disposable-database-effects-invalid")
    except GateFailure as exc:
        failure_code = exc.code
    except Exception:
        failure_code = "unexpected-gate-failure"
    finally:
        # The random name is gate-owned even when `docker run` times out after
        # daemon-side creation but before the client returns a container id.
        if docker_enabled and docker_environment_active:
            try:
                _docker("rm", "--force", container, timeout=30)
            except GateFailure:
                pass
        if docker_environment_active and not cleanup["container_absent"]:
            try:
                cleanup["container_absent"] = _container_absent(container)
            except GateFailure:
                cleanup["container_absent"] = False
        docker_environment.close()
        if workspace_path is not None:
            # sqlite3 connection objects may be finalized after their context
            # manager exits; collect before asserting Windows cleanup.
            gc.collect()
            shutil.rmtree(workspace_path, ignore_errors=True)
            cleanup["workspace_absent"] = not workspace_path.exists()
        else:
            cleanup["workspace_absent"] = True
        if failure_code is None and not all(cleanup.values()):
            failure_code = "cleanup-failed"

    success = (
        failure_code is None
        and all(cleanup.values())
        and effects.total == 0
        and scenarios
        and invariants
        and all(scenarios.values())
        and all(invariants.values())
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "evidence_mode": "disposable-runtime",
        "environment": "ci-disposable",
        "database_scope": "temporary-sqlite-and-disposable-postgres",
        "network_mode": "container-none",
        "image": IMAGE,
        "host_port_published": False,
        "external_credentials_used": False,
        "tested_commit_sha": commit,
        "contract_sha256": contract_hash,
        "migration_sha256": migration_hash,
        "schema_sha256": schema_hash,
        "runtime_asset_sha256": runtime_hashes,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "success": bool(success),
        "production_ready": False,
        "l3_eligible": False,
        "external_migration_applied": False,
        "scenario_count": len(scenarios),
        "scenario_checks": scenarios,
        "invariant_checks": invariants,
        "operational_effect_count": effects.total,
        "worker_dispatch_count": effects.worker_dispatch,
        "operational_effects": effects.as_dict(),
        "disposable_database_effect_count": sum(disposable_effects.values()),
        "disposable_database_effects": disposable_effects,
        "cleanup": cleanup,
    }
    if failure_code is not None:
        # Failure code is intentionally not copied into the strict runtime evidence schema.
        report["success"] = False
    try:
        _write_report(report_path, report)
    except Exception:
        return 1
    return 0 if success else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 3J disposable saga/outbox runtime contract.")
    parser.add_argument("--contract", type=Path, default=CONTRACT)
    parser.add_argument("--migration", type=Path, default=MIGRATION)
    parser.add_argument("--runtime-dir", type=Path, default=RUNTIME_DIR)
    parser.add_argument("--expected-commit", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_gate(
        expected_commit=args.expected_commit,
        contract_path=args.contract,
        migration_path=args.migration,
        runtime_dir=args.runtime_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())

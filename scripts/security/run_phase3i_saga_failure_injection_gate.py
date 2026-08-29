from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
STATE_MACHINE = ROOT / "python-api" / "scraping" / "cross_store_saga_contract.py"
VERIFIER = ROOT / "scripts" / "verify_phase3i_saga_failure_injection.py"
CONTRACT = ROOT / "python-api" / "tests" / "fixtures" / "phase3i_saga_failure_matrix_v1.json"
REPORT = ROOT / "reports" / "phase3i_saga_failure_injection_runtime.json"
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
NAMESPACE = uuid.UUID("bc97a799-46d4-431e-ac06-177d9bb7c99e")


class GateFailure(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SyntheticLedger:
    """Temporary test ledger. It never opens a production database."""

    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(str(path))
        self._connection.executescript(
            """
            CREATE TABLE scenario_results (name TEXT PRIMARY KEY, passed INTEGER NOT NULL CHECK (passed IN (0,1)));
            CREATE TABLE forbidden_effects (effect_name TEXT NOT NULL);
            """
        )
        self._connection.commit()

    def record_scenario(self, name: str, passed: bool) -> None:
        self._connection.execute(
            "INSERT INTO scenario_results(name, passed) VALUES (?, ?)",
            (name, 1 if passed else 0),
        )
        self._connection.commit()

    def record_forbidden_effect(self, effect_name: str) -> None:
        self._connection.execute("INSERT INTO forbidden_effects(effect_name) VALUES (?)", (effect_name,))
        self._connection.commit()

    def effect_count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM forbidden_effects").fetchone()
        return int(row[0]) if row else -1

    def close(self) -> None:
        self._connection.close()


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


def _require_state_machine_api(module: ModuleType) -> None:
    required = (
        "SagaBinding",
        "SagaEvent",
        "SagaState",
        "EventKind",
        "EffectAction",
        "create_saga",
        "apply_event",
        "recover",
        "validate_snapshot",
        "TERMINAL_STATES",
    )
    if any(not hasattr(module, name) for name in required):
        raise GateFailure("state-machine-api-unavailable")


@contextmanager
def _forbidden_effect_guard(ledger: SyntheticLedger):
    """Record and reject any effectful primitive reached by the pure model."""

    builtins_module = importlib.import_module("builtins")
    io_module = importlib.import_module("io")
    socket_module = importlib.import_module("socket")
    subprocess_module = importlib.import_module("subprocess")
    threading_module = importlib.import_module("threading")
    thread_module = importlib.import_module("_thread")
    urllib_request = importlib.import_module("urllib.request")
    pathlib_module = importlib.import_module("pathlib")

    def reject(effect_name: str):
        def blocked(*_args: Any, **_kwargs: Any):
            ledger.record_forbidden_effect(effect_name)
            raise GateFailure("forbidden-effect-attempted")

        return blocked

    patches = (
        (builtins_module, "open", "file_open"),
        (io_module, "open", "file_open"),
        (os, "open", "file_open"),
        (os, "fdopen", "file_open"),
        (os, "write", "file_write"),
        (os, "replace", "file_write"),
        (os, "rename", "file_write"),
        (os, "remove", "file_write"),
        (os, "unlink", "file_write"),
        (os, "mkdir", "file_write"),
        (os, "makedirs", "file_write"),
        (os, "rmdir", "file_write"),
        (sqlite3, "connect", "sqlite_connect"),
        (socket_module, "socket", "network_call"),
        (socket_module, "create_connection", "network_call"),
        (subprocess_module, "Popen", "subprocess_start"),
        (subprocess_module, "run", "subprocess_start"),
        (subprocess_module, "call", "subprocess_start"),
        (subprocess_module, "check_call", "subprocess_start"),
        (subprocess_module, "check_output", "subprocess_start"),
        (threading_module.Thread, "start", "thread_start"),
        (thread_module, "start_new_thread", "thread_start"),
        (urllib_request, "urlopen", "network_call"),
        (urllib_request, "urlretrieve", "network_call"),
        (urllib_request.OpenerDirector, "open", "network_call"),
        (pathlib_module.Path, "open", "file_open"),
        (pathlib_module.Path, "write_text", "file_write"),
        (pathlib_module.Path, "write_bytes", "file_write"),
        (pathlib_module.Path, "touch", "file_write"),
        (pathlib_module.Path, "mkdir", "file_write"),
        (pathlib_module.Path, "unlink", "file_write"),
        (pathlib_module.Path, "rename", "file_write"),
        (pathlib_module.Path, "replace", "file_write"),
        (pathlib_module.Path, "rmdir", "file_write"),
    )
    with ExitStack() as stack:
        for target, attribute, effect_name in patches:
            stack.enter_context(patch.object(target, attribute, reject(effect_name)))
        yield


class _BindingObserver:
    def __init__(self) -> None:
        self.identities: dict[str, tuple[Any, ...]] = {}
        self.observation_count = 0
        self.valid = True

    def observe(self, result: Any) -> Any:
        try:
            binding = result.snapshot.binding
            identity = (
                binding.operation_id,
                binding.review_id,
                binding.review_version,
                binding.owner_user_id,
                binding.job_id,
                binding.request_hash,
                binding.review_binding_hash,
                binding.execution_binding_hash,
                binding.binding_hash,
            )
            previous = self.identities.setdefault(binding.operation_id, identity)
            if previous != identity:
                self.valid = False
            self.observation_count += 1
        except Exception:
            self.valid = False
        return result


@contextmanager
def _observe_all_transition_bindings(module: ModuleType):
    observer = _BindingObserver()
    originals = {name: getattr(module, name) for name in ("create_saga", "apply_event", "recover")}

    def wrap(original):
        def observed(*args: Any, **kwargs: Any):
            return observer.observe(original(*args, **kwargs))

        return observed

    try:
        for name, original in originals.items():
            setattr(module, name, wrap(original))
        yield observer
    finally:
        for name, original in originals.items():
            setattr(module, name, original)


def _stable_uuid(label: str) -> str:
    return str(uuid.uuid5(NAMESPACE, label))


def _binding(module: ModuleType, label: str):
    return module.SagaBinding(
        operation_id=_stable_uuid(f"{label}:operation"),
        review_id=_stable_uuid(f"{label}:review"),
        review_version=1,
        owner_user_id=_stable_uuid(f"{label}:owner"),
        job_id=_stable_uuid(f"{label}:job"),
        request_hash=hashlib.sha256(f"{label}:request".encode("utf-8")).hexdigest(),
    )


def _event(module: ModuleType, snapshot: Any, kind: Any, label: str, **values: Any):
    return module.SagaEvent(
        kind=kind,
        event_id=_stable_uuid(f"{label}:event"),
        binding_hash=snapshot.binding.binding_hash,
        **values,
    )


def _accepted(module: ModuleType, result: Any, code: str):
    if result.accepted is not True or result.failure_code is not None:
        raise GateFailure(code)
    if tuple(module.validate_snapshot(result.snapshot)):
        raise GateFailure(f"{code}-invalid-snapshot")
    return result.snapshot


def _new(module: ModuleType, label: str):
    binding = _binding(module, label)
    result = module.create_saga(binding)
    snapshot = _accepted(module, result, f"{label}-create-failed")
    if snapshot.binding.binding_hash != binding.binding_hash or len(result.emitted_intents) != 1:
        raise GateFailure(f"{label}-binding-failed")
    return snapshot


def _grant(module: ModuleType, snapshot: Any, label: str):
    result = module.apply_event(
        snapshot,
        _event(
            module,
            snapshot,
            module.EventKind.RESERVATION_GRANTED,
            f"{label}:grant",
            intent_id=snapshot.pending_intent.intent_id,
            observed_at_epoch=100,
            reservation_id=_stable_uuid(f"{label}:reservation"),
            reservation_fencing_token=7,
            reservation_expires_at_epoch=1_000,
        ),
    )
    return _accepted(module, result, f"{label}-grant-failed")


def _prepare(module: ModuleType, snapshot: Any, label: str):
    event = _event(
        module,
        snapshot,
        module.EventKind.LOCAL_PREPARE_SUCCEEDED,
        f"{label}:prepare",
        intent_id=snapshot.pending_intent.intent_id,
        observed_at_epoch=150,
    )
    result = module.apply_event(snapshot, event)
    return _accepted(module, result, f"{label}-prepare-failed"), event, result


def _consume(module: ModuleType, snapshot: Any, label: str):
    result = module.apply_event(
        snapshot,
        _event(
            module,
            snapshot,
            module.EventKind.RESERVATION_CONSUMED,
            f"{label}:consume",
            intent_id=snapshot.pending_intent.intent_id,
            observed_at_epoch=200,
            consume_receipt_hash=hashlib.sha256(f"{label}:receipt".encode("utf-8")).hexdigest(),
        ),
    )
    return _accepted(module, result, f"{label}-consume-failed")


def _release(module: ModuleType, snapshot: Any, label: str):
    event = _event(
        module,
        snapshot,
        module.EventKind.RELEASE_CONFIRMED,
        f"{label}:release",
        intent_id=snapshot.pending_intent.intent_id,
        observed_at_epoch=1_100,
    )
    result = module.apply_event(snapshot, event)
    return _accepted(module, result, f"{label}-release-failed"), event, result


def _safety(module: ModuleType, snapshot: Any, label: str):
    result = module.apply_event(
        snapshot,
        _event(
            module,
            snapshot,
            module.EventKind.SAFETY_VIOLATION,
            f"{label}:safety",
            observed_at_epoch=250,
            reason_code="synthetic-safety-stop",
        ),
    )
    return _accepted(module, result, f"{label}-safety-stop-failed")


def _run_scenarios(module: ModuleType, ledger: SyntheticLedger) -> tuple[dict[str, bool], dict[str, bool]]:
    verifier = _load_module(VERIFIER, "phase3i_verifier")
    scenarios = {key: False for key in verifier.SCENARIO_KEYS}
    facts = {key: False for key in verifier.INVARIANT_KEYS}

    snapshot = _new(module, "failure-before-prepare")
    stopped = _safety(module, snapshot, "failure-before-prepare")
    scenarios["failure_before_prepare"] = stopped.state is module.SagaState.MANUAL_INTERVENTION

    snapshot = _grant(module, _new(module, "failure-after-prepare"), "failure-after-prepare")
    failed = module.apply_event(
        snapshot,
        _event(
            module,
            snapshot,
            module.EventKind.LOCAL_PREPARE_FAILED,
            "failure-after-prepare:failed",
            intent_id=snapshot.pending_intent.intent_id,
            observed_at_epoch=150,
            reason_code="synthetic-prepare-failure",
        ),
    )
    snapshot = _accepted(module, failed, "failure-after-prepare-event-failed")
    snapshot, _, _ = _release(module, snapshot, "failure-after-prepare")
    scenarios["failure_after_prepare"] = snapshot.state is module.SagaState.COMPENSATED

    snapshot = _new(module, "reservation-rejected")
    rejected = module.apply_event(
        snapshot,
        _event(
            module,
            snapshot,
            module.EventKind.RESERVATION_REJECTED,
            "reservation-rejected",
            intent_id=snapshot.pending_intent.intent_id,
            observed_at_epoch=100,
            reason_code="synthetic-reservation-rejected",
        ),
    )
    snapshot = _accepted(module, rejected, "reservation-rejected-event-failed")
    scenarios["reservation_rejected"] = snapshot.state is module.SagaState.FAILED_TERMINAL

    snapshot = _grant(module, _new(module, "reservation-expired"), "reservation-expired")
    recovered = module.recover(snapshot, 1_000)
    snapshot = _accepted(module, recovered, "reservation-expiry-recovery-failed")
    snapshot, _, _ = _release(module, snapshot, "reservation-expired")
    scenarios["reservation_expired"] = snapshot.state is module.SagaState.COMPENSATED

    snapshot = _grant(module, _new(module, "consume-rejected"), "consume-rejected")
    snapshot, prepare_event, _ = _prepare(module, snapshot, "consume-rejected")
    duplicate_prepare = module.apply_event(snapshot, prepare_event)
    facts["idempotent_prepare"] = duplicate_prepare.accepted is True and duplicate_prepare.duplicate is True
    rejected = module.apply_event(
        snapshot,
        _event(
            module,
            snapshot,
            module.EventKind.CONSUME_REJECTED,
            "consume-rejected",
            intent_id=snapshot.pending_intent.intent_id,
            observed_at_epoch=200,
            reason_code="synthetic-consume-rejected",
        ),
    )
    snapshot = _accepted(module, rejected, "consume-rejection-event-failed")
    snapshot, _, _ = _release(module, snapshot, "consume-rejected")
    scenarios["consume_rejected"] = snapshot.state is module.SagaState.COMPENSATED

    snapshot = _grant(module, _new(module, "consume-ambiguous"), "consume-ambiguous")
    snapshot, _, _ = _prepare(module, snapshot, "consume-ambiguous")
    consume_intent_id = snapshot.pending_intent.intent_id
    first_observation = module.recover(snapshot, 200)
    second_observation = module.recover(snapshot, 200)
    no_dispatch_intents = all(
        intent.action is not module.EffectAction.DISPATCH_WORKER
        for result in (first_observation, second_observation)
        for intent in result.emitted_intents
    )
    still_waiting = (
        first_observation.snapshot == second_observation.snapshot == snapshot
        and len(first_observation.emitted_intents) == 1
        and len(second_observation.emitted_intents) == 1
        and first_observation.emitted_intents[0].intent_id == consume_intent_id
        and second_observation.emitted_intents[0].intent_id == consume_intent_id
        and no_dispatch_intents
    )
    snapshot = _safety(module, snapshot, "consume-ambiguous")
    scenarios["consume_ambiguous"] = still_waiting and snapshot.state is module.SagaState.MANUAL_INTERVENTION

    snapshot = _grant(module, _new(module, "after-consume"), "after-consume")
    snapshot, _, _ = _prepare(module, snapshot, "after-consume")
    snapshot = _consume(module, snapshot, "after-consume")
    first = module.recover(snapshot, 300)
    second = module.recover(snapshot, 300)
    scenarios["failure_after_consume_before_outbox_ack"] = (
        first.accepted is True
        and second.accepted is True
        and first.snapshot == snapshot
        and second.snapshot == snapshot
        and first.emitted_intents == second.emitted_intents
        and len(first.emitted_intents) == 1
    )
    facts["deterministic_recovery"] = scenarios["failure_after_consume_before_outbox_ack"]
    facts["consume_before_dispatch"] = snapshot.state is module.SagaState.DISPATCH_PENDING

    before_claim = module.recover(snapshot, 301)
    scenarios["dispatcher_crash_before_claim_commit"] = (
        before_claim.accepted is True and before_claim.snapshot == snapshot and len(before_claim.emitted_intents) == 1
    )

    lease_event = _event(
        module,
        snapshot,
        module.EventKind.WORKER_LEASE_GRANTED,
        "dispatcher:lease",
        intent_id=snapshot.pending_intent.intent_id,
        observed_at_epoch=300,
        worker_fencing_token=1,
        worker_lease_expires_at_epoch=500,
    )
    leased_result = module.apply_event(snapshot, lease_event)
    leased = _accepted(module, leased_result, "worker-lease-failed")
    expired = module.recover(leased, 500)
    redispatch = _accepted(module, expired, "worker-expiry-recovery-failed")
    scenarios["dispatcher_crash_after_claim"] = (
        redispatch.state is module.SagaState.DISPATCH_PENDING and len(expired.emitted_intents) == 1
    )

    stale = module.apply_event(
        leased,
        _event(
            module,
            leased,
            module.EventKind.WORKER_SUCCEEDED,
            "dispatcher:stale",
            observed_at_epoch=350,
            worker_fencing_token=0,
        ),
    )
    stopped = _safety(module, leased, "dispatcher-stale")
    scenarios["stale_fencing_token"] = (
        stale.accepted is False
        and stale.snapshot == leased
        and stopped.state is module.SagaState.MANUAL_INTERVENTION
    )
    facts["lease_fencing"] = scenarios["stale_fencing_token"]

    duplicate = module.apply_event(leased, lease_event)
    scenarios["duplicate_dispatcher_replay"] = (
        duplicate.accepted is True and duplicate.duplicate is True and duplicate.snapshot == leased
    )

    snapshot = _grant(module, _new(module, "compensation-replay"), "compensation-replay")
    failed = module.apply_event(
        snapshot,
        _event(
            module,
            snapshot,
            module.EventKind.LOCAL_PREPARE_FAILED,
            "compensation-replay:failed",
            intent_id=snapshot.pending_intent.intent_id,
            observed_at_epoch=150,
            reason_code="synthetic-prepare-failure",
        ),
    )
    pending = _accepted(module, failed, "compensation-setup-failed")
    recovery_one = module.recover(pending, 200)
    recovery_two = module.recover(pending, 200)
    compensated, release_event, _ = _release(module, pending, "compensation-replay")
    release_duplicate = module.apply_event(compensated, release_event)
    scenarios["compensation_interrupted_then_replayed"] = (
        recovery_one.emitted_intents == recovery_two.emitted_intents
        and compensated.state is module.SagaState.COMPENSATED
        and release_duplicate.accepted is True
        and release_duplicate.duplicate is True
    )
    facts["idempotent_compensation"] = scenarios["compensation_interrupted_then_replayed"]

    initial = _new(module, "recovery-twice")
    recovery_one = module.recover(initial, 100)
    recovery_two = module.recover(initial, 100)
    scenarios["recovery_replayed_twice"] = (
        recovery_one.snapshot == recovery_two.snapshot == initial
        and recovery_one.emitted_intents == recovery_two.emitted_intents
        and len(recovery_one.emitted_intents) == 1
    )

    left = module.recover(initial, 101)
    right = module.recover(initial, 101)
    scenarios["concurrent_recovery"] = (
        left.snapshot == right.snapshot == initial
        and left.emitted_intents == right.emitted_intents
        and left.failure_code is None
        and right.failure_code is None
    )
    facts["replay_deduplication"] = scenarios["concurrent_recovery"]

    original_binding = _binding(module, "binding-probe")
    changed_review = module.SagaBinding(
        operation_id=original_binding.operation_id,
        review_id=original_binding.review_id,
        review_version=2,
        owner_user_id=original_binding.owner_user_id,
        job_id=original_binding.job_id,
        request_hash=original_binding.request_hash,
    )
    changed_job = module.SagaBinding(
        operation_id=original_binding.operation_id,
        review_id=original_binding.review_id,
        review_version=original_binding.review_version,
        owner_user_id=original_binding.owner_user_id,
        job_id=_stable_uuid("binding-probe:changed-job"),
        request_hash=original_binding.request_hash,
    )
    binding_snapshot = _new(module, "binding-rejection-probe")
    wrong_binding = module.apply_event(
        binding_snapshot,
        module.SagaEvent(
            kind=module.EventKind.RESERVATION_REJECTED,
            event_id=_stable_uuid("binding-rejection-probe:wrong-binding:event"),
            binding_hash="0" * 64,
            intent_id=binding_snapshot.pending_intent.intent_id,
            observed_at_epoch=100,
            reason_code="synthetic-binding-mismatch",
        ),
    )
    facts["stable_operation_job_binding"] = (
        original_binding.review_binding_hash != changed_review.review_binding_hash
        and original_binding.execution_binding_hash == changed_review.execution_binding_hash
        and original_binding.binding_hash != changed_review.binding_hash
        and original_binding.review_binding_hash == changed_job.review_binding_hash
        and original_binding.execution_binding_hash != changed_job.execution_binding_hash
        and original_binding.binding_hash != changed_job.binding_hash
        and wrong_binding.accepted is False
        and wrong_binding.failure_code == "event-binding-mismatch"
        and wrong_binding.snapshot == binding_snapshot
        and wrong_binding.emitted_intents == ()
    )
    for name, passed in scenarios.items():
        ledger.record_scenario(name, passed)
    effect_count = ledger.effect_count()
    facts["worker_dispatch_prohibited"] = effect_count == 0
    facts["zero_external_effects"] = effect_count == 0
    return scenarios, facts


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
    expected_commit: str,
    contract_path: Path = CONTRACT,
    state_machine_path: Path = STATE_MACHINE,
    report_path: Path = REPORT,
    scenario_runner: Callable[[ModuleType, SyntheticLedger], tuple[dict[str, bool], dict[str, bool]]] | None = None,
) -> int:
    verifier: ModuleType | None = None
    contract: Any = None
    contract_hash = ""
    scenario_checks: dict[str, bool] = {}
    invariant_checks: dict[str, bool] = {}
    effect_count = -1
    cleanup = {"attempted": False, "workspace_absent": False}
    failure_code: str | None = None
    workspace = Path(tempfile.mkdtemp(prefix="phase3i-saga-"))
    ledger: SyntheticLedger | None = None
    try:
        if not isinstance(expected_commit, str) or COMMIT_PATTERN.fullmatch(expected_commit) is None:
            raise GateFailure("expected-commit-invalid")
        verifier = _load_module(VERIFIER, "phase3i_verifier")
        contract, failures = verifier.load_contract(contract_path)
        if failures or not verifier._contract_schema_valid(contract):
            raise GateFailure("contract-invalid")
        contract_hash = verifier.expected_contract_sha256(contract) or ""
        ledger = SyntheticLedger(workspace / "synthetic_saga.db")
        runner = scenario_runner or _run_scenarios
        with _forbidden_effect_guard(ledger):
            module = _load_module(state_machine_path, "phase3i_state_machine")
            _require_state_machine_api(module)
            if frozenset(module.TERMINAL_STATES) != frozenset(contract["terminal_states"]):
                raise GateFailure("terminal-state-contract-mismatch")
            with _observe_all_transition_bindings(module) as binding_observer:
                scenario_checks, invariant_checks = runner(module, ledger)
        if scenario_runner is None:
            invariant_checks["stable_operation_job_binding"] = bool(
                invariant_checks.get("stable_operation_job_binding")
                and binding_observer.valid
                and binding_observer.observation_count > 0
            )
        effect_count = ledger.effect_count()
        if frozenset(scenario_checks) != frozenset(verifier.SCENARIO_KEYS) or not all(
            type(value) is bool and value is True for value in scenario_checks.values()
        ):
            raise GateFailure("scenario-matrix-failed")
        if frozenset(invariant_checks) != frozenset(verifier.INVARIANT_KEYS) or not all(
            type(value) is bool and value is True for value in invariant_checks.values()
        ):
            raise GateFailure("invariant-matrix-failed")
        if effect_count != 0:
            raise GateFailure("external-effect-observed")
    except GateFailure as exc:
        failure_code = exc.code
    except Exception:
        failure_code = "unexpected-gate-failure"
    finally:
        cleanup["attempted"] = True
        if ledger is not None:
            try:
                effect_count = ledger.effect_count()
            except Exception:
                effect_count = -1
        if ledger is not None:
            try:
                ledger.close()
            except Exception:
                pass
        try:
            shutil.rmtree(workspace)
        except OSError:
            pass
        cleanup["workspace_absent"] = not workspace.exists()
        if not cleanup["workspace_absent"] and failure_code is None:
            failure_code = "workspace-cleanup-failed"

    if verifier is not None:
        scenario_defaults = {key: False for key in verifier.SCENARIO_KEYS}
        scenario_defaults.update({key: value for key, value in scenario_checks.items() if key in scenario_defaults})
        invariant_defaults = {key: False for key in verifier.INVARIANT_KEYS}
        invariant_defaults.update({key: value for key, value in invariant_checks.items() if key in invariant_defaults})
    else:
        scenario_defaults = {}
        invariant_defaults = {}
    success = (
        failure_code is None
        and effect_count == 0
        and cleanup["workspace_absent"]
        and bool(scenario_defaults)
        and all(scenario_defaults.values())
        and bool(invariant_defaults)
        and all(invariant_defaults.values())
    )
    report = {
        "schema_version": 1,
        "evidence_mode": "synthetic",
        "environment": "ci-disposable",
        "database_scope": "temporary-sqlite-model",
        "network_mode": "none",
        "synthetic": True,
        "non_executable": True,
        "success": success,
        "production_ready": False,
        "l3_eligible": False,
        "tested_commit_sha": expected_commit
        if isinstance(expected_commit, str) and COMMIT_PATTERN.fullmatch(expected_commit)
        else "",
        "contract_sha256": contract_hash,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scenario_count": len(scenario_defaults),
        "effect_count": effect_count,
        "scenario_checks": scenario_defaults,
        "invariant_checks": invariant_defaults,
        "cleanup": cleanup,
    }
    try:
        _write_report(report_path, report)
    except Exception:
        return 1
    if failure_code is not None:
        print(json.dumps({"success": False, "failure_code": failure_code}, sort_keys=True))
    return 0 if success else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the offline Phase 3I synthetic saga failure matrix.")
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_gate(expected_commit=args.expected_commit, contract_path=args.contract)


if __name__ == "__main__":
    raise SystemExit(main())

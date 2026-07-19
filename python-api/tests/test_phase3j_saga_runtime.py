from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python-api"))

from scraping.cross_store_saga_contract import (  # noqa: E402
    EventKind,
    SagaBinding,
    SagaEvent,
    SagaState,
)
from scraping.cross_store_saga_ports import (  # noqa: E402
    DenyWorkerDispatchAdapter,
    PortOutcome,
    PortResult,
)
from scraping.cross_store_saga_runtime import SagaRuntime  # noqa: E402
from scraping.cross_store_saga_store import (  # noqa: E402
    OutboxState,
    SagaStore,
    StoreResultCode,
)
from scraping.saga_runtime_config import SagaRuntimeConfig  # noqa: E402


OPERATION_ID = "11111111-1111-4111-8111-111111111111"
REVIEW_ID = "22222222-2222-4222-8222-222222222222"
OWNER_ID = "33333333-3333-4333-8333-333333333333"
JOB_ID = "44444444-4444-4444-8444-444444444444"
RESERVATION_ID = "55555555-5555-4555-8555-555555555555"
REQUEST_HASH = "a" * 64
RECEIPT_HASH = "b" * 64


def _binding() -> SagaBinding:
    return SagaBinding(
        operation_id=OPERATION_ID,
        review_id=REVIEW_ID,
        review_version=3,
        owner_user_id=OWNER_ID,
        job_id=JOB_ID,
        request_hash=REQUEST_HASH,
    )


def _runtime(tmp_path: Path) -> tuple[SagaRuntime, SagaStore]:
    config = SagaRuntimeConfig.ci_disposable(tmp_path / "phase3j.sqlite")
    store = SagaStore(config)
    runtime = SagaRuntime(config, store)
    assert runtime.initialize().code is StoreResultCode.APPLIED
    return runtime, store


def _advance_to_dispatch(store: SagaStore) -> str:
    snapshot = store.load_snapshot(OPERATION_ID)
    assert snapshot is not None and snapshot.pending_intent is not None
    granted = store.apply(
        OPERATION_ID,
        SagaEvent(
            kind=EventKind.RESERVATION_GRANTED,
            event_id="00000000-0000-4000-8000-000000000001",
            binding_hash=snapshot.binding.binding_hash,
            intent_id=snapshot.pending_intent.intent_id,
            observed_at_epoch=110,
            reservation_id=RESERVATION_ID,
            reservation_fencing_token=7,
            reservation_expires_at_epoch=200,
        ),
    ).snapshot
    assert granted is not None and granted.pending_intent is not None
    prepared = store.apply(
        OPERATION_ID,
        SagaEvent(
            kind=EventKind.LOCAL_PREPARE_SUCCEEDED,
            event_id="00000000-0000-4000-8000-000000000002",
            binding_hash=granted.binding.binding_hash,
            intent_id=granted.pending_intent.intent_id,
            observed_at_epoch=120,
        ),
    ).snapshot
    assert prepared is not None and prepared.pending_intent is not None
    dispatch = store.apply(
        OPERATION_ID,
        SagaEvent(
            kind=EventKind.RESERVATION_CONSUMED,
            event_id="00000000-0000-4000-8000-000000000003",
            binding_hash=prepared.binding.binding_hash,
            intent_id=prepared.pending_intent.intent_id,
            observed_at_epoch=130,
            consume_receipt_hash=RECEIPT_HASH,
        ),
    ).snapshot
    assert dispatch is not None
    assert dispatch.state is SagaState.DISPATCH_PENDING
    assert dispatch.pending_intent is not None
    return dispatch.pending_intent.intent_id


def test_disabled_runtime_returns_unavailable_without_creating_storage(
    tmp_path: Path,
) -> None:
    runtime = SagaRuntime(SagaRuntimeConfig())
    result = runtime.initialize()
    assert result.code is StoreResultCode.UNAVAILABLE
    assert result.reason_code == "phase3j-runtime-disabled"
    assert runtime.prepare(_binding(), 100).code is StoreResultCode.UNAVAILABLE
    assert list(tmp_path.iterdir()) == []


def test_runtime_prepares_only_the_disposable_store(tmp_path: Path) -> None:
    runtime, store = _runtime(tmp_path)
    prepared = runtime.prepare(_binding(), 100)
    assert prepared.code is StoreResultCode.APPLIED
    assert prepared.snapshot.state is SagaState.RESERVE_PENDING
    assert store.table_counts()["phase3j_jobs"] == 1


def test_default_non_worker_adapter_is_unavailable_and_safe_to_release(
    tmp_path: Path,
) -> None:
    runtime, store = _runtime(tmp_path)
    prepared = runtime.prepare(_binding(), 100)
    intent_id = prepared.snapshot.pending_intent.intent_id  # type: ignore[union-attr]
    result = runtime.process_pending(intent_id, "runtime-a", 110)
    assert result.code is StoreResultCode.APPLIED
    assert result.port_outcome is PortOutcome.UNAVAILABLE
    assert store.load_outbox(intent_id).state is OutboxState.PENDING  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("outcome", "expected_state"),
    [
        (PortOutcome.CONFIRMED, OutboxState.ACKNOWLEDGED),
        (PortOutcome.REJECTED, OutboxState.BLOCKED),
        (PortOutcome.AMBIGUOUS, OutboxState.BLOCKED),
        (PortOutcome.CONFLICT, OutboxState.BLOCKED),
        (PortOutcome.UNAVAILABLE, OutboxState.PENDING),
    ],
)
def test_runtime_persists_all_explicit_port_outcomes_fail_closed(
    tmp_path: Path,
    outcome: PortOutcome,
    expected_state: OutboxState,
) -> None:
    runtime, store = _runtime(tmp_path)
    prepared = runtime.prepare(_binding(), 100)
    intent_id = prepared.snapshot.pending_intent.intent_id  # type: ignore[union-attr]
    claimed = store.claim(intent_id, "runtime-a", 110, 30).claim
    assert claimed is not None
    result = runtime.settle_observation(
        claimed,
        PortResult(
            outcome,
            f"observed-{outcome.value}",
            claimed.intent.intent_id,
            claimed.intent.binding_hash,
            claimed.fencing_token,
            receipt_hash="c" * 64 if outcome is PortOutcome.CONFIRMED else None,
        ),
        111,
    )
    assert result.code is StoreResultCode.APPLIED
    assert result.port_outcome is outcome
    assert store.load_outbox(intent_id).state is expected_state  # type: ignore[union-attr]


def test_recovery_hard_denies_worker_dispatch_and_does_not_enter_running(
    tmp_path: Path,
) -> None:
    runtime, store = _runtime(tmp_path)
    assert runtime.prepare(_binding(), 100).code is StoreResultCode.APPLIED
    dispatch_intent_id = _advance_to_dispatch(store)

    recovered = runtime.recover(OPERATION_ID, 140, owner="recovery-a")
    assert recovered.code is StoreResultCode.APPLIED
    assert recovered.port_outcome is PortOutcome.REJECTED
    assert recovered.snapshot.state is SagaState.DISPATCH_PENDING
    assert store.load_snapshot(OPERATION_ID).state is SagaState.DISPATCH_PENDING  # type: ignore[union-attr]
    outbox = store.load_outbox(dispatch_intent_id)
    assert outbox is not None
    assert outbox.state is OutboxState.BLOCKED
    assert outbox.settlement_reason == "worker-dispatch-disabled"


def test_deny_worker_adapter_rejects_only_dispatch_intents(tmp_path: Path) -> None:
    runtime, store = _runtime(tmp_path)
    prepared = runtime.prepare(_binding(), 100)
    initial_intent = prepared.snapshot.pending_intent
    assert initial_intent is not None
    adapter = DenyWorkerDispatchAdapter()
    result = adapter.execute(initial_intent, fencing_token=1)
    assert result.outcome is PortOutcome.CONFLICT
    assert result.reason_code == "worker-dispatch-intent-invalid"

    dispatch_id = _advance_to_dispatch(store)
    dispatch = store.load_outbox(dispatch_id)
    assert dispatch is not None
    denied = adapter.execute(dispatch.intent, fencing_token=1)
    assert denied.outcome is PortOutcome.REJECTED
    assert denied.reason_code == "worker-dispatch-disabled"


def test_runtime_recovery_reclaims_expired_outbox_lease(tmp_path: Path) -> None:
    runtime, store = _runtime(tmp_path)
    prepared = runtime.prepare(_binding(), 100)
    intent_id = prepared.snapshot.pending_intent.intent_id  # type: ignore[union-attr]
    claim = store.claim(intent_id, "crashed-owner", 105, 5)
    assert claim.code is StoreResultCode.APPLIED
    recovered = runtime.recover(OPERATION_ID, 110)
    assert recovered.recovered_claims == 1
    assert recovered.port_outcome is None
    outbox = store.load_outbox(intent_id)
    assert outbox is not None and outbox.state is OutboxState.PENDING


def test_runtime_recover_unknown_operation_does_not_recover_other_claims(
    tmp_path: Path,
) -> None:
    runtime, store = _runtime(tmp_path)
    prepared = runtime.prepare(_binding(), 100)
    intent_id = prepared.snapshot.pending_intent.intent_id  # type: ignore[union-attr]
    claimed = store.claim(intent_id, "crashed-owner", 105, 5).claim
    assert claimed is not None
    before = store.load_outbox(intent_id)

    result = runtime.recover(
        "99999999-9999-4999-8999-999999999999", 110
    )
    assert result.code is StoreResultCode.NOT_FOUND
    assert result.recovered_claims == 0
    assert store.load_outbox(intent_id) == before


def test_port_result_requires_claim_correlation_and_confirmed_receipt(
    tmp_path: Path,
) -> None:
    runtime, store = _runtime(tmp_path)
    prepared = runtime.prepare(_binding(), 100)
    intent_id = prepared.snapshot.pending_intent.intent_id  # type: ignore[union-attr]
    claim = store.claim(intent_id, "runtime-a", 110, 30).claim
    assert claim is not None

    with pytest.raises(ValueError, match="confirmed-receipt-required"):
        PortResult(
            PortOutcome.CONFIRMED,
            "confirmed",
            claim.intent.intent_id,
            claim.intent.binding_hash,
            claim.fencing_token,
        )

    mismatches = (
        PortResult(
            PortOutcome.REJECTED,
            "correlation-mismatch",
            "f" * 64,
            claim.intent.binding_hash,
            claim.fencing_token,
        ),
        PortResult(
            PortOutcome.REJECTED,
            "correlation-mismatch",
            claim.intent.intent_id,
            "e" * 64,
            claim.fencing_token,
        ),
        PortResult(
            PortOutcome.REJECTED,
            "correlation-mismatch",
            claim.intent.intent_id,
            claim.intent.binding_hash,
            claim.fencing_token + 1,
        ),
    )
    for result in mismatches:
        settled = runtime.settle_observation(claim, result, 111)
        assert settled.code is StoreResultCode.CONFLICT
        assert settled.reason_code == "port-result-correlation-conflict"
        assert store.load_outbox(intent_id).state is OutboxState.CLAIMED  # type: ignore[union-attr]


def test_cross_saga_port_result_replay_is_rejected(tmp_path: Path) -> None:
    runtime, store = _runtime(tmp_path)
    first = runtime.prepare(_binding(), 100)
    second_binding = SagaBinding(
        operation_id="66666666-6666-4666-8666-666666666666",
        review_id="77777777-7777-4777-8777-777777777777",
        review_version=3,
        owner_user_id=OWNER_ID,
        job_id="88888888-8888-4888-8888-888888888888",
        request_hash=REQUEST_HASH,
    )
    second = runtime.prepare(second_binding, 100)
    first_id = first.snapshot.pending_intent.intent_id  # type: ignore[union-attr]
    second_id = second.snapshot.pending_intent.intent_id  # type: ignore[union-attr]
    first_claim = store.claim(first_id, "runtime-a", 110, 30).claim
    second_claim = store.claim(second_id, "runtime-b", 110, 30).claim
    assert first_claim is not None and second_claim is not None

    cross_saga = PortResult(
        PortOutcome.REJECTED,
        "cross-saga-replay",
        second_claim.intent.intent_id,
        second_claim.intent.binding_hash,
        second_claim.fencing_token,
    )
    result = runtime.settle_observation(first_claim, cross_saga, 111)
    assert result.code is StoreResultCode.CONFLICT
    assert store.load_outbox(first_id).state is OutboxState.CLAIMED  # type: ignore[union-attr]
    assert store.load_outbox(second_id).state is OutboxState.CLAIMED  # type: ignore[union-attr]


def test_phase3j_runtime_modules_have_no_network_thread_or_operational_job_boundary() -> None:
    module_names = (
        "cross_store_saga_codec.py",
        "cross_store_saga_store.py",
        "cross_store_saga_ports.py",
        "cross_store_saga_runtime.py",
        "saga_runtime_config.py",
    )
    forbidden_imports = {"asyncio", "httpx", "requests", "socket", "subprocess", "threading", "urllib"}
    for module_name in module_names:
        source = (ROOT / "python-api" / "scraping" / module_name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        roots = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not (roots & forbidden_imports)
        assert "scraping.jobs" not in source
        assert "from .jobs" not in source
        assert "Thread(" not in source
        assert "urlopen(" not in source

"""Executable-but-disabled Phase 3J saga runtime coordinator.

The coordinator performs only disposable SQLite mutations.  It has no real
effect adapter and hard-wires worker dispatch to a deny adapter.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cross_store_saga_codec import canonical_sha256
from .cross_store_saga_contract import EffectAction, SagaBinding, SagaSnapshot
from .cross_store_saga_ports import (
    DenyWorkerDispatchAdapter,
    PortOutcome,
    PortResult,
    UnavailableEffectAdapter,
)
from .cross_store_saga_store import (
    OutboxClaim,
    OutboxRecord,
    SagaStore,
    StoreMutation,
    StoreResultCode,
)
from .saga_runtime_config import SagaRuntimeConfig


@dataclass(frozen=True)
class RuntimeResult:
    code: StoreResultCode
    reason_code: str | None = None
    snapshot: SagaSnapshot | None = None
    outbox: OutboxRecord | None = None
    port_outcome: PortOutcome | None = None
    recovered_claims: int = 0


class SagaRuntime:
    """Narrow facade used only by the Phase 3J disposable evidence harness."""

    def __init__(self, config: SagaRuntimeConfig, store: SagaStore | None = None) -> None:
        if not isinstance(config, SagaRuntimeConfig):
            raise TypeError("saga-runtime-config-required")
        self._config = config
        self._store = store
        if config.executable and store is None:
            self._store = SagaStore(config)
        if not config.executable and store is not None:
            raise ValueError("disabled-runtime-store-forbidden")
        self._worker_adapter = DenyWorkerDispatchAdapter()
        self._unavailable_adapter = UnavailableEffectAdapter()

    @property
    def enabled(self) -> bool:
        return self._config.executable

    @property
    def store(self) -> SagaStore | None:
        return self._store

    def _disabled(self) -> RuntimeResult | None:
        if not self.enabled or self._store is None:
            return RuntimeResult(StoreResultCode.UNAVAILABLE, "phase3j-runtime-disabled")
        return None

    @staticmethod
    def _result(mutation: StoreMutation, *, outcome: PortOutcome | None = None) -> RuntimeResult:
        return RuntimeResult(
            code=mutation.code,
            reason_code=mutation.reason_code,
            snapshot=mutation.snapshot,
            outbox=mutation.outbox,
            port_outcome=outcome,
            recovered_claims=mutation.recovered_count,
        )

    def initialize(self) -> RuntimeResult:
        disabled = self._disabled()
        if disabled is not None:
            return disabled
        assert self._store is not None
        return self._result(self._store.initialize())

    def prepare(self, binding: SagaBinding, now_epoch: int) -> RuntimeResult:
        disabled = self._disabled()
        if disabled is not None:
            return disabled
        assert self._store is not None
        return self._result(self._store.prepare(binding, now_epoch))

    @staticmethod
    def _port_fingerprint(result: PortResult) -> str:
        return canonical_sha256(
            {
                "outcome": result.outcome.value,
                "reason_code": result.reason_code,
                "intent_id": result.intent_id,
                "binding_hash": result.binding_hash,
                "fencing_token": result.fencing_token,
                "receipt_hash": result.receipt_hash,
            }
        )

    def settle_observation(
        self,
        claim: OutboxClaim,
        result: PortResult,
        now_epoch: int,
    ) -> RuntimeResult:
        """Persist an already-observed outcome; this method performs no effect."""

        disabled = self._disabled()
        if disabled is not None:
            return disabled
        if not isinstance(result, PortResult):
            return RuntimeResult(StoreResultCode.REJECTED, "port-result-invalid")
        if (
            result.intent_id != claim.intent.intent_id
            or result.binding_hash != claim.intent.binding_hash
            or result.fencing_token != claim.fencing_token
        ):
            return RuntimeResult(
                StoreResultCode.CONFLICT,
                "port-result-correlation-conflict",
                outbox=claim.record,
                port_outcome=result.outcome,
            )
        assert self._store is not None
        fingerprint = self._port_fingerprint(result)
        if result.outcome is PortOutcome.CONFIRMED:
            mutation = self._store.acknowledge(
                claim, fingerprint, result.reason_code, now_epoch
            )
        elif result.outcome is PortOutcome.UNAVAILABLE:
            mutation = self._store.release(claim, now_epoch)
        else:
            # Rejected, ambiguous, and conflict outcomes are blocked.  In
            # particular an ambiguous result must not be retried blindly.
            mutation = self._store.block(
                claim, fingerprint, result.reason_code, now_epoch
            )
        return self._result(mutation, outcome=result.outcome)

    def process_pending(
        self,
        intent_id: str,
        owner: str,
        now_epoch: int,
        lease_seconds: int = 30,
    ) -> RuntimeResult:
        """Claim one intent and invoke only a deny/unavailable local adapter."""

        disabled = self._disabled()
        if disabled is not None:
            return disabled
        assert self._store is not None
        claimed = self._store.claim(intent_id, owner, now_epoch, lease_seconds)
        if claimed.code is not StoreResultCode.APPLIED or claimed.claim is None:
            return self._result(claimed)
        claim = claimed.claim
        adapter = (
            self._worker_adapter
            if claim.intent.action is EffectAction.DISPATCH_WORKER
            else self._unavailable_adapter
        )
        # Both adapters declare remote_effects=False and are defined in the
        # same sealed module.  Any future widening fails closed.
        if adapter.remote_effects is not False:
            result = PortResult(
                PortOutcome.CONFLICT,
                "remote-effect-adapter-forbidden",
                claim.intent.intent_id,
                claim.intent.binding_hash,
                claim.fencing_token,
            )
        else:
            result = adapter.execute(
                claim.intent,
                fencing_token=claim.fencing_token,
            )
        return self.settle_observation(claim, result, now_epoch)

    def recover(
        self,
        operation_id: str,
        observed_at_epoch: int,
        *,
        owner: str = "phase3j-recovery",
        lease_seconds: int = 30,
    ) -> RuntimeResult:
        """Recover expired claims and saga state, denying any dispatch intent."""

        disabled = self._disabled()
        if disabled is not None:
            return disabled
        assert self._store is not None
        saga = self._store.recover(operation_id, observed_at_epoch)
        if saga.code in {StoreResultCode.REJECTED, StoreResultCode.CONFLICT, StoreResultCode.NOT_FOUND}:
            return self._result(saga)
        snapshot = saga.snapshot or self._store.load_snapshot(operation_id)
        if (
            snapshot is not None
            and snapshot.pending_intent is not None
            and snapshot.pending_intent.action is EffectAction.DISPATCH_WORKER
        ):
            denied = self.process_pending(
                snapshot.pending_intent.intent_id,
                owner,
                observed_at_epoch,
                lease_seconds,
            )
            return RuntimeResult(
                code=denied.code,
                reason_code=denied.reason_code,
                snapshot=snapshot,
                outbox=denied.outbox,
                port_outcome=denied.port_outcome,
                recovered_claims=saga.recovered_count,
            )
        return RuntimeResult(
            code=saga.code,
            reason_code=saga.reason_code,
            snapshot=snapshot,
            outbox=saga.outbox,
            recovered_claims=saga.recovered_count,
        )

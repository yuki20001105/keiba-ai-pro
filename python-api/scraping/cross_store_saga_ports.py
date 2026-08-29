"""Effect-port outcomes for Phase 3J.

Only deny/unavailable adapters are implemented.  Real review, quota, worker,
network, or operational-database adapters intentionally do not exist here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .cross_store_saga_contract import EffectAction, EffectIntent


class PortOutcome(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class PortResult:
    outcome: PortOutcome
    reason_code: str
    intent_id: str
    binding_hash: str
    fencing_token: int
    receipt_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, PortOutcome):
            raise ValueError("port-outcome-invalid")
        if (
            not isinstance(self.reason_code, str)
            or not self.reason_code
            or len(self.reason_code) > 64
            or not self.reason_code.replace("-", "").isalnum()
            or not self.reason_code[0].isalpha()
        ):
            raise ValueError("port-reason-code-invalid")
        if self.receipt_hash is not None and (
            not isinstance(self.receipt_hash, str)
            or len(self.receipt_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.receipt_hash)
        ):
            raise ValueError("port-receipt-hash-invalid")
        for value, code in (
            (self.intent_id, "port-intent-id-invalid"),
            (self.binding_hash, "port-binding-hash-invalid"),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(code)
        if type(self.fencing_token) is not int or self.fencing_token < 1:
            raise ValueError("port-fencing-token-invalid")
        if self.outcome is PortOutcome.CONFIRMED and self.receipt_hash is None:
            raise ValueError("confirmed-receipt-required")
        if self.outcome is not PortOutcome.CONFIRMED and self.receipt_hash is not None:
            raise ValueError("unexpected-port-receipt")


class SagaEffectPort(Protocol):
    """A narrow adapter protocol; Phase 3J supplies deny-only implementations."""

    remote_effects: bool

    def execute(self, intent: EffectIntent, *, fencing_token: int) -> PortResult:
        ...


@dataclass(frozen=True)
class DenyWorkerDispatchAdapter:
    remote_effects: bool = False

    def execute(self, intent: EffectIntent, *, fencing_token: int) -> PortResult:
        if not isinstance(intent, EffectIntent) or intent.action is not EffectAction.DISPATCH_WORKER:
            if not isinstance(intent, EffectIntent):
                raise ValueError("worker-dispatch-intent-invalid")
            return PortResult(
                PortOutcome.CONFLICT,
                "worker-dispatch-intent-invalid",
                intent.intent_id,
                intent.binding_hash,
                fencing_token,
            )
        return PortResult(
            PortOutcome.REJECTED,
            "worker-dispatch-disabled",
            intent.intent_id,
            intent.binding_hash,
            fencing_token,
        )


@dataclass(frozen=True)
class UnavailableEffectAdapter:
    """Safe default for every non-worker effect in this disabled slice."""

    remote_effects: bool = False

    def execute(self, intent: EffectIntent, *, fencing_token: int) -> PortResult:
        if not isinstance(intent, EffectIntent):
            raise ValueError("effect-intent-invalid")
        return PortResult(
            PortOutcome.UNAVAILABLE,
            "effect-adapter-unavailable",
            intent.intent_id,
            intent.binding_hash,
            fencing_token,
        )

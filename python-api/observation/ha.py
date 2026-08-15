from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import ObservationContractError, canonical_sha256


@dataclass(frozen=True)
class HaClaim:
    job_id: str
    job_kind: str
    request_payload: dict[str, Any]
    worker_owner: str
    fencing_token: int
    lease_expires_at: str
    attempt_count: int


def _one(response: Any, code: str) -> dict[str, Any]:
    data = getattr(response, "data", None)
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ObservationContractError(code)
    return dict(data[0])


class HaGateway:
    def __init__(self, client: Any) -> None:
        if client is None or not callable(getattr(client, "rpc", None)):
            raise ObservationContractError("ha-client-unavailable")
        self._client = client

    def _rpc(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return _one(self._client.rpc(name, params).execute(), f"{name}-failed")
        except ObservationContractError:
            raise
        except Exception as exc:
            raise ObservationContractError(f"{name}-failed") from exc

    def enqueue(self, idempotency_key: str, job_kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        digest = canonical_sha256(payload)
        return self._rpc(
            "enqueue_phase3n_ha_job",
            {
                "p_idempotency_key": idempotency_key,
                "p_job_kind": job_kind,
                "p_request_payload": payload,
                "p_request_sha256": digest,
            },
        )

    def claim(self, owner: str, lease_seconds: int, job_kind: str | None = None) -> HaClaim | None:
        row = self._rpc(
            "claim_phase3n_ha_job",
            {
                "p_worker_owner": owner,
                "p_lease_seconds": lease_seconds,
                "p_job_kind": job_kind,
            },
        )
        if row.get("mutation_code") == "not-found":
            return None
        if row.get("mutation_code") != "applied" or not isinstance(row.get("request_payload"), dict):
            raise ObservationContractError("ha-claim-invalid")
        return HaClaim(
            job_id=str(row["returned_job_id"]),
            job_kind=str(row["returned_job_kind"]),
            request_payload=dict(row["request_payload"]),
            worker_owner=str(row["worker_owner"]),
            fencing_token=int(row["fencing_token"]),
            lease_expires_at=str(row["lease_expires_at"]),
            attempt_count=int(row["attempt_count"]),
        )

    def heartbeat(self, claim: HaClaim, lease_seconds: int) -> dict[str, Any]:
        return self._rpc(
            "heartbeat_phase3n_ha_job",
            {
                "p_job_id": claim.job_id,
                "p_worker_owner": claim.worker_owner,
                "p_fencing_token": claim.fencing_token,
                "p_lease_seconds": lease_seconds,
            },
        )

    def apply_effect(self, claim: HaClaim, effect_key: str, effect: dict[str, Any]) -> dict[str, Any]:
        return self._rpc(
            "apply_phase3n_ha_effect",
            {
                "p_job_id": claim.job_id,
                "p_worker_owner": claim.worker_owner,
                "p_fencing_token": claim.fencing_token,
                "p_effect_key": effect_key,
                "p_effect_sha256": canonical_sha256(effect),
            },
        )

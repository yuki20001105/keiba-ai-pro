from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from observation.cache_integrity import verify_cache, write_cache_atomic
from observation.contracts import canonical_sha256, timestamp, with_payload_sha256
from observation.ha import HaClaim, HaGateway


BASE_URL = os.environ.get("PHASE3N_HA_REST_URL", "http://rest:3000").rstrip("/")
EVIDENCE_DIR = Path("/evidence")


class _RpcCall:
    def __init__(self, name: str, params: dict[str, Any]) -> None:
        self.name = name
        self.params = params

    def execute(self) -> Any:
        response = httpx.post(
            f"{BASE_URL}/rpc/{self.name}",
            json=self.params,
            timeout=10.0,
        )
        response.raise_for_status()
        return SimpleNamespace(data=response.json())


class RestClient:
    def rpc(self, name: str, params: dict[str, Any]) -> _RpcCall:
        return _RpcCall(name, params)


def _get(table: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
    response = httpx.get(f"{BASE_URL}/{table}", params=params or {"select": "*"}, timeout=10.0)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("postgrest-table-response-invalid")
    return [dict(row) for row in payload]


def _event(kind: str, instance_id: str, **values: Any) -> None:
    print(
        json.dumps(
            {
                "event": kind,
                "instance_id": instance_id,
                "observed_at": timestamp(datetime.now(timezone.utc)),
                **values,
            },
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
        ),
        flush=True,
    )


def wait_ready(_args: argparse.Namespace) -> int:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{BASE_URL}/", timeout=2.0)
            if response.status_code == 200:
                return 0
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return 1


def enqueue(args: argparse.Namespace) -> int:
    payload = {"synthetic_contract_test": True, "scenario": args.scenario}
    result = HaGateway(RestClient()).enqueue(
        f"ha-contract-test:{args.scenario}:0001",
        "ha-contract-test",
        payload,
    )
    _event("enqueue", "controller", scenario=args.scenario, result=result)
    return 0 if result.get("mutation_code") in {"inserted", "duplicate"} else 1


def claim_hold(args: argparse.Namespace) -> int:
    gateway = HaGateway(RestClient())
    claim = gateway.claim(args.instance_id, args.lease_seconds, "ha-contract-test")
    if claim is None:
        _event("claim-not-found", args.instance_id)
        return 2
    _event("claimed", args.instance_id, **asdict(claim))

    def stopping(signum: int, _frame: Any) -> None:
        _event("terminated", args.instance_id, signal=signum, fencing_token=claim.fencing_token)
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stopping)
    time.sleep(args.hold_seconds)
    _event("hold-finished-without-effect", args.instance_id, fencing_token=claim.fencing_token)
    return 3


def claim_apply(args: argparse.Namespace) -> int:
    gateway = HaGateway(RestClient())
    deadline = time.monotonic() + args.wait_seconds
    claim: HaClaim | None = None
    while time.monotonic() < deadline:
        claim = gateway.claim(args.instance_id, args.lease_seconds, "ha-contract-test")
        if claim is not None:
            break
        time.sleep(0.25)
    if claim is None:
        _event("takeover-timeout", args.instance_id)
        return 2
    _event("takeover-claimed", args.instance_id, **asdict(claim))
    effect = {"synthetic_contract_test": True, "accepted_owner": args.instance_id}
    result = gateway.apply_effect(claim, f"effect:{claim.job_id}", effect)
    _event("effect-result", args.instance_id, result=result, fencing_token=claim.fencing_token)
    return 0 if result.get("mutation_code") == "applied" else 1


def stale_apply(args: argparse.Namespace) -> int:
    events = _get(
        "phase3n_ha_events",
        {
            "select": "job_id,worker_owner,fencing_token,event_type,event_id",
            "event_type": "eq.claimed",
            "worker_owner": f"eq.{args.instance_id}",
            "order": "event_id.asc",
            "limit": "1",
        },
    )
    if len(events) != 1:
        _event("stale-source-missing", args.instance_id)
        return 2
    event = events[0]
    claim = HaClaim(
        job_id=str(event["job_id"]),
        job_kind="ha-contract-test",
        request_payload={},
        worker_owner=args.instance_id,
        fencing_token=int(event["fencing_token"]),
        lease_expires_at="expired",
        attempt_count=1,
    )
    result = HaGateway(RestClient()).apply_effect(
        claim,
        f"stale-effect:{claim.job_id}",
        {"synthetic_contract_test": True, "stale_owner": args.instance_id},
    )
    _event("stale-effect-result", args.instance_id, result=result, fencing_token=claim.fencing_token)
    return 0 if result.get("mutation_code") == "conflict" else 1


def _seed_manifest() -> dict[str, Any]:
    columns = ["horse_number", "odds"]
    base = {
        "idempotency_key": "model:synthetic-contract-test:phase3n-ha",
        "model_id": "synthetic-contract-test",
        "model_version": "synthetic-v1",
        "model_artifact_sha256": "1" * 64,
        "candidate_commit_sha": "2" * 40,
        "feature_manifest_sha256": canonical_sha256(columns),
        "model_feature_columns": columns,
        "training_data_ended_at": timestamp(datetime.now(timezone.utc) - timedelta(days=2)),
        "expanding_window_checks_passed": True,
        "source_environment": "staging",
    }
    return with_payload_sha256(base)


def seed_cache_source(_args: argparse.Namespace) -> int:
    client = RestClient()
    manifest = _seed_manifest()
    manifest_result = client.rpc("register_phase3n_model_manifest", {"p_manifest": manifest}).execute().data[0]
    manifest_id = manifest_result["returned_manifest_id"]
    observed = datetime.now(timezone.utc) - timedelta(minutes=2)
    base = {
        "manifest_id": manifest_id,
        "race_id": "209901010101",
        "horse_id": "synthetic-horse-01",
        "horse_number": 1,
        "race_date": datetime.now(timezone.utc).date().isoformat(),
        "data_observed_at": timestamp(observed),
        "data_cutoff_at": timestamp(observed),
        "feature_values_sha256": canonical_sha256([["horse_number", 1], ["odds", 2.5]]),
        "predicted_value": 0.5,
        "predicted_probability": 0.5,
        "predicted_rank": 1,
        "odds_at_prediction": 2.5,
        "recommendation": "unavailable",
        "qualifying_bet": False,
        "wager_amount": 0.0,
        "baseline_wager_amount": 0.0,
        "latency_ms": 10.0,
        "source_environment": "staging",
        "leakage_violation_count": 0,
    }
    digest = canonical_sha256(base)
    base["observation_id"] = "aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa"
    base["idempotency_key"] = f"prediction:{digest}"
    prediction = with_payload_sha256(base)
    result = client.rpc(
        "record_phase3n_prediction_observation", {"p_observation": prediction}
    ).execute().data[0]
    _event("cache-source-seeded", "controller", result=result, synthetic_contract_test=True)
    return 0 if result.get("mutation_code") in {"inserted", "duplicate"} else 1


def _cache_rows() -> list[dict[str, Any]]:
    return _get(
        "phase3n_prediction_observations",
        {
            "select": "observation_id,race_id,horse_id,horse_number,feature_values_sha256,predicted_probability",
            "order": "observation_id.asc",
        },
    )


def cache_build(args: argparse.Namespace) -> int:
    path = EVIDENCE_DIR / "phase3n-cache.json"
    digest = write_cache_atomic(path, _cache_rows())
    _event("cache-built", args.instance_id, cache_digest_sha256=digest)
    if args.hold_seconds:
        time.sleep(args.hold_seconds)
    return 0


def cache_delete(_args: argparse.Namespace) -> int:
    path = (EVIDENCE_DIR / "phase3n-cache.json").resolve()
    if path.parent != EVIDENCE_DIR.resolve() or path.name != "phase3n-cache.json":
        raise RuntimeError("cache-delete-target-invalid")
    path.unlink(missing_ok=True)
    _event("cache-deleted", "controller")
    return 0


def cache_verify(_args: argparse.Namespace) -> int:
    report = verify_cache(EVIDENCE_DIR / "phase3n-cache.json", _cache_rows())
    _event("cache-verified", "controller", **report)
    return 0 if report["success"] else 1


def digest(_args: argparse.Namespace) -> int:
    tables = {}
    for table in (
        "phase3n_model_manifests",
        "phase3n_prediction_observations",
        "phase3n_result_observation_events",
    ):
        tables[table] = _get(table, {"select": "*"})
    print(json.dumps({"digest_sha256": canonical_sha256(tables)}, sort_keys=True), flush=True)
    return 0


def evidence(_args: argparse.Namespace) -> int:
    jobs = _get("phase3n_ha_jobs", {"select": "*", "order": "created_at.asc"})
    effects = _get("phase3n_ha_effects", {"select": "*", "order": "applied_at.asc"})
    events = _get("phase3n_ha_events", {"select": "*", "order": "event_id.asc"})
    stale = sum(row["event_type"] == "stale-fence-rejected" for row in events)
    split_brain = sum(row["state"] == "leased" for row in jobs)
    duplicate_effects = len(effects) - len({row["effect_key"] for row in effects})
    recovery_ms_by_job: dict[str, float] = {}
    for job in jobs:
        timeline = [row for row in events if row["job_id"] == job["job_id"]]
        claimed = next((row for row in timeline if row["event_type"] == "claimed"), None)
        applied = next((row for row in timeline if row["event_type"] == "effect-applied"), None)
        if claimed is not None and applied is not None:
            started = datetime.fromisoformat(str(claimed["occurred_at"]).replace("Z", "+00:00"))
            finished = datetime.fromisoformat(str(applied["occurred_at"]).replace("Z", "+00:00"))
            recovery_ms_by_job[str(job["job_id"])] = round(
                (finished - started).total_seconds() * 1000.0, 3
            )
    report = {
        "schema": "phase3n-ha-contract-evidence",
        "schema_version": 1,
        "synthetic_contract_test": True,
        "production_evidence": False,
        "job_count": len(jobs),
        "completed_job_count": sum(row["state"] == "completed" for row in jobs),
        "effect_count": len(effects),
        "duplicate_effect_count": duplicate_effects,
        "duplicate_processing_count": duplicate_effects,
        "duplicate_bet_count": duplicate_effects,
        "duplicate_scheduler_execution_count": duplicate_effects,
        "stale_fence_rejection_count": stale,
        "split_brain_count": split_brain,
        "recovery_ms_by_job": recovery_ms_by_job,
        "jobs": jobs,
        "effects": effects,
        "timeline": events,
        "success": (
            len(jobs) >= 1
            and all(row["state"] == "completed" for row in jobs)
            and len(effects) == len(jobs)
            and stale >= len(jobs)
            and split_brain == 0
        ),
    }
    print(json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True), flush=True)
    return 0 if report["success"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("wait-ready").set_defaults(func=wait_ready)
    enqueue_parser = sub.add_parser("enqueue")
    enqueue_parser.add_argument("--scenario", required=True)
    enqueue_parser.set_defaults(func=enqueue)
    hold = sub.add_parser("claim-hold")
    hold.add_argument("--instance-id", required=True)
    hold.add_argument("--lease-seconds", type=int, default=3)
    hold.add_argument("--hold-seconds", type=int, default=300)
    hold.set_defaults(func=claim_hold)
    apply = sub.add_parser("claim-apply")
    apply.add_argument("--instance-id", required=True)
    apply.add_argument("--lease-seconds", type=int, default=3)
    apply.add_argument("--wait-seconds", type=int, default=30)
    apply.set_defaults(func=claim_apply)
    stale = sub.add_parser("stale-apply")
    stale.add_argument("--instance-id", required=True)
    stale.set_defaults(func=stale_apply)
    sub.add_parser("seed-cache-source").set_defaults(func=seed_cache_source)
    cache = sub.add_parser("cache-build")
    cache.add_argument("--instance-id", default="cache-builder")
    cache.add_argument("--hold-seconds", type=int, default=0)
    cache.set_defaults(func=cache_build)
    sub.add_parser("cache-delete").set_defaults(func=cache_delete)
    sub.add_parser("cache-verify").set_defaults(func=cache_verify)
    sub.add_parser("digest").set_defaults(func=digest)
    sub.add_parser("evidence").set_defaults(func=evidence)
    return parser.parse_args()


if __name__ == "__main__":
    _args = parse_args()
    raise SystemExit(_args.func(_args))

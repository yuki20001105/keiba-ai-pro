#!/usr/bin/env python3
"""Fail-closed Limited Production monitor with sanitized JSON evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


EXPECTED_CONTRACT = "limited-production-observation-v1"
SHA_LENGTH = 40


@dataclass(frozen=True)
class Probe:
    name: str
    passed: bool
    status: int | None
    code: str
    facts: dict[str, Any]


def _request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    body: bytes | None = None,
    timeout: float = 12.0,
    attempts: int = 1,
    opener: Callable[..., Any] = urlopen,
) -> tuple[int, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(url, data=body, headers=headers or {}, method=method)
            with opener(request, timeout=timeout) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(2**attempt, 4))
    raise RuntimeError(type(last_error).__name__ if last_error else "request-failed")


def _runtime_probe(name: str, url: str, *, opener: Callable[..., Any] = urlopen) -> Probe:
    try:
        status, payload = _request_json(url, attempts=3, opener=opener)
    except RuntimeError as exc:
        return Probe(name, False, None, f"{name}-unreachable", {"error_type": str(exc)})
    expected = {
        "status": "ok",
        "model_runtime_status": "observation",
        "observation_enabled": True,
        "observation_release_mode": "limited-observation",
        "automated_betting_enabled": False,
    }
    if not isinstance(payload, dict):
        return Probe(name, False, status, f"{name}-payload-invalid", {})
    mismatches = sorted(key for key, value in expected.items() if payload.get(key) != value)
    return Probe(
        name,
        status == 200 and not mismatches,
        status,
        "ok" if status == 200 and not mismatches else f"{name}-safety-state-invalid",
        {"mismatched_fields": mismatches},
    )


def _supabase_probes(
    base_url: str, service_key: str, *, opener: Callable[..., Any] = urlopen
) -> list[Probe]:
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    probes: list[Probe] = []
    try:
        status, _ = _request_json(f"{base_url.rstrip('/')}/auth/v1/health", headers=headers, opener=opener)
        probes.append(Probe("supabase_auth", status == 200, status, "ok" if status == 200 else "supabase-auth-failed", {}))
    except RuntimeError as exc:
        probes.append(Probe("supabase_auth", False, None, "supabase-auth-unreachable", {"error_type": str(exc)}))

    try:
        status, payload = _request_json(
            f"{base_url.rstrip('/')}/rest/v1/rpc/phase3n_operational_runtime_health",
            headers=headers,
            method="POST",
            body=b"{}",
            opener=opener,
        )
        valid = (
            status == 200
            and isinstance(payload, list)
            and len(payload) == 1
            and payload[0].get("ready") is True
            and payload[0].get("schema_version") == 1
        )
        probes.append(Probe("supabase_runtime", valid, status, "ok" if valid else "supabase-runtime-invalid", {}))
    except RuntimeError as exc:
        probes.append(Probe("supabase_runtime", False, None, "supabase-runtime-unreachable", {"error_type": str(exc)}))

    cutoff = quote((datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat())
    query = (
        "select=attempt_id,event_kind,outcome,failure_code,attempted_at"
        "&outcome=in.(conflict,rejected)&order=attempted_at.desc&limit=1"
        f"&attempted_at=gte.{cutoff}"
    )
    try:
        status, payload = _request_json(
            f"{base_url.rstrip('/')}/rest/v1/phase3n_observation_ingest_attempts?{query}",
            headers=headers,
            opener=opener,
        )
        valid = status == 200 and isinstance(payload, list) and not payload
        probes.append(
            Probe(
                "observation_failures",
                valid,
                status,
                "ok" if valid else "observation-conflict-or-rejection-detected",
                {"recent_failure_count_capped": 0 if valid else 1},
            )
        )
    except RuntimeError as exc:
        probes.append(Probe("observation_failures", False, None, "observation-failure-query-unreachable", {"error_type": str(exc)}))
    return probes


def _find_render_deploy(payload: Any) -> tuple[str | None, str | None, str | None]:
    item = payload[0] if isinstance(payload, list) and payload else payload
    if isinstance(item, dict) and isinstance(item.get("deploy"), dict):
        item = item["deploy"]
    if not isinstance(item, dict):
        return None, None, None
    commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
    return (
        str(item.get("id")) if item.get("id") else None,
        str(commit.get("id") or item.get("commitId") or "") or None,
        str(item.get("status") or "") or None,
    )


def _render_probe(
    service_id: str,
    api_key: str,
    expected_sha: str,
    *,
    opener: Callable[..., Any] = urlopen,
) -> Probe:
    try:
        status, payload = _request_json(
            f"https://api.render.com/v1/services/{service_id}/deploys?limit=1",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            opener=opener,
        )
    except RuntimeError as exc:
        return Probe("render_exact_sha", False, None, "render-deploy-unreachable", {"error_type": str(exc)})
    deployment_id, commit_sha, deploy_status = _find_render_deploy(payload)
    valid = status == 200 and commit_sha == expected_sha and deploy_status == "live"
    return Probe(
        "render_exact_sha",
        valid,
        status,
        "ok" if valid else "render-exact-sha-or-status-mismatch",
        {
            "service_id": service_id,
            "deployment_id": deployment_id,
            "commit_sha": commit_sha,
            "deploy_status": deploy_status,
        },
    )


def run(env: dict[str, str], *, opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    required = (
        "PRODUCTION_FRONTEND_HEALTH_URL",
        "PRODUCTION_BACKEND_HEALTH_URL",
        "PRODUCTION_SUPABASE_URL",
        "PRODUCTION_SUPABASE_SERVICE_KEY",
        "PRODUCTION_RENDER_SERVICE_ID",
        "RENDER_API_KEY",
    )
    missing = sorted(key for key in required if not env.get(key, "").strip())
    expected_deploy_sha = env.get("PRODUCTION_EXPECTED_DEPLOY_SHA", "").strip().lower()
    candidate_sha = env.get("PHASE3N_CANDIDATE_COMMIT_SHA", "").strip().lower()
    binding_missing = (
        []
        if missing
        else sorted(
            name
            for name, value in (
                ("PRODUCTION_EXPECTED_DEPLOY_SHA", expected_deploy_sha),
                ("PHASE3N_CANDIDATE_COMMIT_SHA", candidate_sha),
            )
            if not value
        )
    )
    invalid_sha = sorted(
        name
        for name, value in (
            ("PRODUCTION_EXPECTED_DEPLOY_SHA", expected_deploy_sha),
            ("PHASE3N_CANDIDATE_COMMIT_SHA", candidate_sha),
        )
        if value and (len(value) != SHA_LENGTH or any(char not in "0123456789abcdef" for char in value))
    )
    probes: list[Probe] = []
    if not missing and not binding_missing and not invalid_sha:
        probes.extend(
            [
                _runtime_probe("frontend", env["PRODUCTION_FRONTEND_HEALTH_URL"], opener=opener),
                _runtime_probe("backend", env["PRODUCTION_BACKEND_HEALTH_URL"], opener=opener),
            ]
        )
        probes.extend(_supabase_probes(env["PRODUCTION_SUPABASE_URL"], env["PRODUCTION_SUPABASE_SERVICE_KEY"], opener=opener))
        probes.append(
            _render_probe(
                env["PRODUCTION_RENDER_SERVICE_ID"],
                env["RENDER_API_KEY"],
                expected_deploy_sha,
                opener=opener,
            )
        )
    failures = (
        [f"missing-config:{name}" for name in missing]
        + [f"missing-config:{name}" for name in binding_missing]
        + [f"invalid-config:{name}" for name in invalid_sha]
        + [probe.code for probe in probes if not probe.passed]
    )
    return {
        "schema": "limited-production-monitor-evidence",
        "schema_version": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "expected_candidate_sha": candidate_sha or None,
        "expected_production_deploy_sha": expected_deploy_sha or None,
        "release_contract_id": EXPECTED_CONTRACT,
        "success": not failures,
        "failure_codes": failures,
        "probes": [
            {"name": p.name, "pass": p.passed, "status": p.status, "code": p.code, "facts": p.facts}
            for p in probes
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("reports/limited_production_monitor.json"))
    args = parser.parse_args()
    report = run(dict(os.environ))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": report["success"], "failure_codes": report["failure_codes"]}))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())

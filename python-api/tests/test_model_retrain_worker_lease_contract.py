from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "20260802_model_retrain_worker_lease.sql"
MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
CONTRACT = ROOT / "supabase" / "bootstrap" / "v1" / "tests" / "bootstrap_contract.sql"
JOB_LEDGER = ROOT / "src" / "lib" / "model-retrain-job-ledger.ts"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _function(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION public\.{re.escape(name)}\b(?P<body>.*?)\n\$\$;",
        _sql(),
        flags=re.DOTALL,
    )
    assert match, f"missing function: {name}"
    return match.group("body")


def test_worker_state_machine_is_private_fenced_and_artifact_disabled() -> None:
    sql = _sql()
    assert "CREATE SEQUENCE IF NOT EXISTS public.model_retrain_job_fencing_seq" in sql
    assert "REVOKE ALL ON SEQUENCE public.model_retrain_job_fencing_seq" in sql
    assert "job_state IN ('queued', 'claimed', 'running', 'failed')" in sql
    assert "NEW.artifact_written IS DISTINCT FROM FALSE" in sql
    assert "NEW.artifact_uri IS NOT NULL OR NEW.artifact_sha256 IS NOT NULL" in sql
    assert "model retrain job binding is immutable" in sql
    assert "BEFORE UPDATE ON public.model_retrain_jobs" in sql
    assert "BEFORE DELETE ON public.model_retrain_jobs" in sql
    assert "model retrain jobs cannot be deleted" in sql
    assert "model retrain job events are append-only" in sql


def test_claim_rechecks_approval_and_issues_monotonic_bounded_lease() -> None:
    body = _function("claim_model_retrain_job")
    for fragment in (
        "v_job.record_version <> p_expected_version",
        "v_job.job_state <> 'queued'",
        "PERFORM public._expire_model_retrain_approval_if_needed",
        "v_approval.approval_status <> 'approved'",
        "v_approval.job_created IS DISTINCT FROM TRUE",
        "v_approval.approved_payload_hash IS DISTINCT FROM v_job.approved_payload_hash",
        "LEAST(v_now + make_interval(secs => p_ttl_seconds), v_approval.expires_at)",
        "nextval('public.model_retrain_job_fencing_seq'::regclass)",
        "'claimed'",
    ):
        assert fragment in body


def test_heartbeat_start_and_failure_reject_stale_worker_or_token() -> None:
    heartbeat = _function("heartbeat_model_retrain_job")
    start = _function("start_model_retrain_job")
    fail = _function("fail_model_retrain_job")
    for body in (heartbeat, start, fail):
        assert "v_job.record_version <> p_expected_version" in body
        assert "v_job.worker_id IS DISTINCT FROM p_worker_id" in body
        assert "v_job.fencing_token IS DISTINCT FROM p_fencing_token" in body
        assert "v_job.lease_expires_at <= v_now" in body
    assert "execution_started = TRUE" in start
    assert "artifact_written" not in start
    assert "p_failure_code NOT IN" in fail
    assert "job_state = 'failed'" in fail


def test_expired_claim_is_requeued_but_expired_running_attempt_is_terminal() -> None:
    body = _function("recover_expired_model_retrain_job")
    assert "v_job.job_state NOT IN ('claimed', 'running')" in body
    assert "v_job.lease_expires_at > v_now" in body
    assert "IF v_from = 'claimed'" in body
    assert "job_state = 'queued'" in body
    assert "job_state = 'failed'" in body
    assert "failure_code = 'lease-expired-running'" in body
    assert "'lease-expired'" in body


def test_bootstrap_and_projection_include_worker_lease_contract() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [item for item in manifest["migrations"] if item["path"] == "supabase/migrations/20260802_model_retrain_worker_lease.sql"]
    assert len(entries) == 1
    assert entries[0]["version"] == "20260802142000"
    contract = CONTRACT.read_text(encoding="utf-8")
    for fragment in (
        "phase3m stale model retrain fencing token was accepted",
        "phase3m model retrain heartbeat contract failed",
        "phase3m model retrain worker start contract failed",
        "phase3m_check:model_retrain_worker_lease",
    ):
        assert fragment in contract
    ledger = JOB_LEDGER.read_text(encoding="utf-8")
    assert "export type ModelRetrainJobState" in ledger
    for state in ("queued", "claimed", "running", "artifact-registered", "evaluation-recorded", "failed"):
        assert f"| '{state}'" in ledger
    assert "fencing_token: number | null" in ledger
    assert "lease_expires_at: string | null" in ledger

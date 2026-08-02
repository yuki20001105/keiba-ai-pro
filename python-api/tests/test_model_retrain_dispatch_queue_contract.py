from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "20260802_model_retrain_dispatch_queue.sql"
MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
CONTRACT = ROOT / "supabase" / "bootstrap" / "v1" / "tests" / "bootstrap_contract.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _function() -> str:
    match = re.search(
        r"CREATE OR REPLACE FUNCTION public\.list_dispatchable_model_retrain_jobs\b"
        r"(?P<body>.*?)\n\$\$;",
        _sql(),
        flags=re.DOTALL,
    )
    assert match
    return match.group("body")


def test_dispatch_queue_is_read_only_bounded_and_service_only() -> None:
    sql = _sql()
    body = _function()
    assert "SECURITY DEFINER" in body
    assert "p_limit NOT BETWEEN 1 AND 5" in body
    assert "ORDER BY j.submitted_at, j.job_id" in body
    assert "LIMIT p_limit" in body
    for mutation in ("UPDATE ", "DELETE ", "INSERT "):
        assert mutation not in body
    assert ") FROM PUBLIC, anon, authenticated;" in sql
    assert ") TO service_role;" in sql


def test_dispatch_queue_rechecks_preliminary_approval_and_job_binding() -> None:
    body = _function()
    for fragment in (
        "j.job_state = 'queued'",
        "j.execution_started = FALSE",
        "j.artifact_written = FALSE",
        "j.worker_id IS NULL AND j.fencing_token IS NULL",
        "a.approval_status = 'approved'",
        "a.expires_at > clock_timestamp()",
        "a.job_created = TRUE",
        "a.execution_enabled = FALSE",
        "a.approved_payload_hash = j.approved_payload_hash",
        "a.dry_run_payload->>'git_commit' = p_candidate_commit_sha",
        "a.dry_run_payload->>'active_model_id' = p_active_model_id",
        "a.dry_run_payload->>'data_snapshot_id' <> repeat('0', 64)",
    ):
        assert fragment in body
    for check in (
        "future_field_exclusion",
        "out_of_time_split",
        "active_model_immutable",
        "production_write_blocked",
        "path_input_rejected",
        "data_snapshot_bound",
        "candidate_commit_bound",
    ):
        assert f'{{"key":"{check}","status":"pass"}}' in body


def test_dispatch_projection_cannot_start_or_claim_work() -> None:
    body = _function()
    for forbidden in (
        "claim_model_retrain_job",
        "start_model_retrain_job",
        "get_model_retrain_execution_bundle",
        "storage.objects",
        "artifact_written = TRUE",
    ):
        assert forbidden not in body


def test_bootstrap_manifest_and_contract_include_dispatch_queue() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [
        item for item in manifest["migrations"]
        if item["path"] == "supabase/migrations/20260802_model_retrain_dispatch_queue.sql"
    ]
    assert len(entries) == 1
    assert entries[0]["version"] == "20260802147000"
    contract = CONTRACT.read_text(encoding="utf-8")
    assert "phase3m model retrain dispatch queue contract failed" in contract
    assert "phase3m_check:model_retrain_dispatch_queue" in contract

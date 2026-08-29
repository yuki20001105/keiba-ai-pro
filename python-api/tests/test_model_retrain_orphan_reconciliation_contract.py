from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "20260802_model_retrain_orphan_reconciliation.sql"
MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
CONTRACT = ROOT / "supabase" / "bootstrap" / "v1" / "tests" / "bootstrap_contract.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _function(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION public\.{name}\b(?P<body>.*?)\n\$\$;",
        _sql(),
        flags=re.DOTALL,
    )
    assert match
    return match.group("body")


def test_orphan_tables_are_private_immutable_and_service_readable() -> None:
    sql = _sql()
    assert "model_retrain_orphan_reconciliation_runs ENABLE ROW LEVEL SECURITY" in sql
    assert "model_retrain_orphan_reconciliation_runs FORCE ROW LEVEL SECURITY" in sql
    assert "FROM PUBLIC, anon, authenticated, service_role" in sql
    assert "GRANT SELECT ON TABLE public.model_retrain_orphan_reconciliation_runs TO service_role" in sql
    assert "BEFORE UPDATE OR DELETE ON public.model_retrain_orphan_reconciliation_runs" in sql
    assert "authoritative_record = TRUE" in sql


def test_expired_projection_is_bounded_read_only_and_service_only() -> None:
    sql = _sql()
    body = _function("list_expired_model_retrain_job_candidates")
    for fragment in (
        "SECURITY DEFINER",
        "j.job_state IN ('claimed', 'running')",
        "j.lease_expires_at <= clock_timestamp()",
        "p_limit NOT BETWEEN 1 AND 50",
        "ORDER BY j.lease_expires_at, j.job_id",
    ):
        assert fragment in body
    for forbidden in ("UPDATE ", "DELETE ", "INSERT "):
        assert forbidden not in body
    assert "list_expired_model_retrain_job_candidates(TEXT, INTEGER)" in sql
    assert "TO service_role" in sql


def test_only_old_terminal_unregistered_exact_objects_are_candidates() -> None:
    body = _function("list_model_retrain_orphan_candidates")
    for fragment in (
        "o.bucket_id = 'models'",
        "^retrain/",
        "[0-9a-f]{64}\\.joblib$",
        "j.job_state = 'failed'",
        "j.artifact_written = FALSE",
        "j.finished_at IS NOT NULL",
        "NOT EXISTS (",
        "FROM public.model_retrain_artifacts AS a",
        "extensions.digest(",
        "ORDER BY o.created_at, o.name",
        "LIMIT p_limit",
    ):
        assert fragment in body
    for protected_state in ("'queued'", "'claimed'", "'running'", "'artifact-registered'"):
        assert f"j.job_state = {protected_state}" not in body


def test_audit_registration_rechecks_terminal_binding_and_storage_outcome() -> None:
    body = _function("record_model_retrain_orphan_reconciliation")
    for fragment in (
        "jsonb_array_length(p_observations) > p_limit",
        "model retrain orphan observations contain duplicates",
        "model retrain orphan reconciliation omitted candidates",
        "v_outcome NOT IN ('deleted', 'not-found', 'delete-failed')",
        "j.job_state = 'failed'",
        "j.artifact_written = FALSE",
        "FROM public.model_retrain_artifacts AS a",
        "model retrain orphan object still exists",
        "model retrain failed cleanup object is unavailable",
        "v_failed_count = 0",
    ):
        assert fragment in body
    assert "promotion_eligible" not in body
    assert "active_model" not in body


def test_browser_roles_cannot_execute_any_reconciliation_rpc() -> None:
    sql = _sql()
    for signature in (
        "list_expired_model_retrain_job_candidates(TEXT, INTEGER)",
        "list_model_retrain_orphan_candidates(TEXT, INTEGER, INTEGER)",
        "record_model_retrain_orphan_reconciliation(\n    TEXT, INTEGER, INTEGER, JSONB\n)",
    ):
        assert signature in sql
    assert sql.count("FROM PUBLIC, anon, authenticated;") == 3


def test_bootstrap_manifest_and_contract_include_orphan_reconciliation() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [
        item for item in manifest["migrations"]
        if item["path"] == "supabase/migrations/20260802_model_retrain_orphan_reconciliation.sql"
    ]
    assert len(entries) == 1
    assert entries[0]["version"] == "20260802146000"
    contract = CONTRACT.read_text(encoding="utf-8")
    assert "phase3m model retrain orphan reconciliation contract failed" in contract
    assert "phase3m_check:model_retrain_orphan_reconciliation" in contract

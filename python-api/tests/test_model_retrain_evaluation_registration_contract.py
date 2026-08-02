from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "20260802_model_retrain_evaluation_registration.sql"
MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
CONTRACT = ROOT / "supabase" / "bootstrap" / "v1" / "tests" / "bootstrap_contract.sql"


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


def test_evaluation_ledger_is_private_immutable_and_non_promoting() -> None:
    sql = _sql()
    assert "CREATE TABLE IF NOT EXISTS public.model_retrain_evaluations" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON TABLE public.model_retrain_evaluations" in sql
    assert "GRANT SELECT ON TABLE public.model_retrain_evaluations TO service_role" in sql
    assert "model retrain evaluations are immutable" in sql
    assert "trusted_promotion_evidence = FALSE" in sql
    assert "promotion_eligible = FALSE" in sql


def test_registration_requires_artifact_binding_current_approval_and_cas() -> None:
    body = _function("register_model_retrain_accepted_evaluation")
    for fragment in (
        "v_job.record_version <> p_expected_version",
        "v_job.job_state <> 'artifact-registered'",
        "v_job.artifact_written IS DISTINCT FROM TRUE",
        "v_approval.approval_status <> 'approved'",
        "v_approval.expires_at <= v_now",
        "v_approval.job_created IS DISTINCT FROM TRUE",
        "v_approval.approved_payload_hash IS DISTINCT FROM v_job.approved_payload_hash",
        "model_artifact_sha256') IS DISTINCT FROM v_job.artifact_sha256",
        "evaluated_commit_sha')",
        "v_approval.dry_run_payload ->> 'git_commit'",
    ):
        assert fragment in body


def test_registration_revalidates_sanitized_accepted_report_shape() -> None:
    body = _function("register_model_retrain_accepted_evaluation")
    for fragment in (
        "model-acceptance-gate-report",
        "all-approved-thresholds-pass",
        "p_sanitized_report -> 'accepted') IS DISTINCT FROM 'true'::JSONB",
        "p_sanitized_report -> 'acceptance_required') IS DISTINCT FROM 'true'::JSONB",
        "p_sanitized_report -> 'blockers') IS DISTINCT FROM '[]'::JSONB",
        "p_sanitized_report -> 'failure_codes') IS DISTINCT FROM '[]'::JSONB",
        "contract_approved",
        "metrics_against_thresholds",
        "promotion_policy",
        "v_observed_at < v_now - INTERVAL '7 days'",
        "extensions.digest(convert_to(p_sanitized_report::TEXT, 'UTF8'), 'sha256')",
    ):
        assert fragment in body
    assert "jsonb_typeof(v_contract) IS DISTINCT FROM 'object'" in body
    assert "jsonb_typeof(v_evidence) IS DISTINCT FROM 'object'" in body
    assert "v_observed_at IS NULL" in body


def test_evaluation_transition_cannot_be_used_as_activation() -> None:
    body = _function("register_model_retrain_accepted_evaluation").lower()
    assert "job_state = 'evaluation-recorded'" in body
    assert "acceptance_passed = true" in body
    assert "promotion_eligible = false" in body
    assert "trusted_promotion_evidence" in body
    assert "is_active" not in body
    assert "active_model" not in body
    assert "model_metadata" not in body


def test_bootstrap_executes_non_promoting_evaluation_registration() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [
        item for item in manifest["migrations"]
        if item["path"] == "supabase/migrations/20260802_model_retrain_evaluation_registration.sql"
    ]
    assert len(entries) == 1
    assert entries[0]["version"] == "20260802144000"
    contract = CONTRACT.read_text(encoding="utf-8")
    for fragment in (
        "FROM public.register_model_retrain_accepted_evaluation(",
        "phase3m model retrain evaluation registration contract failed",
        "phase3m model evaluation JSON null bypass was accepted",
        "trusted_promotion_evidence = FALSE",
        "promotion_eligible = FALSE",
        "phase3m_check:model_retrain_evaluation_registration",
    ):
        assert fragment in contract

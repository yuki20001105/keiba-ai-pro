from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "20260802_model_retrain_approval_ledger.sql"
LEDGER = ROOT / "src" / "lib" / "model-retrain-approval-ledger.ts"
CREATE_ROUTE = ROOT / "src" / "app" / "api" / "model-redesign" / "approval" / "route.ts"
GET_ROUTE = ROOT / "src" / "app" / "api" / "model-redesign" / "approval" / "[approval_id]" / "route.ts"
DECISION_ROUTE = (
    ROOT
    / "src"
    / "app"
    / "api"
    / "model-redesign"
    / "approval"
    / "[approval_id]"
    / "decision"
    / "route.ts"
)
MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
BOOTSTRAP_CONTRACT = ROOT / "supabase" / "bootstrap" / "v1" / "tests" / "bootstrap_contract.sql"
BOOTSTRAP_RUNNER = ROOT / "scripts" / "security" / "run_phase3m_supabase_bootstrap_gate.py"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _function(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION public\.{re.escape(name)}\b(?P<body>.*?)\n\$\$;",
        sql,
        flags=re.DOTALL,
    )
    assert match, f"missing function: {name}"
    return match.group("body")


def test_ledger_tables_are_private_authoritative_and_non_executing() -> None:
    sql = _sql()
    assert "CREATE TABLE IF NOT EXISTS public.model_retrain_approval_requests" in sql
    assert "CREATE TABLE IF NOT EXISTS public.model_retrain_approval_events" in sql
    assert "authoritative_record BOOLEAN NOT NULL DEFAULT TRUE" in sql
    assert "execution_enabled BOOLEAN NOT NULL DEFAULT FALSE" in sql
    assert "job_created BOOLEAN NOT NULL DEFAULT FALSE" in sql
    assert "CHECK (execution_enabled = FALSE)" in sql
    assert "CHECK (job_created = FALSE)" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert re.search(
        r"REVOKE ALL ON TABLE public\.model_retrain_approval_requests\s+"
        r"FROM PUBLIC, anon, authenticated, service_role;",
        sql,
    )
    assert re.search(
        r"REVOKE ALL ON TABLE public\.model_retrain_approval_events\s+"
        r"FROM PUBLIC, anon, authenticated, service_role;",
        sql,
    )
    assert "GRANT SELECT ON TABLE public.model_retrain_approval_requests TO service_role" in sql


def test_mutations_are_rpc_only_and_events_are_append_only() -> None:
    sql = _sql()
    assert "model retrain approval events are append-only" in sql
    assert "BEFORE UPDATE OR DELETE ON public.model_retrain_approval_events" in sql
    assert "model retrain approval binding is immutable" in sql
    assert "model retrain approval update violates safety state" in sql
    assert "BEFORE UPDATE ON public.model_retrain_approval_requests" in sql
    for name, signature in (
        (
            "create_model_retrain_approval",
            "public.create_model_retrain_approval(UUID, UUID, TEXT, JSONB, TEXT, TEXT[])",
        ),
        ("get_model_retrain_approval", "public.get_model_retrain_approval(UUID, UUID)"),
        (
            "transition_model_retrain_approval",
            "public.transition_model_retrain_approval(UUID, UUID, INTEGER, TEXT, TEXT)",
        ),
    ):
        body = _function(sql, name)
        assert "SECURITY DEFINER" in body
        assert "SET search_path = public" in body
        assert f"REVOKE ALL ON FUNCTION {signature}" in sql
        assert f"GRANT EXECUTE ON FUNCTION {signature}" in sql


def test_create_is_actor_bound_idempotent_and_fixed_expiry() -> None:
    body = _function(_sql(), "create_model_retrain_approval")
    assert "PERFORM public._model_retrain_require_admin(p_actor_user_id)" in body
    assert "p_dry_run_payload->>'created_by' IS DISTINCT FROM p_actor_user_id::TEXT" in body
    assert "p_dry_run_payload->>'state' IS DISTINCT FROM 'preview-ready'" in body
    assert "p_dry_run_payload->>'dry_run_id' IS DISTINCT FROM p_dry_run_id::TEXT" in body
    assert "pg_advisory_xact_lock" in body
    assert "dry run approval payload conflict" in body
    assert "v_now + INTERVAL '30 minutes'" in body
    assert "v_generated_at < v_now - INTERVAL '24 hours'" in body
    assert "v_generated_at > v_now + INTERVAL '5 minutes'" in body
    assert "'pending', 1, TRUE, FALSE, FALSE" in body


def test_create_parenthesizes_allowed_action_case_sum_for_plpgsql() -> None:
    body = _function(_sql(), "create_model_retrain_approval")
    assert re.search(
        r"cardinality\(p_allowed_actions\)\s*<>\s*\(\s*"
        r"CASE WHEN 'submit_approved_retrain' = ANY \(p_allowed_actions\) "
        r"THEN 1 ELSE 0 END\s*\+\s*"
        r"CASE WHEN 'view_approval_status' = ANY \(p_allowed_actions\) "
        r"THEN 1 ELSE 0 END\s*\+\s*"
        r"CASE WHEN 'view_job_status' = ANY \(p_allowed_actions\) "
        r"THEN 1 ELSE 0 END\s*\)",
        body,
    )


def test_transition_requires_cas_and_independent_reviewer() -> None:
    body = _function(_sql(), "transition_model_retrain_approval")
    assert "v_row.record_version <> p_expected_version" in body
    assert "model retrain approval version conflict" in body
    assert "p_actor_user_id = v_row.requested_by" in body
    assert "requester cannot decide own approval" in body
    assert "only requester can revoke approval" in body
    assert "approval_status <> 'pending'" in body
    assert "execution_enabled = FALSE" in body
    assert "job_created = a.job_created" in body


def test_next_routes_use_admin_auth_and_only_the_bounded_rpc_adapter() -> None:
    for route in (CREATE_ROUTE, GET_ROUTE, DECISION_ROUTE):
        text = route.read_text(encoding="utf-8")
        assert "verifyRequestAuth(request, { requireAdmin: true })" in text
        assert "createSupabaseServiceClient()" in text
        assert ".from(" not in text
        assert "Cache-Control': 'no-store" in text

    ledger = LEDGER.read_text(encoding="utf-8")
    assert "MODEL_RETRAIN_APPROVAL_BODY_LIMIT_BYTES = 256 * 1024" in ledger
    assert "canonicalRetrainPayloadHash(input.dry_run_payload)" in ledger
    assert "value.execution_enabled !== false" in ledger
    assert "typeof value.job_created !== 'boolean'" in ledger
    assert "requester does not match dry-run creator" in ledger


def test_ledger_contains_no_job_artifact_or_pointer_write_primitive() -> None:
    ledger = LEDGER.read_text(encoding="utf-8")
    routes = "\n".join(
        path.read_text(encoding="utf-8") for path in (CREATE_ROUTE, GET_ROUTE, DECISION_ROUTE)
    )
    combined = f"{ledger}\n{routes}"
    assert "joblib.dump" not in combined
    assert ".active_model.json" not in combined
    assert "writeFile" not in combined
    assert "spawn(" not in combined
    assert "exec(" not in combined
    assert "fetch(" not in combined


def test_phase3m_bootstrap_includes_and_exercises_the_ledger() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [
        item for item in manifest["migrations"]
        if item["path"] == "supabase/migrations/20260802_model_retrain_approval_ledger.sql"
    ]
    assert len(entries) == 1
    assert entries[0]["version"] == "20260802140000"
    assert [item["version"] for item in manifest["migrations"]] == sorted(
        item["version"] for item in manifest["migrations"]
    )

    contract = BOOTSTRAP_CONTRACT.read_text(encoding="utf-8")
    assert "phase3m_model_retrain_approval" in contract
    assert "phase3m model retrain self approval was not denied" in contract
    assert "phase3m model approval transition contract failed" in contract
    assert "phase3m model approval immutable binding changed" in contract
    assert "v_execution_enabled IS DISTINCT FROM FALSE" in contract
    assert "v_job_created IS DISTINCT FROM FALSE" in contract

    runner = BOOTSTRAP_RUNNER.read_text(encoding="utf-8")
    assert "'model_retrain_approval_requests'" in runner
    assert "'create_model_retrain_approval'" in runner

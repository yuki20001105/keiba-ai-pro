from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "20260802_model_retrain_job_ledger.sql"
JOB_LEDGER = ROOT / "src" / "lib" / "model-retrain-job-ledger.ts"
CREATE_ROUTE = ROOT / "src" / "app" / "api" / "model-redesign" / "jobs" / "route.ts"
GET_ROUTE = ROOT / "src" / "app" / "api" / "model-redesign" / "jobs" / "[job_id]" / "route.ts"
MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
BOOTSTRAP_CONTRACT = ROOT / "supabase" / "bootstrap" / "v1" / "tests" / "bootstrap_contract.sql"


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


def test_job_and_event_tables_are_private_immutable_and_non_executing() -> None:
    sql = _sql()
    assert "CREATE TABLE IF NOT EXISTS public.model_retrain_jobs" in sql
    assert "CREATE TABLE IF NOT EXISTS public.model_retrain_job_events" in sql
    assert "approval_id UUID NOT NULL UNIQUE" in sql
    assert "job_state TEXT NOT NULL DEFAULT 'queued' CHECK (job_state = 'queued')" in sql
    assert "execution_started BOOLEAN NOT NULL DEFAULT FALSE CHECK (execution_started = FALSE)" in sql
    assert "artifact_written BOOLEAN NOT NULL DEFAULT FALSE CHECK (artifact_written = FALSE)" in sql
    assert "BEFORE UPDATE OR DELETE ON public.model_retrain_jobs" in sql
    assert "BEFORE UPDATE OR DELETE ON public.model_retrain_job_events" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert re.search(r"REVOKE ALL ON TABLE public\.model_retrain_jobs\s+FROM PUBLIC, anon, authenticated, service_role;", sql)


def test_atomic_create_requires_approved_actor_bound_cas_and_is_idempotent() -> None:
    body = _function(_sql(), "create_model_retrain_job")
    for fragment in (
        "PERFORM public._model_retrain_require_admin(p_actor_user_id)",
        "pg_advisory_xact_lock",
        "v_approval.record_version <> p_expected_approval_version",
        "v_approval.approval_status <> 'approved'",
        "v_approval.expires_at <= clock_timestamp()",
        "v_approval.requested_by IS DISTINCT FROM p_actor_user_id",
        "v_approval.approved_by = v_approval.requested_by",
        "v_approval.approved_payload_hash IS DISTINCT FROM p_approved_payload_hash",
        "'submit_approved_retrain' = ANY (v_approval.allowed_actions)",
        "INSERT INTO public.model_retrain_jobs",
        "SET job_created = TRUE",
        "'job-created'",
    ):
        assert fragment in body
    assert body.index("SELECT * INTO v_existing") < body.index("PERFORM public._expire_model_retrain_approval_if_needed")


def test_rpc_and_next_boundaries_are_service_only_admin_and_non_executing() -> None:
    sql = _sql()
    for signature in (
        "public.create_model_retrain_job(UUID, UUID, INTEGER, TEXT)",
        "public.get_model_retrain_job(UUID, UUID)",
    ):
        assert f"REVOKE ALL ON FUNCTION {signature}" in sql
        assert f"GRANT EXECUTE ON FUNCTION {signature}" in sql
    for route in (CREATE_ROUTE, GET_ROUTE):
        text = route.read_text(encoding="utf-8")
        assert "verifyRequestAuth(request, { requireAdmin: true })" in text
        assert "createSupabaseServiceClient()" in text
        assert "Cache-Control': 'no-store" in text
        assert ".from(" not in text
    combined = JOB_LEDGER.read_text(encoding="utf-8") + CREATE_ROUTE.read_text(encoding="utf-8")
    for primitive in ("joblib.dump", ".active_model.json", "writeFile", "spawn(", "exec(", "fetch("):
        assert primitive not in combined


def test_canonical_bootstrap_includes_and_exercises_job_submission() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [item for item in manifest["migrations"] if item["path"] == "supabase/migrations/20260802_model_retrain_job_ledger.sql"]
    assert len(entries) == 1
    assert entries[0]["version"] == "20260802141000"
    contract = BOOTSTRAP_CONTRACT.read_text(encoding="utf-8")
    for fragment in (
        "phase3m service role directly inserted model retrain job",
        "phase3m model retrain queued job contract failed",
        "phase3m model retrain job idempotency contract failed",
        "phase3m_check:model_retrain_job_ledger",
    ):
        assert fragment in contract

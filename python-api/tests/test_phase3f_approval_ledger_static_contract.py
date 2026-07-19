from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "20260718_scrape_uncertainty_review_ledger.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_phase3f_ledger_tables_are_server_only_and_events_are_immutable() -> None:
    sql = _sql()

    assert "CREATE TABLE IF NOT EXISTS public.scrape_uncertainty_review_requests" in sql
    assert "CREATE TABLE IF NOT EXISTS public.scrape_uncertainty_review_events" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY" not in sql
    assert re.search(
        r"REVOKE ALL ON TABLE public\.scrape_uncertainty_review_requests\s+FROM PUBLIC, anon, authenticated, service_role;",
        sql,
    )
    assert re.search(
        r"REVOKE ALL ON TABLE public\.scrape_uncertainty_review_events\s+FROM PUBLIC, anon, authenticated, service_role;",
        sql,
    )
    assert "GRANT SELECT ON TABLE public.scrape_uncertainty_review_requests TO service_role;" in sql
    assert "GRANT SELECT ON TABLE public.scrape_uncertainty_review_events TO service_role;" in sql
    assert "BEFORE UPDATE OR DELETE ON public.scrape_uncertainty_review_events" in sql
    assert "scrape uncertainty review events are append-only" in sql
    assert re.search(
        r"REVOKE UPDATE ON TABLE public\.profiles FROM PUBLIC, anon, authenticated;",
        sql,
    )
    assert re.search(
        r"GRANT UPDATE \(full_name, updated_at\) ON TABLE public\.profiles TO authenticated;",
        sql,
    )


def test_phase3f_rpc_surface_is_service_role_only_and_has_no_execution_rpc() -> None:
    sql = _sql()
    rpc_names = {
        "create_scrape_uncertainty_review",
        "get_scrape_uncertainty_review",
        "list_scrape_uncertainty_reviews",
        "transition_scrape_uncertainty_review",
    }

    for rpc in rpc_names:
        assert f"CREATE OR REPLACE FUNCTION public.{rpc}" in sql
        assert re.search(
            rf"REVOKE ALL ON FUNCTION public\.{rpc}\([\s\S]*?\)\s+FROM PUBLIC, anon, authenticated, service_role;",
            sql,
        )
        assert re.search(
            rf"GRANT EXECUTE ON FUNCTION public\.{rpc}\([\s\S]*?\)\s+TO service_role;",
            sql,
        )

    created_public_functions = set(
        re.findall(r"CREATE OR REPLACE FUNCTION public\.([a-z0-9_]+)\s*\(", sql, flags=re.IGNORECASE)
    )
    assert not any(
        token in name
        for name in created_public_functions
        for token in ("unlock", "execute", "consume")
    )
    function_blocks = re.findall(
        r"CREATE OR REPLACE FUNCTION public\.[a-z0-9_]+\s*\([\s\S]*?\$\$;",
        sql,
        flags=re.IGNORECASE,
    )
    assert len(function_blocks) == 8
    assert sum("SECURITY DEFINER" in block for block in function_blocks) == 7
    assert all("SET search_path = public" in block for block in function_blocks)


def test_phase3f_create_is_canonical_idempotent_and_non_executable() -> None:
    sql = _sql()

    assert "extensions.digest" in sql
    assert "'sha256'" in sql
    assert re.search(
        r"CREATE OR REPLACE FUNCTION public\._scrape_uncertainty_payload_hash\([\s\S]*?\)\s*RETURNS TEXT\s*LANGUAGE sql\s*STABLE\s*STRICT",
        sql,
    )
    assert "regexp_replace(btrim(p_reason)" in sql
    assert "CASE WHEN p_server_state_unverified THEN '1' ELSE '0' END" in sql
    assert "CASE WHEN p_no_unlock_or_retry THEN '1' ELSE '0' END" in sql
    assert "UNIQUE (owner_user_id, client_request_id)" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "client_request_id payload conflict" in sql
    assert "active review already exists for payload" in sql
    assert "UNIQUE INDEX IF NOT EXISTS uq_scrape_uncertainty_active_payload" in sql
    assert "status IN ('pending_review', 'approved')" in sql
    assert "p_server_state_unverified IS DISTINCT FROM TRUE" in sql
    assert "p_no_unlock_or_retry IS DISTINCT FROM TRUE" in sql
    assert "p_failure_kind IS NULL" in sql
    assert "p_start_period IS NULL" in sql
    assert "p_end_period IS NULL" in sql
    assert "v_hash IS NULL" in sql
    assert "v_existing.request_payload_hash IS DISTINCT FROM v_hash" in sql

    assert "approval_scope TEXT NOT NULL DEFAULT 'review_only' CHECK (approval_scope = 'review_only')" in sql
    assert "authoritative BOOLEAN NOT NULL DEFAULT TRUE CHECK (authoritative = TRUE)" in sql
    assert "execution_enabled BOOLEAN NOT NULL DEFAULT FALSE CHECK (execution_enabled = FALSE)" in sql
    assert "lock_release_allowed BOOLEAN NOT NULL DEFAULT FALSE CHECK (lock_release_allowed = FALSE)" in sql
    assert "status IN ('approved', 'rejected', 'revoked')" in sql
    assert "decided_by IS NOT NULL" in sql
    assert "decided_at IS NOT NULL" in sql
    assert "decision_reason IS NOT NULL" in sql


def test_phase3f_transition_uses_row_lock_cas_two_person_rule_and_requester_revoke() -> None:
    sql = _sql()

    transition_start = sql.index("CREATE OR REPLACE FUNCTION public.transition_scrape_uncertainty_review")
    transition_end = sql.index("-- Internal helpers", transition_start)
    transition = sql[transition_start:transition_end]

    assert "FOR UPDATE" in transition
    assert "v_row.version <> p_expected_version" in transition
    assert "review version conflict" in transition
    assert "p_action IN ('approve', 'reject')" in transition
    assert "p_action IS NULL" in transition
    assert "p_actor_user_id = v_row.owner_user_id" in transition
    assert "requester cannot approve or reject own review" in transition
    assert "p_actor_user_id <> v_row.owner_user_id" in transition
    assert "only requester can revoke review" in transition
    assert "v_row.decided_by IS DISTINCT FROM p_actor_user_id" in transition
    assert "RAISE EXCEPTION 'review not found' USING ERRCODE = 'P0002'" in transition
    assert "v_row.status NOT IN ('pending_review', 'approved')" in transition
    assert "execution_enabled = FALSE" in transition
    assert "lock_release_allowed = FALSE" in transition


def test_phase3f_expiry_is_atomic_and_audited() -> None:
    sql = _sql()

    expiry_start = sql.index("CREATE OR REPLACE FUNCTION public._expire_scrape_uncertainty_review_if_needed")
    expiry_end = sql.index("CREATE OR REPLACE FUNCTION public.create_scrape_uncertainty_review", expiry_start)
    expiry = sql[expiry_start:expiry_end]

    assert "FOR UPDATE" in expiry
    assert "v_row.expires_at <= clock_timestamp()" in expiry
    assert "SET status = 'expired'" in expiry
    assert "'expired', NULL" in expiry
    assert "INSERT INTO public.scrape_uncertainty_review_events" in expiry


def test_phase3f_reviewable_list_never_projects_expired_pending_rows() -> None:
    sql = _sql()

    list_start = sql.index("CREATE OR REPLACE FUNCTION public.list_scrape_uncertainty_reviews")
    list_end = sql.index("CREATE OR REPLACE FUNCTION public.transition_scrape_uncertainty_review", list_start)
    list_rpc = sql[list_start:list_end]

    assert "p_scope IS NULL" in list_rpc
    assert "r.status = 'pending_review'" in list_rpc
    assert "r.expires_at > clock_timestamp()" in list_rpc


def test_phase3f_projection_is_strict_snake_case_and_hides_owner() -> None:
    sql = _sql()
    required_columns = (
        "review_id UUID",
        "client_request_id UUID",
        "request_payload_hash TEXT",
        "uncertainty_occurred_at TIMESTAMPTZ",
        "approval_scope TEXT",
        "authoritative BOOLEAN",
        "execution_enabled BOOLEAN",
        "lock_release_allowed BOOLEAN",
    )
    for column in required_columns:
        assert sql.count(column) >= 5

    # owner_user_id is a durable binding and RPC input, never a returned column.
    return_blocks = re.findall(r"RETURNS TABLE \(([\s\S]*?)\)\s*LANGUAGE", sql)
    assert len(return_blocks) == 4
    assert all("owner_user_id" not in block for block in return_blocks)

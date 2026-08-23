from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "20260720_scrape_execution_reservation.sql"
BOOTSTRAP = ROOT / "supabase" / "tests" / "phase3j_execution_reservation_bootstrap.sql"
RUNTIME = ROOT / "supabase" / "tests" / "phase3j_execution_reservation_runtime_contract.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(sql: str, name: str) -> str:
    pattern = re.compile(
        rf"CREATE OR REPLACE FUNCTION public\.{re.escape(name)}\s*\([\s\S]*?\$\$;",
        re.IGNORECASE,
    )
    match = pattern.search(sql)
    assert match is not None, f"missing function: {name}"
    return match.group(0)


def test_phase3j_contract_files_are_repository_owned_and_nonempty() -> None:
    for path in (MIGRATION, BOOTSTRAP, RUNTIME):
        assert path.is_file()
        assert len(_read(path)) > 500


def test_authorization_is_explicit_bootstrap_only_and_two_person() -> None:
    sql = _read(MIGRATION)
    trigger = _function(sql, "_validate_scrape_execution_authorization_insert")
    public_functions = set(
        re.findall(
            r"CREATE OR REPLACE FUNCTION public\.([a-z0-9_]+)\s*\(",
            sql,
            flags=re.IGNORECASE,
        )
    )

    assert "CREATE TABLE IF NOT EXISTS public.scrape_execution_authorizations" in sql
    assert "authorization_source TEXT NOT NULL DEFAULT 'ci_bootstrap_only'" in sql
    assert "authorization_status TEXT NOT NULL DEFAULT 'authorized'" in sql
    assert "execution_authorized BOOLEAN NOT NULL DEFAULT TRUE" in sql
    assert "CHECK (authorized_by_user_id <> owner_user_id)" in sql
    assert "UNIQUE (review_id, review_version)" in sql
    assert "requester cannot self-authorize execution" in trigger
    assert "v_review.status <> 'approved'" in trigger
    assert "v_review.execution_enabled IS DISTINCT FROM FALSE" in trigger
    assert "v_review.lock_release_allowed IS DISTINCT FROM FALSE" in trigger
    assert "v_review.decided_by IS DISTINCT FROM NEW.authorized_by_user_id" in trigger
    assert "NEW.authorization_expires_at > v_review.expires_at" in trigger
    assert "CREATE TRIGGER trg_scrape_execution_authorizations_immutable" in sql
    assert "scrape execution authorizations are immutable" in sql
    assert not any(
        token in name
        for name in public_functions
        for token in (
            "create_scrape_execution_authorization",
            "update_scrape_execution_authorization",
            "revoke_scrape_execution_authorization",
        )
    )


def test_reservation_binds_the_complete_execution_tuple() -> None:
    sql = _read(MIGRATION)
    reserve = _function(sql, "reserve_scrape_execution")
    required_columns = (
        "authorization_id UUID NOT NULL UNIQUE",
        "operation_id UUID NOT NULL UNIQUE",
        "job_id UUID NOT NULL UNIQUE",
        "review_id UUID NOT NULL",
        "review_version INTEGER NOT NULL",
        "owner_user_id UUID NOT NULL",
        "execution_request_hash TEXT NOT NULL",
        "authorization_binding_hash TEXT NOT NULL",
        "fencing_token BIGINT NOT NULL UNIQUE",
    )
    for column in required_columns:
        assert column in sql

    assert "REFERENCES public.scrape_uncertainty_review_requests(review_id) ON DELETE RESTRICT" in sql
    assert "owner_user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT" in sql

    assert "p_operation_id, p_job_id, p_review_id, p_review_version" in reserve
    assert "p_owner_user_id, p_execution_request_hash" in reserve
    assert "v_authorization.authorization_binding_hash <> v_binding_hash" in reserve
    assert "v_review.status <> 'approved'" in reserve
    assert "v_review.decided_by IS DISTINCT FROM v_authorization.authorized_by_user_id" in reserve
    assert "explicit execution authorization not found" in reserve
    assert "approved review no longer matches authorization" in reserve


def test_reserve_is_exact_idempotent_conflict_safe_and_serialized() -> None:
    sql = _read(MIGRATION)
    reserve = _function(sql, "reserve_scrape_execution")

    assert "pg_advisory_xact_lock" in reserve
    assert "FOR UPDATE" in reserve
    assert "v_existing.requested_ttl_seconds <> p_ttl_seconds" in reserve
    assert "reservation id payload conflict" in reserve
    assert "authorization or execution binding already reserved" in reserve
    assert "authorization version conflict" in reserve
    assert "nextval('public.scrape_execution_reservation_fencing_seq'::regclass)" in reserve
    assert "LEAST(" in reserve
    assert "p_ttl_seconds NOT BETWEEN 1 AND 300" in reserve


def test_consume_is_cas_guarded_single_use_and_receipt_idempotent() -> None:
    sql = _read(MIGRATION)
    consume = _function(sql, "consume_scrape_execution_reservation")

    assert "FOR UPDATE" in consume
    assert "v_row.version <> p_expected_version" in consume
    assert "reservation version conflict" in consume
    assert "IF v_row.status = 'consumed'" in consume
    assert "v_row.consume_request_id <> p_consume_request_id" in consume
    assert "reservation already consumed by another request" in consume
    assert "consume payload binding conflict" in consume
    assert "scrape-consume-receipt-v1" in consume
    assert "consume_receipt_hash = v_receipt" in consume
    assert "status = 'consumed', version = r.version + 1" in consume
    assert "authorization is no longer consumable" in consume


def test_release_and_expiry_are_terminal_cas_transitions() -> None:
    sql = _read(MIGRATION)
    release = _function(sql, "release_scrape_execution_reservation")
    expiry = _function(sql, "expire_scrape_execution_reservation")
    materialize = _function(sql, "_materialize_scrape_execution_reservation_expiry")

    assert "IF v_row.status = 'released'" in release
    assert "v_row.release_request_id <> p_release_request_id" in release
    assert "v_row.version <> p_expected_version" in release
    assert "status = 'released', version = r.version + 1" in release
    assert "release payload binding conflict" in release
    assert "v_row.version <> p_expected_version" in expiry
    assert "reservation has not expired" in expiry
    assert "v_row.status = 'expired'" in expiry
    assert "status = 'expired'" in materialize
    assert "version = r.version + 1" in materialize
    assert "INSERT INTO public.scrape_execution_reservation_events" in materialize


def test_event_log_is_append_only_and_exactly_tracks_lifecycle() -> None:
    sql = _read(MIGRATION)

    assert "CREATE TABLE IF NOT EXISTS public.scrape_execution_reservation_events" in sql
    assert "event_type IN ('reserved', 'consumed', 'released', 'expired')" in sql
    assert "UNIQUE (reservation_id, event_seq)" in sql
    assert "CREATE TRIGGER trg_scrape_execution_reservation_events_immutable" in sql
    assert "BEFORE UPDATE OR DELETE ON public.scrape_execution_reservation_events" in sql
    assert "scrape execution reservation events are append-only" in sql
    for event in ("'reserved'", "'consumed'", "'released'", "'expired'"):
        assert sql.count(event) >= 3


def test_tables_are_rls_server_read_only_and_sequences_are_private() -> None:
    sql = _read(MIGRATION)
    tables = (
        "scrape_execution_authorizations",
        "scrape_execution_reservations",
        "scrape_execution_reservation_events",
    )
    for table in tables:
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;" in sql
        assert re.search(
            rf"REVOKE ALL ON TABLE public\.{table}\s+FROM PUBLIC, anon, authenticated, service_role;",
            sql,
        )
        assert f"GRANT SELECT ON TABLE public.{table} TO service_role;" in sql
    assert "CREATE POLICY" not in sql
    assert re.search(
        r"REVOKE ALL ON SEQUENCE public\.scrape_execution_reservation_fencing_seq\s+FROM PUBLIC, anon, authenticated, service_role;",
        sql,
    )


def test_reservation_rpc_surface_is_service_role_only_with_fixed_search_path() -> None:
    sql = _read(MIGRATION)
    signatures = {
        "reserve_scrape_execution": "UUID, UUID, UUID, UUID, UUID, INTEGER, UUID, TEXT, INTEGER, INTEGER",
        "consume_scrape_execution_reservation": "UUID, INTEGER, UUID, UUID, UUID, UUID, INTEGER, UUID, TEXT",
        "release_scrape_execution_reservation": "UUID, INTEGER, UUID, UUID, UUID, TEXT, TEXT",
        "expire_scrape_execution_reservation": "UUID, INTEGER",
    }
    for name, signature in signatures.items():
        block = _function(sql, name)
        assert "SECURITY DEFINER" in block
        assert "SET search_path = public" in block
        assert re.search(
            rf"REVOKE ALL ON FUNCTION public\.{name}\({re.escape(signature)}\)\s+FROM PUBLIC, anon, authenticated, service_role;",
            sql,
        )
        assert re.search(
            rf"GRANT EXECUTE ON FUNCTION public\.{name}\({re.escape(signature)}\)\s+TO service_role;",
            sql,
        )


def test_contract_contains_no_unlock_dispatch_or_review_mutation_path() -> None:
    sql = _read(MIGRATION)
    public_function_names = set(
        re.findall(
            r"CREATE OR REPLACE FUNCTION public\.([a-z0-9_]+)\s*\(",
            sql,
            flags=re.IGNORECASE,
        )
    )
    assert not any("unlock" in name or "dispatch" in name for name in public_function_names)
    assert not re.search(
        r"UPDATE\s+public\.scrape_uncertainty_review_requests",
        sql,
        flags=re.IGNORECASE,
    )
    assert not re.search(
        r"DELETE\s+FROM\s+public\.scrape_uncertainty_review_requests",
        sql,
        flags=re.IGNORECASE,
    )


def test_bootstrap_uses_direct_authorization_insert_after_independent_reviews() -> None:
    sql = _read(BOOTSTRAP)

    assert sql.count("public.create_scrape_uncertainty_review(") == 6
    assert sql.count("public.transition_scrape_uncertainty_review(") == 6
    assert "INSERT INTO public.scrape_execution_authorizations" in sql
    assert "authorized_by_user_id" in sql
    assert "review.decided_by" in sql
    assert "LEAST(clock_timestamp() + interval '10 minutes', review.expires_at)" in sql
    assert "create_scrape_execution_authorization" not in sql
    assert "reserve_scrape_execution" not in sql


def test_runtime_contract_has_frozen_catalog_and_behavior_markers() -> None:
    sql = _read(RUNTIME)
    markers = re.findall(r"SELECT 'phase3j_check:([a-z0-9_]+)';", sql)
    assert markers == [
        "authorization_table_present",
        "reservation_table_present",
        "event_table_present",
        "rls_enabled",
        "no_browser_policies",
        "server_read_only_tables",
        "reservation_rpc_signatures",
        "rpc_security_definer",
        "rpc_search_path_fixed",
        "append_only_events",
        "authorization_bootstrap_only",
        "postgres_approved_review_only_denied",
        "postgres_reservation_replay",
        "postgres_consume_cas",
        "postgres_release_replay",
        "postgres_expiry_fencing",
    ]


def test_runtime_contract_exercises_conflicts_receipt_expiry_fencing_and_grants() -> None:
    sql = _read(RUNTIME)

    required_evidence = (
        "approved review without explicit authorization is denied",
        "requester self-authorization is denied",
        "same reservation id with different payload conflicts",
        "same authorization with different reservation id conflicts",
        "dblink_send_query",
        "concurrent reserve replay serialized to one row and one event",
        "stale consume CAS is rejected",
        "second consume identifier is rejected",
        "count(DISTINCT consume_receipt_hash) = 1",
        "stale release CAS is rejected",
        "released reservation cannot be consumed",
        "fencing_token > earlier.fencing_token",
        "reservation event update is rejected",
        "authorization update is rejected",
        "service role cannot directly create authorization",
        "service role cannot directly create reservation",
    )
    for evidence in required_evidence:
        assert evidence in sql
    assert "SET ROLE authenticated" not in sql or "42501" in sql
    assert "UPDATE public.scrape_uncertainty_review_requests" not in sql

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "bootstrap" / "v1" / "migrations"

CORE = MIGRATIONS / "20260720141000_core_identity_finance.sql"
QUOTA = MIGRATIONS / "20260720141100_prediction_quota.sql"
PURCHASE = MIGRATIONS / "20260720141200_purchase_history.sql"
HARDENING = MIGRATIONS / "20260720143200_security_definer_search_path.sql"
OCR_QUOTA = MIGRATIONS / "20260720143300_ocr_quota_reservation.sql"


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return re.sub(r"\s+", " ", _sql(path)).lower()


def test_core_bootstrap_versions_are_unique_and_supabase_compatible() -> None:
    expected = {CORE, QUOTA, PURCHASE, HARDENING, OCR_QUOTA}
    assert all(path.is_file() for path in expected)

    names = [path.name for path in MIGRATIONS.glob("*.sql")]
    versions = [name.split("_", 1)[0] for name in names]
    assert len(versions) == len(set(versions))
    assert all(re.fullmatch(r"\d{14}", version) for version in versions)


def test_core_tables_have_expected_ownership_shape_and_bank_uniqueness() -> None:
    sql = _normalized(CORE)
    for table in ("profiles", "predictions", "bets", "bank_records", "ocr_usage"):
        assert f"create table public.{table}" in sql
        assert f"alter table public.{table} enable row level security" in sql
        assert f"alter table public.{table} force row level security" in sql
        assert f"revoke all on table public.{table} from public, anon, authenticated" in sql

    assert "user_id uuid not null unique references public.profiles(id)" in sql
    assert "constraint predictions_id_user_key unique (id, user_id)" in sql
    assert "foreign key (prediction_id, user_id) references public.predictions(id, user_id)" in sql
    assert "on delete set null (prediction_id)" in sql
    assert "role text not null default 'user' check (role in ('user', 'admin'))" in sql
    assert "subscription_tier text not null default 'free'" in sql


def test_profile_grants_prevent_self_promotion() -> None:
    core = _normalized(CORE)
    hardening = _normalized(HARDENING)

    for sql in (core, hardening):
        assert "grant update (full_name) on public.profiles to authenticated" in sql
        assert "grant update on public.profiles to authenticated" not in sql
        assert "grant all" not in sql

    # The signup trigger may consume a display name, but never privilege claims.
    trigger_body = core.split("create function public.phase3m_handle_new_user()", 1)[1]
    assert "raw_user_meta_data ->> 'full_name'" in trigger_body
    assert "raw_user_meta_data ->> 'role'" not in trigger_body
    assert "raw_user_meta_data ->> 'subscription_tier'" not in trigger_body
    assert "when others" not in trigger_body


def test_owner_update_policies_have_using_and_with_check_guards() -> None:
    core = _normalized(CORE)
    purchase = _normalized(PURCHASE)

    for policy in (
        "profiles_update_own",
        "predictions_update_own",
        "bets_update_own",
        "bank_records_update_own",
    ):
        policy_sql = core.split(f"create policy {policy}", 1)[1].split(";", 1)[0]
        assert " using " in policy_sql
        assert " with check " in policy_sql

    policy_sql = purchase.split("create policy purchase_history_update_own", 1)[1].split(";", 1)[0]
    assert " using " in policy_sql
    assert " with check " in policy_sql


def test_quota_rpcs_are_atomic_bounded_and_service_role_only() -> None:
    sql = _normalized(QUOTA)

    assert "check (pred_count_remaining = -1 or pred_count_remaining >= 0)" in sql
    assert "create function public.reset_pred_count_if_needed(p_user_id uuid)" in sql
    assert "create function public.consume_pred_count_batch(p_user_id uuid, p_units integer)" in sql
    assert "create function public.consume_pred_count(p_user_id uuid)" in sql
    assert sql.count("security definer") == 3
    assert sql.count("set search_path = pg_catalog, public") == 3
    assert "for update" in sql
    assert "p_units is null or p_units < 1 or p_units > 100" in sql
    assert "pred_count_remaining = p.pred_count_remaining - p_units" in sql

    for signature in (
        "reset_pred_count_if_needed(uuid)",
        "consume_pred_count_batch(uuid, integer)",
        "consume_pred_count(uuid)",
    ):
        assert f"revoke all on function public.{signature} from public, anon, authenticated" in sql
        assert f"grant execute on function public.{signature} to service_role" in sql
        assert f"grant execute on function public.{signature} to authenticated" not in sql
        assert f"grant execute on function public.{signature} to anon" not in sql


def test_ocr_quota_rpc_is_atomic_strict_and_service_role_only() -> None:
    sql = _normalized(OCR_QUOTA)

    assert "create or replace function public.consume_ocr_quota(p_user_id uuid)" in sql
    assert "returns table ( allowed boolean, used_count integer, monthly_limit integer, reset_at timestamptz )" in sql
    assert "security definer" in sql
    assert "set search_path = pg_catalog, public" in sql
    assert "from public.profiles as p where p.id = p_user_id for update" in sql
    assert "v_used := v_used + 1" in sql
    assert "set ocr_used_this_month = v_used" in sql
    assert "return query select true, v_used, v_limit, v_reset_at" in sql
    assert "return query select false, v_used, v_limit, v_reset_at" in sql
    assert "revoke all on function public.consume_ocr_quota(uuid) from public, anon, authenticated, service_role" in sql
    assert "grant execute on function public.consume_ocr_quota(uuid) to service_role" in sql
    assert "grant execute on function public.consume_ocr_quota(uuid) to authenticated" not in sql
    assert "grant execute on function public.consume_ocr_quota(uuid) to anon" not in sql


def test_purchase_history_is_owner_scoped_and_fail_closed() -> None:
    sql = _normalized(PURCHASE)
    assert "create table public.purchase_history" in sql
    assert "user_id uuid not null references public.profiles(id) on delete cascade" in sql
    assert "alter table public.purchase_history enable row level security" in sql
    assert "alter table public.purchase_history force row level security" in sql
    assert "revoke all on table public.purchase_history from public, anon, authenticated" in sql
    assert "grant select, insert, update, delete on public.purchase_history to authenticated" in sql
    assert sql.count("(select auth.uid()) = user_id") == 5


def test_hardening_sets_fail_closed_defaults_and_no_anonymous_data_grants() -> None:
    sql = _normalized(HARDENING)
    assert "revoke all on schema public from public, anon, authenticated" in sql
    assert "alter default privileges for role postgres in schema public revoke all on tables from public, anon, authenticated" in sql
    assert "alter default privileges for role postgres in schema public revoke all on sequences from public, anon, authenticated" in sql
    assert "alter default privileges for role postgres in schema public revoke execute on functions from public, anon, authenticated" in sql

    assert not re.search(r"grant\s+(?:select|insert|update|delete|all).*?\s+to\s+anon\b", sql)
    assert not re.search(r"grant\s+execute\s+on\s+function.*?\s+to\s+(?:anon|authenticated)\b", sql)


def test_post_ledger_function_hardening_fixes_search_path_and_browser_execute() -> None:
    sql = _normalized(HARDENING)
    assert "where n.nspname = 'public' and p.prosecdef" in sql
    assert "alter function %s set search_path = pg_catalog, public, extensions" in sql
    assert "revoke execute on function %s from public, anon, authenticated" in sql

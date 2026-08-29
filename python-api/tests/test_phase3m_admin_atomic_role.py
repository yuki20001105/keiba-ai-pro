from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "supabase"
    / "bootstrap"
    / "v1"
    / "migrations"
    / "20260720143400_admin_role_change.sql"
)
ROUTE = (
    ROOT
    / "src"
    / "app"
    / "api"
    / "admin"
    / "profiles"
    / "[userId]"
    / "role"
    / "route.ts"
)


def _normalized(path: Path) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8")).lower()


def test_admin_role_rpc_revalidates_actor_and_updates_target_atomically() -> None:
    sql = _normalized(MIGRATION)

    assert "create or replace function public.update_admin_profile_role(" in sql
    assert "security definer" in sql
    assert "set search_path = pg_catalog, public" in sql
    assert "pg_catalog.pg_advisory_xact_lock" in sql
    assert "where p.id = p_actor_user_id for update" in sql
    assert "if not found or v_actor_role <> 'admin'" in sql
    assert "where p.id = p_target_user_id for update" in sql
    assert "update public.profiles as p set role = p_role where p.id = p_target_user_id" in sql
    assert sql.index("v_actor_role <> 'admin'") < sql.index("update public.profiles as p")


def test_admin_role_rpc_prevents_last_admin_demotion_under_the_same_lock() -> None:
    sql = _normalized(MIGRATION)

    lock_position = sql.index("pg_catalog.pg_advisory_xact_lock")
    invariant_position = sql.index("admin_role_change_last_admin_forbidden")
    update_position = sql.index("update public.profiles as p")
    assert lock_position < invariant_position < update_position
    assert "v_previous_role = 'admin' and p_role = 'user'" in sql
    assert "where p.role = 'admin' and p.id <> p_target_user_id" in sql


def test_admin_role_audit_is_written_in_transaction_and_not_exposed() -> None:
    sql = _normalized(MIGRATION)

    assert "create table public.admin_role_change_audit" in sql
    assert "request_id uuid primary key" in sql
    assert "actor_user_id uuid not null" in sql
    assert "target_user_id uuid not null" in sql
    assert "previous_role text not null" in sql
    assert "new_role text not null" in sql
    assert "alter table public.admin_role_change_audit enable row level security" in sql
    assert "alter table public.admin_role_change_audit force row level security" in sql
    assert "revoke all on table public.admin_role_change_audit from public, anon, authenticated, service_role" in sql
    assert "insert into public.admin_role_change_audit" in sql
    assert sql.index("update public.profiles as p") < sql.index("insert into public.admin_role_change_audit")


def test_admin_role_rpc_is_service_role_only() -> None:
    sql = _normalized(MIGRATION)
    signature = "public.update_admin_profile_role(uuid, uuid, text, uuid)"

    assert f"revoke all on function {signature} from public, anon, authenticated, service_role" in sql
    assert f"grant execute on function {signature} to service_role" in sql
    assert f"grant execute on function {signature} to authenticated" not in sql
    assert f"grant execute on function {signature} to anon" not in sql


def test_admin_route_uses_verified_actor_and_never_directly_updates_profiles() -> None:
    route = ROUTE.read_text(encoding="utf-8")

    assert "verifyRequestAuth(request, { requireAdmin: true })" in route
    assert "p_actor_user_id: actor.value" in route
    assert "p_target_user_id: target.value" in route
    assert "p_request_id: requestId" in route
    assert ".rpc('update_admin_profile_role'" in route
    assert ".from('profiles')" not in route
    assert ".update({ role:" not in route
    assert "projectAdminRoleRpcResult" in route

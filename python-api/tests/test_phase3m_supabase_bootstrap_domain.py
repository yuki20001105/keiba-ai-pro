from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "bootstrap" / "v1" / "migrations"
RACE = MIGRATIONS / "20260720142000_race_domain.sql"
NORMALIZED = MIGRATIONS / "20260720142100_normalized_ultimate_domain.sql"
ML = MIGRATIONS / "20260720142200_server_ml_storage.sql"
DOMAIN_FILES = (RACE, NORMALIZED, ML)


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compact(path: Path) -> str:
    return " ".join(_sql(path).lower().split())


def test_domain_migration_versions_are_unique_and_ordered_after_core() -> None:
    versions = []
    for path in DOMAIN_FILES:
        assert path.is_file()
        match = re.fullmatch(r"(\d{14})_[a-z0-9_]+\.sql", path.name)
        assert match is not None
        versions.append(match.group(1))

    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
    assert min(versions) > "20260720141300"


def test_domain_migrations_are_non_destructive_and_never_disable_rls() -> None:
    for path in DOMAIN_FILES:
        sql = _sql(path)
        assert re.search(r"\bdrop\b", sql, flags=re.IGNORECASE) is None
        assert re.search(
            r"\bdisable\s+row\s+level\s+security\b", sql, flags=re.IGNORECASE
        ) is None


def test_races_has_one_bootstrap_definition_with_legacy_and_ultimate_columns() -> None:
    combined = "\n".join(_sql(path) for path in DOMAIN_FILES)
    definitions = re.findall(
        r"create\s+table\s+if\s+not\s+exists\s+public\.races\s*\(",
        combined,
        flags=re.IGNORECASE,
    )
    assert len(definitions) == 1

    race_sql = _compact(RACE)
    for column in (
        "race_id text primary key",
        "date text",
        "kaisai_date date",
        "num_horses integer",
        "horse_count integer",
        "surface text",
        "course_direction text",
        "market_entropy numeric(10, 4)",
        "top3_probability numeric(10, 4)",
        "user_id uuid references public.profiles(id)",
    ):
        assert column in race_sql


def test_race_write_contract_is_service_role_only_with_forced_rls() -> None:
    sql = _compact(RACE)
    for table in ("races", "race_results", "race_odds", "race_payouts"):
        assert f"alter table public.{table} enable row level security" in sql
        assert f"alter table public.{table} force row level security" in sql
        assert table in sql

    for column in (
        "finish_position integer",
        "bracket_number integer",
        "horse_number integer",
        "jockey_weight double precision",
        "finish_time text",
        "odds double precision",
    ):
        assert column in sql

    assert "from public, anon, authenticated" in sql
    assert "to service_role" in sql
    assert re.search(r"\bgrant\b[^;]+\bto\s+(anon|authenticated)\b", sql) is None
    assert re.search(r"\bcreate\s+policy\b", sql) is None


def test_updated_at_triggers_are_replay_safe() -> None:
    race_sql = _compact(RACE)
    normalized_sql = _compact(NORMALIZED)
    ml_sql = _compact(ML)

    assert "create or replace function public.phase3m_set_updated_at()" in race_sql
    assert "language plpgsql security invoker set search_path = pg_catalog, public" in race_sql
    assert (
        "revoke all on function public.phase3m_set_updated_at() from public, anon, authenticated"
        in race_sql
    )
    assert "if not exists" in race_sql
    assert "phase3m_races_set_updated_at" in race_sql
    assert "phase3m_race_odds_set_updated_at" in race_sql
    assert "if not exists" in normalized_sql
    assert "phase3m_horse_details_set_updated_at" not in normalized_sql
    assert "trigger_name := 'phase3m_' || target_table || '_set_updated_at'" in normalized_sql
    assert "if not exists" in ml_sql
    assert "trigger_name := 'phase3m_' || target_table || '_set_updated_at'" in ml_sql


def test_normalized_tables_have_stable_upsert_keys_and_service_only_access() -> None:
    sql = _compact(NORMALIZED)
    for table in (
        "entries",
        "results",
        "horse_details",
        "past_performances",
        "jockey_details",
        "trainer_details",
        "race_lap_times",
        "payouts",
    ):
        assert f"create table if not exists public.{table}" in sql
        assert f"alter table public.{table} enable row level security" in sql
        assert f"alter table public.{table} force row level security" in sql

    assert "constraint entries_pkey primary key (race_id, horse_id)" in sql
    assert "constraint results_pkey primary key (race_id, horse_id)" in sql
    assert "constraint past_performances_pkey primary key (race_id, horse_id)" in sql
    assert "from public, anon, authenticated" in sql
    assert "to service_role" in sql
    assert re.search(r"\bgrant\b[^;]+\bto\s+(anon|authenticated)\b", sql) is None
    assert re.search(r"\bcreate\s+policy\b", sql) is None


def test_ml_training_view_is_security_invoker_and_not_browser_granted() -> None:
    sql = _compact(NORMALIZED)
    assert "create or replace view public.ml_training_data with (security_invoker = true)" in sql
    assert "revoke all on table public.ml_training_data from public, anon, authenticated" in sql
    assert "grant select on table public.ml_training_data to service_role" in sql
    assert re.search(
        r"grant\s+select\s+on\s+table\s+public\.ml_training_data\s+to\s+(anon|authenticated)",
        sql,
    ) is None


def test_server_only_ml_tables_enable_rls_and_revoke_browser_roles() -> None:
    sql = _compact(ML)
    server_tables = (
        "races_ultimate",
        "race_results_ultimate",
        "model_metadata",
        "horse_pedigree",
        "ml_models",
    )
    for table in server_tables:
        assert f"create table if not exists public.{table}" in sql
        assert f"alter table public.{table} enable row level security" in sql
        assert f"alter table public.{table} force row level security" in sql

    assert "from public, anon, authenticated" in sql
    assert "to service_role" in sql
    assert re.search(r"\bgrant\b[^;]+\bto\s+(anon|authenticated)\b", sql) is None
    assert re.search(r"\bcreate\s+policy\b[^;]+\bon\s+public\.", sql) is None


def test_blob_contract_supports_both_current_and_legacy_writers() -> None:
    sql = _compact(ML)
    assert "horse_number text not null" in sql
    assert "race_id text not null references public.races_ultimate(race_id) on delete cascade" in sql
    assert "unique (race_id, horse_number)" in sql
    assert "constraint races_ultimate_data_object check (jsonb_typeof(data) = 'object')" in sql
    assert "constraint race_results_ultimate_data_object check (jsonb_typeof(data) = 'object')" in sql
    assert "new.data ->> 'horse_number'" in sql
    assert "new.data ->> 'horse_num'" in sql
    assert "if new.horse_number is null then" in sql
    assert "using errcode = '23502'" in sql
    assert "phase3m_race_results_ultimate_horse_number" in sql
    assert "language plpgsql security invoker set search_path = pg_catalog, public" in sql
    assert (
        "revoke all on function public.phase3m_normalize_ultimate_horse_number() "
        "from public, anon, authenticated"
        in sql
    )
    assert "user_id text not null default 'shared'" in sql
    assert "check (metadata is null or jsonb_typeof(metadata) = 'object')" in sql


def test_models_bucket_is_private_and_browser_access_is_restrictively_denied() -> None:
    sql = _compact(ML)
    assert "insert into storage.buckets" in sql
    assert "'models', 'models', false" in sql
    assert "on conflict (id) do update" in sql
    assert "public = false" in sql
    assert "create policy phase3m_models_browser_deny" in sql
    assert "as restrictive" in sql
    assert "to anon, authenticated" in sql
    assert "using (bucket_id <> 'models')" in sql
    assert "with check (bucket_id <> 'models')" in sql

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "security" / "run_phase3m_supabase_bootstrap_gate.py"
DEFAULT_MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
DEFAULT_CONTRACT = ROOT / "reports" / "phase3n_legacy_production_adoption_contract_20260816.json"
DEFAULT_OUTPUT = ROOT / "reports" / "phase3n_legacy_production_adoption_review.sql"
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PROJECT_REF_PATTERN = re.compile(r"^[a-z]{20}$")
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
COLUMN_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class RenderFailure(RuntimeError):
    pass


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase3m_bootstrap_runner_for_legacy_adoption",
        RUNNER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RenderFailure("runner-import-unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = _load_runner()


def _safe_report_path(path: Path, *, suffix: str) -> Path:
    reports = (ROOT / "reports").resolve()
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(reports)
    except ValueError as exc:
        raise RenderFailure("output-must-be-under-reports") from exc
    if resolved.suffix.lower() != suffix or resolved.exists() and resolved.is_symlink():
        raise RenderFailure("output-path-invalid")
    return resolved


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _load_contract(path: Path) -> tuple[dict[str, Any], str]:
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise RenderFailure("contract-outside-repository") from exc
    if resolved.is_symlink() or resolved.stat().st_size > 256 * 1024:
        raise RenderFailure("contract-path-invalid")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RenderFailure("contract-read-failed") from exc
    if not isinstance(value, dict):
        raise RenderFailure("contract-shape-invalid")
    return value, hashlib.sha256(_canonical_json(value)).hexdigest()


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_array(values: list[str]) -> str:
    return "ARRAY[" + ",".join(_sql_literal(value) for value in values) + "]::TEXT[]"


def _validated_contract(
    raw: dict[str, Any],
    *,
    expected_commit: str,
) -> dict[str, Any]:
    if raw.get("schema_version") != 1:
        raise RenderFailure("contract-version-invalid")
    if raw.get("strategy") != "in-place-archive-and-canonical-rebuild":
        raise RenderFailure("contract-strategy-invalid")
    project_ref = raw.get("production_project_ref")
    archive_schema = raw.get("archive_schema")
    candidate = raw.get("candidate_commit_sha")
    if not isinstance(project_ref, str) or PROJECT_REF_PATTERN.fullmatch(project_ref) is None:
        raise RenderFailure("contract-project-ref-invalid")
    if not isinstance(archive_schema, str) or IDENTIFIER_PATTERN.fullmatch(archive_schema) is None:
        raise RenderFailure("contract-archive-schema-invalid")
    if candidate != expected_commit:
        raise RenderFailure("contract-candidate-mismatch")

    expected_tables = raw.get("expected_public_tables")
    row_counts = raw.get("source_row_counts")
    row_digests = raw.get("source_row_digests")
    digest_keys = raw.get("digest_key_columns")
    if (
        not isinstance(expected_tables, list)
        or not expected_tables
        or expected_tables != sorted(set(expected_tables))
        or any(not isinstance(name, str) or IDENTIFIER_PATTERN.fullmatch(name) is None for name in expected_tables)
    ):
        raise RenderFailure("contract-table-list-invalid")
    if not isinstance(row_counts, dict) or sorted(row_counts) != expected_tables:
        raise RenderFailure("contract-row-counts-invalid")
    if any(not isinstance(value, int) or value < 0 for value in row_counts.values()):
        raise RenderFailure("contract-row-counts-invalid")
    nonempty = sorted(name for name, count in row_counts.items() if count > 0)
    if not isinstance(row_digests, dict) or sorted(row_digests) != nonempty:
        raise RenderFailure("contract-row-digests-invalid")
    if any(not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None for value in row_digests.values()):
        raise RenderFailure("contract-row-digests-invalid")
    if not isinstance(digest_keys, dict) or sorted(digest_keys) != nonempty:
        raise RenderFailure("contract-digest-keys-invalid")
    if any(
        not isinstance(value, str) or COLUMN_IDENTIFIER_PATTERN.fullmatch(value) is None
        for value in digest_keys.values()
    ):
        raise RenderFailure("contract-digest-keys-invalid")

    required_facts = {
        "phase3m_history_exists": False,
        "phase3n_table_count": 0,
        "race_results_without_race": 0,
        "race_payouts_without_race": 0,
        "ultimate_results_without_race": 0,
        "ultimate_missing_horse_number_after_decode": 0,
        "ultimate_duplicate_race_horse_after_decode": 0,
        "ultimate_non_object_after_decode": 0,
        "profile_without_auth_user": 0,
        "purchase_without_profile": 0,
        "invalid_profile_role": 0,
        "invalid_subscription_tier": 0,
        "models_bucket_exists": True,
        "model_storage_object_count": 146,
        "metadata_path_without_object": 0,
    }
    facts = raw.get("preflight_facts")
    if not isinstance(facts, dict) or any(facts.get(key) != value for key, value in required_facts.items()):
        raise RenderFailure("contract-preflight-facts-invalid")
    user_ref_policy = raw.get("legacy_domain_user_reference_policy")
    if (
        not isinstance(user_ref_policy, dict)
        or user_ref_policy.get("action") != "set_missing_profile_reference_to_null_in_canonical_copy"
        or user_ref_policy.get("archive_preserves_original") is not True
        or sorted(user_ref_policy.get("tables", {})) != ["race_payouts", "race_results", "races"]
    ):
        raise RenderFailure("contract-user-reference-policy-invalid")
    for table, values in user_ref_policy["tables"].items():
        if (
            not isinstance(values, dict)
            or any(
                not isinstance(values.get(key), int) or values[key] < 0
                for key in (
                    "rows_without_profile",
                    "distinct_user_ids_without_profile",
                    "rows_without_auth",
                    "expected_canonical_null_rows",
                )
            )
        ):
            raise RenderFailure("contract-user-reference-policy-invalid")
    authorized = raw.get("migration_apply_authorized")
    review_status = raw.get("review_status")
    approval_reference = raw.get("approval_reference")
    clone_authorized = raw.get("disposable_clone_test_authorized")
    clone_reference = raw.get("clone_test_approval_reference")
    if authorized not in (True, False):
        raise RenderFailure("contract-approval-state-invalid")
    if authorized:
        if review_status != "approved" or not isinstance(approval_reference, str) or not approval_reference.strip():
            raise RenderFailure("contract-approval-state-invalid")
    elif review_status != "pending" or approval_reference is not None:
        raise RenderFailure("contract-approval-state-invalid")
    if clone_authorized not in (True, False):
        raise RenderFailure("contract-clone-approval-state-invalid")
    if clone_authorized:
        if not isinstance(clone_reference, str) or not clone_reference.strip():
            raise RenderFailure("contract-clone-approval-state-invalid")
    elif clone_reference is not None:
        raise RenderFailure("contract-clone-approval-state-invalid")
    return raw


def _row_digest_sql(schema: str, table: str, key: str) -> str:
    return f"""(
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT {key}::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM {schema}.{table} AS t
        ) AS source_rows
    )"""


def _preflight_block(contract: dict[str, Any]) -> str:
    tables: list[str] = contract["expected_public_tables"]
    row_counts: dict[str, int] = contract["source_row_counts"]
    row_digests: dict[str, str] = contract["source_row_digests"]
    key_columns: dict[str, str] = contract["digest_key_columns"]
    archive = contract["archive_schema"]
    checks = [
        f"""    IF (SELECT array_agg(c.relname::TEXT ORDER BY c.relname::TEXT)
          FROM pg_catalog.pg_class AS c
          JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p'))
       IS DISTINCT FROM {_sql_array(tables)} THEN
        RAISE EXCEPTION 'phase3n-legacy-public-table-set-changed';
    END IF;""",
        f"""    IF to_regnamespace({_sql_literal(archive)}) IS NOT NULL THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-already-exists';
    END IF;""",
        """    IF to_regclass('phase3m_internal.bootstrap_history') IS NOT NULL THEN
        RAISE EXCEPTION 'phase3n-legacy-bootstrap-history-unexpected';
    END IF;""",
        """    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_class AS c
          JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind IN ('r', 'p')
           AND c.relname LIKE 'phase3n_%'
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-phase3n-object-unexpected';
    END IF;""",
    ]
    for table in tables:
        checks.append(
            f"""    IF (SELECT count(*) FROM public.{table}) <> {row_counts[table]} THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:{table}';
    END IF;"""
        )
    for table, digest in sorted(row_digests.items()):
        checks.append(
            f"""    IF {_row_digest_sql('public', table, key_columns[table])}
       IS DISTINCT FROM {_sql_literal(digest)} THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:{table}';
    END IF;"""
        )
    for table, values in sorted(contract["legacy_domain_user_reference_policy"]["tables"].items()):
        checks.append(
            f"""    IF (SELECT count(*) FROM public.{table} AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> {values['rows_without_profile']}
       OR (SELECT count(DISTINCT source.user_id) FROM public.{table} AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> {values['distinct_user_ids_without_profile']}
       OR (SELECT count(*) FROM public.{table} AS source
        LEFT JOIN auth.users AS u ON u.id = source.user_id
        WHERE source.user_id IS NOT NULL AND u.id IS NULL) <> {values['rows_without_auth']} THEN
        RAISE EXCEPTION 'phase3n-legacy-user-reference-contract-changed:{table}';
    END IF;"""
        )
    checks.extend(
        (
            """    IF EXISTS (
        SELECT 1 FROM public.race_results AS rr
        LEFT JOIN public.races AS r ON r.race_id = rr.race_id
        WHERE r.race_id IS NULL
    ) OR EXISTS (
        SELECT 1 FROM public.race_payouts AS rp
        LEFT JOIN public.races AS r ON r.race_id = rp.race_id
        WHERE r.race_id IS NULL
    ) OR EXISTS (
        SELECT 1 FROM public.race_results_ultimate AS rr
        LEFT JOIN public.races_ultimate AS r ON r.race_id = rr.race_id
        WHERE r.race_id IS NULL
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-orphan-reference-detected';
    END IF;""",
            """    IF EXISTS (
        WITH parsed AS (
            SELECT race_id, (data #>> '{}')::JSONB AS data
            FROM public.race_results_ultimate
        )
        SELECT 1 FROM parsed
        WHERE jsonb_typeof(data) <> 'object'
           OR COALESCE(
                NULLIF(btrim(data ->> 'horse_number'), ''),
                NULLIF(btrim(data ->> 'horse_num'), '')
           ) IS NULL
    ) OR EXISTS (
        WITH parsed AS (
            SELECT race_id,
                   COALESCE(
                       NULLIF(btrim(((data #>> '{}')::JSONB) ->> 'horse_number'), ''),
                       NULLIF(btrim(((data #>> '{}')::JSONB) ->> 'horse_num'), '')
                   ) AS horse_number
            FROM public.race_results_ultimate
        )
        SELECT 1 FROM parsed GROUP BY race_id, horse_number HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-ultimate-transform-invalid';
    END IF;""",
            """    IF EXISTS (
        SELECT 1 FROM public.profiles AS p
        LEFT JOIN auth.users AS u ON u.id = p.id
        WHERE u.id IS NULL OR p.role IS NULL OR p.role NOT IN ('user', 'admin')
           OR p.subscription_tier IS NULL OR p.subscription_tier NOT IN ('free', 'premium')
    ) OR EXISTS (
        SELECT 1 FROM public.purchase_history AS ph
        LEFT JOIN public.profiles AS p ON p.id = ph.user_id
        WHERE p.id IS NULL
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-identity-contract-invalid';
    END IF;""",
            """    IF NOT EXISTS (
        SELECT 1 FROM storage.buckets WHERE id = 'models' AND name = 'models'
    ) OR (SELECT count(*) FROM storage.objects WHERE bucket_id = 'models') <> 146
       OR EXISTS (
           SELECT 1 FROM public.model_metadata AS m
           LEFT JOIN storage.objects AS o
             ON o.bucket_id = 'models' AND o.name = m.storage_path
           WHERE o.id IS NULL
       ) THEN
        RAISE EXCEPTION 'phase3n-legacy-model-storage-contract-invalid';
    END IF;""",
        )
    )
    return "\n".join(("DO $phase3n_legacy_preflight$", "BEGIN", *checks, "END", "$phase3n_legacy_preflight$;"))


def _archive_sql(contract: dict[str, Any]) -> str:
    archive = contract["archive_schema"]
    statements = [
        f"CREATE SCHEMA {archive} AUTHORIZATION postgres;",
        f"REVOKE ALL ON SCHEMA {archive} FROM PUBLIC, anon, authenticated, service_role;",
        f"ALTER VIEW public.ml_training_data SET SCHEMA {archive};",
    ]
    for table in contract["expected_public_tables"]:
        statements.append(f"ALTER TABLE public.{table} SET SCHEMA {archive};")
    statements.extend(
        (
            f"ALTER FUNCTION public.consume_pred_count(UUID) SET SCHEMA {archive};",
            f"ALTER FUNCTION public.handle_new_user() SET SCHEMA {archive};",
            f"ALTER FUNCTION public.reset_ocr_counter() SET SCHEMA {archive};",
            f"ALTER FUNCTION public.reset_pred_count_if_needed(UUID) SET SCHEMA {archive};",
            f"ALTER FUNCTION public.update_updated_at_column() SET SCHEMA {archive};",
            f"REVOKE ALL ON ALL TABLES IN SCHEMA {archive} FROM PUBLIC, anon, authenticated, service_role;",
            f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {archive} FROM PUBLIC, anon, authenticated, service_role;",
            f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {archive} FROM PUBLIC, anon, authenticated;",
        )
    )
    return "\n".join(statements)


def _history_sql(manifest: object, commit: str) -> str:
    values = ",\n".join(
        "(" + ", ".join(
            (
                str(ordinal),
                _sql_literal(migration.version),
                _sql_literal(migration.path),
                _sql_literal(migration.source),
                _sql_literal(migration.sha256),
                _sql_literal(manifest.chain_digest),
                _sql_literal(manifest.bootstrap_id),
                _sql_literal(manifest.sha256),
                _sql_literal(commit),
            )
        ) + ")"
        for ordinal, migration in enumerate(manifest.migrations, start=1)
    )
    return f"""CREATE SCHEMA phase3m_internal AUTHORIZATION postgres;
REVOKE ALL ON SCHEMA phase3m_internal FROM PUBLIC, anon, authenticated, service_role;
CREATE TABLE phase3m_internal.bootstrap_history (
    ordinal INTEGER PRIMARY KEY CHECK (ordinal > 0),
    version TEXT NOT NULL UNIQUE CHECK (version ~ '^[0-9]{{14}}$'),
    path TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL CHECK (migration_sha256 ~ '^[0-9a-f]{{64}}$'),
    chain_digest TEXT NOT NULL CHECK (chain_digest ~ '^[0-9a-f]{{64}}$'),
    bootstrap_id TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{{64}}$'),
    expected_commit_sha TEXT NOT NULL CHECK (expected_commit_sha ~ '^[0-9a-f]{{40}}$'),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
REVOKE ALL ON TABLE phase3m_internal.bootstrap_history FROM PUBLIC, anon, authenticated, service_role;
INSERT INTO phase3m_internal.bootstrap_history (
    ordinal, version, path, source, migration_sha256,
    chain_digest, bootstrap_id, manifest_sha256, expected_commit_sha
)
VALUES
{values};"""


def _copy_sql(contract: dict[str, Any]) -> str:
    a = contract["archive_schema"]
    return f"""INSERT INTO public.profiles (
    id, email, full_name, role, subscription_tier,
    stripe_customer_id, stripe_subscription_id,
    ocr_monthly_limit, ocr_used_this_month, ocr_reset_date,
    pred_count_remaining, pred_count_reset_at, created_at, updated_at
)
SELECT id, email, full_name, role, subscription_tier,
       stripe_customer_id, stripe_subscription_id,
       COALESCE(ocr_monthly_limit, 10), COALESCE(ocr_used_this_month, 0),
       COALESCE(ocr_reset_date, now()), COALESCE(pred_count_remaining, 10),
       COALESCE(pred_count_reset_at, date_trunc('month', now()) + interval '1 month'),
       COALESCE(created_at, now()), COALESCE(updated_at, created_at, now())
FROM {a}.profiles;

INSERT INTO public.purchase_history (
    id, user_id, race_id, purchase_date, season, venue, bet_type, combinations,
    strategy_type, purchase_count, unit_price, total_cost, expected_value,
    expected_return, actual_return, is_hit, recovery_rate, created_at
)
SELECT id, user_id, race_id, purchase_date, season, venue, bet_type, combinations,
       strategy_type, purchase_count, unit_price, total_cost, expected_value,
       expected_return, COALESCE(actual_return, 0), COALESCE(is_hit, false),
       COALESCE(recovery_rate, 0), COALESCE(created_at, now())
FROM {a}.purchase_history;

INSERT INTO public.races (
    race_id, race_name, venue, date, kaisai_date, post_time, race_class, distance,
    track_type, surface, course_direction, weather, field_condition, kai, day,
    num_horses, horse_count, prize_money, market_entropy, top3_probability,
    source, user_id, created_at, updated_at
)
SELECT race_id, race_name, venue, date, kaisai_date, post_time, race_class, distance,
       track_type, surface, course_direction, weather, field_condition, kai, day,
       num_horses, horse_count, prize_money, market_entropy, top3_probability,
       source, CASE WHEN EXISTS (
           SELECT 1 FROM {a}.profiles AS p WHERE p.id = source_rows.user_id
       ) THEN source_rows.user_id ELSE NULL END,
       COALESCE(source_rows.created_at, now()), COALESCE(source_rows.updated_at, source_rows.created_at, now())
FROM {a}.races AS source_rows;

INSERT INTO public.race_results (
    id, race_id, finish_position, bracket_number, horse_number, horse_name, sex,
    age, jockey_weight, jockey_name, trainer_name, owner_name, finish_time, odds,
    popularity, margin, corner_positions, last_3f_time, horse_weight,
    weight_change, prize_money, user_id, created_at
)
SELECT id, race_id, finish_position, bracket_number, horse_number, horse_name, sex,
       age, jockey_weight, jockey_name, trainer_name, owner_name, finish_time, odds,
       popularity, margin, corner_positions, last_3f_time, horse_weight,
       weight_change, prize_money::BIGINT,
       CASE WHEN EXISTS (
           SELECT 1 FROM {a}.profiles AS p WHERE p.id = source_rows.user_id
       ) THEN source_rows.user_id ELSE NULL END,
       COALESCE(source_rows.created_at, now())
FROM {a}.race_results AS source_rows;

INSERT INTO public.race_payouts (
    id, race_id, bet_type, combination, payout, popularity, user_id, created_at
)
SELECT id, race_id, bet_type, combination, payout, popularity,
       CASE WHEN EXISTS (
           SELECT 1 FROM {a}.profiles AS p WHERE p.id = source_rows.user_id
       ) THEN source_rows.user_id ELSE NULL END,
       COALESCE(source_rows.created_at, now())
FROM {a}.race_payouts AS source_rows;

INSERT INTO public.races_ultimate (race_id, data, created_at, updated_at)
SELECT race_id, (data #>> '{{}}')::JSONB,
       COALESCE(created_at, now()), COALESCE(created_at, now())
FROM {a}.races_ultimate;

INSERT INTO public.race_results_ultimate (
    id, race_id, horse_number, data, created_at, updated_at
)
SELECT id, race_id,
       COALESCE(
           NULLIF(btrim(((data #>> '{{}}')::JSONB) ->> 'horse_number'), ''),
           NULLIF(btrim(((data #>> '{{}}')::JSONB) ->> 'horse_num'), '')
       ),
       (data #>> '{{}}')::JSONB,
       COALESCE(created_at, now()), COALESCE(created_at, now())
FROM {a}.race_results_ultimate;
SELECT setval(
    'public.race_results_ultimate_id_seq',
    (SELECT max(id) FROM public.race_results_ultimate),
    true
);

INSERT INTO public.model_metadata (
    model_id, user_id, storage_path, metadata, created_at, updated_at
)
SELECT model_id, 'shared', storage_path, (metadata #>> '{{}}')::JSONB,
       COALESCE(created_at, now()), COALESCE(created_at, now())
FROM {a}.model_metadata;

INSERT INTO public.horse_pedigree (
    horse_id, sire, dam, damsire, created_at, updated_at
)
SELECT horse_id, sire, dam, damsire,
       COALESCE(created_at, now()), COALESCE(created_at, now())
FROM {a}.horse_pedigree;
"""


def _postcondition_block(contract: dict[str, Any]) -> str:
    archive = contract["archive_schema"]
    row_counts: dict[str, int] = contract["source_row_counts"]
    row_digests: dict[str, str] = contract["source_row_digests"]
    key_columns: dict[str, str] = contract["digest_key_columns"]
    migrated = {
        "profiles",
        "purchase_history",
        "races",
        "race_results",
        "race_payouts",
        "races_ultimate",
        "race_results_ultimate",
        "model_metadata",
        "horse_pedigree",
    }
    checks: list[str] = []
    for table, expected in sorted(row_counts.items()):
        checks.append(
            f"""    IF (SELECT count(*) FROM {archive}.{table}) <> {expected} THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:{table}';
    END IF;"""
        )
        if table in migrated:
            checks.append(
                f"""    IF (SELECT count(*) FROM public.{table}) <> {expected} THEN
        RAISE EXCEPTION 'phase3n-legacy-migrated-row-count-mismatch:{table}';
    END IF;"""
            )
    for table, digest in sorted(row_digests.items()):
        checks.append(
            f"""    IF {_row_digest_sql(archive, table, key_columns[table])}
       IS DISTINCT FROM {_sql_literal(digest)} THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-digest-mismatch:{table}';
    END IF;"""
        )
    for table, values in sorted(contract["legacy_domain_user_reference_policy"]["tables"].items()):
        checks.append(
            f"""    IF (SELECT count(*) FROM public.{table} WHERE user_id IS NULL)
       <> {values['expected_canonical_null_rows']}
       OR EXISTS (
           SELECT 1 FROM public.{table} AS canonical
           LEFT JOIN public.profiles AS p ON p.id = canonical.user_id
           WHERE canonical.user_id IS NOT NULL AND p.id IS NULL
       ) THEN
        RAISE EXCEPTION 'phase3n-legacy-canonical-user-reference-invalid:{table}';
    END IF;"""
        )
    checks.extend(
        (
            """    IF (SELECT count(*) FROM phase3m_internal.bootstrap_history) <> 21 THEN
        RAISE EXCEPTION 'phase3n-legacy-bootstrap-history-incomplete';
    END IF;""",
            """    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'phase3n_prediction_observations'
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-phase3n-postcondition-missing';
    END IF;""",
        )
    )
    return "\n".join(("DO $phase3n_legacy_postcondition$", "BEGIN", *checks, "END", "$phase3n_legacy_postcondition$;"))


def build_adoption_transaction(
    *,
    contract: dict[str, Any],
    contract_sha256: str,
    manifest: object,
    candidate_commit: str,
    review_only: bool,
    target_scope: str = "review",
    target_project_ref: str | None = None,
    clone_fault_after_archive: bool = False,
) -> str:
    statements = [
        "BEGIN;",
        f"-- phase3n legacy Production adoption candidate {candidate_commit}",
        f"-- phase3n adoption contract sha256 {contract_sha256}",
        f"-- provider project ref (out-of-band binding) {contract['production_project_ref']}",
        f"-- execution target scope {target_scope}",
        f"-- execution target project ref {target_project_ref or 'not-bound'}",
        "SET LOCAL lock_timeout = '5s';",
        "SET LOCAL statement_timeout = '300s';",
        "SET LOCAL idle_in_transaction_session_timeout = '300s';",
        (
            "SELECT pg_catalog.pg_advisory_xact_lock("
            f"pg_catalog.hashtextextended('phase3n-legacy-adoption:{contract_sha256}', 0));"
        ),
        _preflight_block(contract),
    ]
    if review_only:
        statements.append(
            """DO $phase3n_review_only_guard$
BEGIN
    RAISE EXCEPTION 'phase3n-legacy-adoption-review-only-not-authorized';
END
$phase3n_review_only_guard$;"""
        )
    statements.extend(
        (
            "-- Archive legacy objects without deleting rows.",
            _archive_sql(contract),
        )
    )
    if clone_fault_after_archive:
        statements.append(
            """DO $phase3n_clone_rollback_probe$
BEGIN
    RAISE EXCEPTION 'phase3n-disposable-clone-forced-rollback-after-archive';
END
$phase3n_clone_rollback_probe$;"""
        )
    for migration in manifest.migrations:
        try:
            content = migration.content.decode("utf-8", errors="strict").rstrip()
        except UnicodeError as exc:
            raise RenderFailure("migration-read-failed") from exc
        statements.extend(
            (
                f"-- canonical migration {migration.version} ({migration.path})",
                content,
            )
        )
    statements.extend(
        (
            _history_sql(manifest, candidate_commit),
            "-- Copy only reviewed legacy rows into canonical tables.",
            _copy_sql(contract),
            _postcondition_block(contract),
            "COMMIT;",
        )
    )
    return "\n".join(statements) + "\n"


def render_bundle(
    *,
    contract_path: Path,
    manifest_path: Path,
    expected_commit: str,
    output_path: Path,
    approval_digest: str | None,
    approval_reference: str | None,
    target_scope: str = "review",
    target_project_ref: str | None = None,
    clone_fault_after_archive: bool = False,
) -> dict[str, Any]:
    commit = expected_commit.strip().lower()
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise RenderFailure("candidate-commit-invalid")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip().lower()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RenderFailure("repository-head-unavailable") from exc
    RUNNER._tested_commit(head)
    raw_contract, contract_sha256 = _load_contract(contract_path)
    contract = _validated_contract(raw_contract, expected_commit=commit)
    manifest = RUNNER.load_ancestor_manifest(
        RUNNER._require_canonical_manifest(manifest_path),
        ancestor_commit=commit,
        expected_head_commit=head,
    )
    if len(manifest.migrations) != 21:
        raise RenderFailure("canonical-migration-count-invalid")

    if target_scope not in {"review", "disposable-clone", "production"}:
        raise RenderFailure("target-scope-invalid")
    if clone_fault_after_archive and target_scope != "disposable-clone":
        raise RenderFailure("clone-fault-injection-scope-invalid")
    approved = contract["migration_apply_authorized"] is True
    clone_authorized = contract["disposable_clone_test_authorized"] is True
    review_only = target_scope == "review"
    if target_scope == "production":
        if not approved:
            raise RenderFailure("production-migration-approval-not-recorded")
        if approval_digest != contract_sha256:
            raise RenderFailure("approval-digest-mismatch")
        if not isinstance(approval_reference, str) or not approval_reference.strip():
            raise RenderFailure("approval-reference-required")
        if approval_reference != contract["approval_reference"]:
            raise RenderFailure("approval-reference-mismatch")
        if target_project_ref != contract["production_project_ref"]:
            raise RenderFailure("production-project-ref-mismatch")
    elif target_scope == "disposable-clone":
        if not clone_authorized:
            raise RenderFailure("disposable-clone-test-not-authorized")
        if approval_digest != contract_sha256:
            raise RenderFailure("approval-digest-mismatch")
        if approval_reference != contract["clone_test_approval_reference"]:
            raise RenderFailure("clone-approval-reference-mismatch")
        if (
            not isinstance(target_project_ref, str)
            or PROJECT_REF_PATTERN.fullmatch(target_project_ref) is None
            or target_project_ref == contract["production_project_ref"]
        ):
            raise RenderFailure("disposable-clone-project-ref-invalid")
    elif any(value is not None for value in (approval_digest, approval_reference, target_project_ref)):
        raise RenderFailure("review-scope-does-not-accept-approval")

    sql = build_adoption_transaction(
        contract=contract,
        contract_sha256=contract_sha256,
        manifest=manifest,
        candidate_commit=commit,
        review_only=review_only,
        target_scope=target_scope,
        target_project_ref=target_project_ref,
        clone_fault_after_archive=clone_fault_after_archive,
    )
    required = (
        "BEGIN;",
        "$phase3n_legacy_preflight$",
        "$phase3n_legacy_postcondition$",
        f"CREATE SCHEMA {contract['archive_schema']}",
        "phase3m_internal.bootstrap_history",
        "phase3n_prediction_observations",
        "COMMIT;",
    )
    if not all(fragment in sql for fragment in required):
        raise RenderFailure("rendered-adoption-contract-incomplete")
    if review_only and "phase3n-legacy-adoption-review-only-not-authorized" not in sql:
        raise RenderFailure("review-only-guard-missing")
    if any(fragment in sql.upper() for fragment in ("DROP TABLE", "TRUNCATE TABLE", "DELETE FROM")):
        raise RenderFailure("destructive-sql-detected")
    if "postgresql://" in sql.lower() or "service_role_key" in sql.lower():
        raise RenderFailure("rendered-adoption-secret-like-content")

    output = _safe_report_path(output_path, suffix=".sql")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(sql, encoding="utf-8", newline="\n")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "schema_version": 1,
        "candidate_commit_sha": commit,
        "production_project_ref": contract["production_project_ref"],
        "contract_sha256": contract_sha256,
        "manifest_sha256": manifest.sha256,
        "chain_digest": manifest.chain_digest,
        "migration_count": len(manifest.migrations),
        "review_only": review_only,
        "target_scope": target_scope,
        "target_project_ref": target_project_ref,
        "migration_apply_authorized": approved,
        "approval_reference": approval_reference,
        "clone_fault_after_archive": clone_fault_after_archive,
        "output": output.relative_to(ROOT).as_posix(),
        "output_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "remote_connection_attempted": False,
        "production_changed": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render a fail-closed legacy Production adoption review/apply SQL bundle. "
            "The default pending contract emits a review-only transaction that always aborts."
        )
    )
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--approval-digest")
    parser.add_argument("--approval-reference")
    parser.add_argument(
        "--target-scope",
        choices=("review", "disposable-clone", "production"),
        default="review",
    )
    parser.add_argument("--target-project-ref")
    parser.add_argument("--clone-fault-after-archive", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = render_bundle(
            contract_path=args.contract,
            manifest_path=args.manifest,
            expected_commit=args.expected_commit,
            output_path=args.output,
            approval_digest=args.approval_digest,
            approval_reference=args.approval_reference,
            target_scope=args.target_scope,
            target_project_ref=args.target_project_ref,
            clone_fault_after_archive=args.clone_fault_after_archive,
        )
    except (RenderFailure, RUNNER.GateFailure) as exc:
        print(json.dumps({"success": False, "failure_code": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"success": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

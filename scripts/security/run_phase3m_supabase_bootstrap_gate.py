from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
IMAGE = "postgres:17.6-bookworm@sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3"
DEFAULT_MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
PRELUDE = ROOT / "supabase" / "bootstrap" / "v1" / "tests" / "supabase_prelude.sql"
CONTRACT = ROOT / "supabase" / "bootstrap" / "v1" / "tests" / "bootstrap_contract.sql"
REPORT = ROOT / "reports" / "phase3m_supabase_bootstrap_gate.json"

MAX_MANIFEST_BYTES = 64 * 1024
MAX_MIGRATION_BYTES = 512 * 1024
MAX_MIGRATIONS = 64
LOCAL_DOCKER_ENDPOINTS = ("unix://", "npipe://")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
VERSION_PATTERN = re.compile(r"^[0-9]{14}$")
TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{12,64}$")
SAFE_SOURCE_VALUES = frozenset({"phase3m-bootstrap", "reconciled-existing"})
SAFE_PATH_PREFIXES = (
    PurePosixPath("supabase/bootstrap/v1/migrations"),
    PurePosixPath("supabase/migrations"),
)
REQUIRED_MARKERS = frozenset(
    {
        "all_public_tables_rls",
        "authenticated_idor_boundaries",
        "bootstrap_history_authoritative",
        "no_unsafe_browser_grants",
        "security_definer_hardened",
        "service_rpc_grants",
        "profile_bank_trigger",
        "private_model_storage",
        "security_invoker_ml_view",
        "storage_role_boundaries",
        "required_triggers_enabled",
    }
)

FENCING_SEQUENCE_PREFLIGHT_FRAGMENT = (
    "c.relname = 'scrape_execution_reservation_fencing_seq'"
)
TARGET_PREFLIGHT_REQUIRED_FRAGMENTS = (
    "c.relkind = 'S'",
    FENCING_SEQUENCE_PREFLIGHT_FRAGMENT,
    "'consume_ocr_quota'",
    "'update_admin_profile_role'",
    "FROM storage.buckets AS b",
    "b.id = 'models' OR b.name = 'models'",
    "FROM storage.objects AS o",
    "o.bucket_id = 'models'",
    "FROM pg_catalog.pg_policy AS pol",
    "pol.polname = 'phase3m_models_browser_deny'",
    "n.nspname = 'auth' AND c.relname = 'users' AND t.tgname = 'on_auth_user_created'",
)

TARGET_PREFLIGHT_SQL = """
DO $phase3m_target_preflight$
BEGIN
    -- Hosted Supabase already owns auth/storage schemas and their platform
    -- relations.  Reject only Phase 3M application signatures so a normal
    -- hosted project passes while any partial bootstrap fails closed.
    IF to_regnamespace('phase3m_internal') IS NOT NULL
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.pg_class AS c
           JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public'
             AND c.relname = ANY (ARRAY[
                 'profiles', 'predictions', 'bets', 'bank_records', 'ocr_usage',
                 'purchase_history', 'races', 'race_results', 'race_odds',
                 'race_payouts', 'horse_details', 'jockey_details', 'trainer_details',
                 'entries', 'results', 'past_performances', 'race_lap_times',
                 'payouts', 'ml_training_data', 'races_ultimate',
                 'race_results_ultimate', 'model_metadata', 'horse_pedigree',
                 'ml_models', 'scrape_uncertainty_review_requests',
                 'scrape_uncertainty_review_events', 'scrape_execution_authorizations',
                 'scrape_execution_reservations', 'scrape_execution_reservation_events',
                 'admin_role_change_audit'
             ])
       )
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.pg_proc AS p
           JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
           WHERE n.nspname = 'public'
             AND p.proname = ANY (ARRAY[
                 'phase3m_touch_updated_at', 'phase3m_handle_new_user',
                 'reset_pred_count_if_needed', 'consume_pred_count_batch',
                 'consume_pred_count', 'consume_ocr_quota', 'phase3m_set_updated_at',
                 'phase3m_normalize_ultimate_horse_number',
                 '_reject_scrape_uncertainty_event_mutation',
                 '_scrape_uncertainty_require_admin', '_scrape_uncertainty_payload_hash',
                 '_expire_scrape_uncertainty_review_if_needed',
                 'create_scrape_uncertainty_review', 'get_scrape_uncertainty_review',
                 'list_scrape_uncertainty_reviews', 'transition_scrape_uncertainty_review',
                 '_scrape_execution_binding_hash',
                 '_validate_scrape_execution_authorization_insert',
                 '_reject_scrape_execution_authorization_mutation',
                 '_reject_scrape_execution_reservation_event_mutation',
                 '_materialize_scrape_execution_reservation_expiry',
                 'reserve_scrape_execution', 'consume_scrape_execution_reservation',
                 'release_scrape_execution_reservation',
                 'expire_scrape_execution_reservation', 'update_admin_profile_role'
             ])
       )
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.pg_class AS c
           JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public'
             AND c.relkind = 'S'
             AND c.relname = 'scrape_execution_reservation_fencing_seq'
       )
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.pg_trigger AS t
           JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
           JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
           WHERE NOT t.tgisinternal
             AND (
                 (n.nspname = 'auth' AND c.relname = 'users' AND t.tgname = 'on_auth_user_created')
                 OR t.tgname LIKE 'phase3m_%'
                 OR t.tgname LIKE 'trg_scrape_%'
             )
       )
       OR EXISTS (
           SELECT 1
           FROM storage.buckets AS b
           WHERE b.id = 'models' OR b.name = 'models'
       )
       OR EXISTS (
           SELECT 1
           FROM storage.objects AS o
           WHERE o.bucket_id = 'models'
       )
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.pg_policy AS pol
           JOIN pg_catalog.pg_class AS c ON c.oid = pol.polrelid
           JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
           WHERE n.nspname = 'storage'
             AND c.relname = 'objects'
             AND pol.polname = 'phase3m_models_browser_deny'
       ) THEN
        RAISE EXCEPTION 'phase3m target already contains bootstrap application objects';
    END IF;
END;
$phase3m_target_preflight$;
""".strip()


class GateFailure(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class DuplicateKeyError(ValueError):
    pass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class BinaryCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class MigrationEntry:
    version: str
    path: str
    source: str
    description: str
    absolute_path: Path
    sha256: str
    content: bytes


@dataclass(frozen=True)
class BootstrapManifest:
    schema_version: int
    bootstrap_id: str
    postgres_image: str
    sha256: str
    chain_digest: str
    migrations: tuple[MigrationEntry, ...]


@dataclass(frozen=True)
class DatabaseResult:
    fingerprint: str
    markers: frozenset[str]


def _without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _canonical_file_sha256(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise GateFailure("required-input-unavailable") from exc
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def _canonical_bytes_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_relative_migration_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise GateFailure("manifest-migration-path-invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise GateFailure("manifest-migration-path-invalid")
    if path.suffix != ".sql" or not any(path.parent == prefix for prefix in SAFE_PATH_PREFIXES):
        raise GateFailure("manifest-migration-path-outside-allowlist")
    return path


def _verified_git_bytes(path: Path, expected_commit: str) -> bytes:
    root = ROOT.resolve()
    try:
        candidate = Path(os.path.abspath(path))
        relative_path = candidate.relative_to(root)
        cursor = root
        for part in relative_path.parts:
            cursor /= part
            if cursor.is_symlink():
                raise GateFailure("git-input-invalid")
        resolved = candidate.resolve(strict=True)
        if resolved != candidate or not resolved.is_file():
            raise GateFailure("git-input-invalid")
    except GateFailure:
        raise
    except (OSError, ValueError) as exc:
        raise GateFailure("git-input-invalid") from exc

    relative = relative_path.as_posix()
    committed = _command_bytes(
        ("git", "-C", str(root), "show", f"{expected_commit}:{relative}"),
        timeout=20,
    )
    if committed.returncode != 0:
        raise GateFailure("git-input-untracked")

    tracked = _command_bytes(
        (
            "git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative,
        ),
        timeout=20,
    )
    if tracked.returncode != 0:
        raise GateFailure("git-input-untracked")

    # Compare the working tree through Git's normal clean-filter semantics.
    # This accepts a clean core.autocrlf checkout while still rejecting any
    # semantic file or mode drift.  Execution always uses the immutable blob
    # read above, so a working-tree race cannot replace the trusted input.
    drift = _command_bytes(
        (
            "git", "-C", str(root), "diff", "--quiet", "--no-ext-diff",
            "--no-textconv", expected_commit, "--", relative,
        ),
        timeout=20,
    )
    if drift.returncode == 1:
        raise GateFailure("git-input-drift")
    if drift.returncode != 0:
        raise GateFailure("git-input-unavailable")
    return committed.stdout


def load_manifest(path: Path, *, expected_commit: str | None = None) -> BootstrapManifest:
    try:
        stat = path.stat()
        if not path.is_file() or path.is_symlink() or stat.st_size <= 0 or stat.st_size > MAX_MANIFEST_BYTES:
            raise GateFailure("manifest-file-invalid")
        raw = _verified_git_bytes(path, expected_commit) if expected_commit else path.read_bytes()
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except GateFailure:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, DuplicateKeyError, ValueError) as exc:
        raise GateFailure("manifest-json-invalid") from exc

    if not isinstance(value, dict) or frozenset(value) != {
        "schema_version", "bootstrap_id", "postgres_image", "migrations"
    }:
        raise GateFailure("manifest-schema-invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise GateFailure("manifest-schema-version-invalid")
    bootstrap_id = value["bootstrap_id"]
    if not isinstance(bootstrap_id, str) or TOKEN_PATTERN.fullmatch(bootstrap_id) is None:
        raise GateFailure("manifest-bootstrap-id-invalid")
    if value["postgres_image"] != IMAGE:
        raise GateFailure("manifest-image-not-pinned")
    migration_values = value["migrations"]
    if not isinstance(migration_values, list) or not 1 <= len(migration_values) <= MAX_MIGRATIONS:
        raise GateFailure("manifest-migration-count-invalid")

    migrations: list[MigrationEntry] = []
    versions: list[str] = []
    paths: set[str] = set()
    for raw_entry in migration_values:
        if not isinstance(raw_entry, dict) or frozenset(raw_entry) != {
            "version", "path", "source", "description"
        }:
            raise GateFailure("manifest-migration-schema-invalid")
        version = raw_entry["version"]
        source = raw_entry["source"]
        description = raw_entry["description"]
        if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
            raise GateFailure("manifest-migration-version-invalid")
        if source not in SAFE_SOURCE_VALUES:
            raise GateFailure("manifest-migration-source-invalid")
        if (
            not isinstance(description, str)
            or not 1 <= len(description) <= 240
            or any(ord(character) < 32 for character in description)
        ):
            raise GateFailure("manifest-migration-description-invalid")
        relative = _safe_relative_migration_path(raw_entry["path"])
        relative_text = relative.as_posix()
        if relative_text in paths:
            raise GateFailure("manifest-migration-path-duplicate")
        candidate = ROOT / Path(*relative.parts)
        if candidate.is_symlink():
            raise GateFailure("manifest-migration-file-invalid")
        absolute = candidate.resolve(strict=False)
        try:
            absolute.relative_to(ROOT.resolve())
            file_stat = absolute.stat()
        except (OSError, ValueError) as exc:
            raise GateFailure("manifest-migration-file-unavailable") from exc
        if (
            not absolute.is_file()
            or file_stat.st_size <= 0
            or file_stat.st_size > MAX_MIGRATION_BYTES
        ):
            raise GateFailure("manifest-migration-file-invalid")
        versions.append(version)
        paths.add(relative_text)
        content = (
            _verified_git_bytes(absolute, expected_commit)
            if expected_commit
            else absolute.read_bytes()
        )
        migrations.append(
            MigrationEntry(
                version=version,
                path=relative_text,
                source=source,
                description=description,
                absolute_path=absolute,
                sha256=_canonical_bytes_sha256(content),
                content=content,
            )
        )
    if versions != sorted(versions) or len(set(versions)) != len(versions):
        raise GateFailure("manifest-migration-order-invalid")

    chain_projection = {
        "schema_version": 1,
        "bootstrap_id": bootstrap_id,
        "postgres_image": IMAGE,
        "migrations": [
            {
                "version": migration.version,
                "path": migration.path,
                "source": migration.source,
                "sha256": migration.sha256,
            }
            for migration in migrations
        ],
    }
    return BootstrapManifest(
        schema_version=1,
        bootstrap_id=bootstrap_id,
        postgres_image=IMAGE,
        sha256=hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
        chain_digest=_canonical_json_sha256(chain_projection),
        migrations=tuple(migrations),
    )


def _safe_environment() -> dict[str, str]:
    allowed = (
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "HOME", "TMP", "TEMP",
        "DOCKER_CONFIG", "DOCKER_HOST",
    )
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _command(
    args: Sequence[str],
    *,
    input_text: str | None = None,
    timeout: int = 60,
) -> CommandResult:
    try:
        completed = subprocess.run(
            list(args),
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            env=_safe_environment(),
        )
    except FileNotFoundError as exc:
        raise GateFailure("command-missing") from exc
    except subprocess.TimeoutExpired as exc:
        raise GateFailure("command-timeout") from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _command_bytes(args: Sequence[str], *, timeout: int = 60) -> BinaryCommandResult:
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=False,
            timeout=timeout,
            check=False,
            shell=False,
            env=_safe_environment(),
        )
    except FileNotFoundError as exc:
        raise GateFailure("command-missing") from exc
    except subprocess.TimeoutExpired as exc:
        raise GateFailure("command-timeout") from exc
    return BinaryCommandResult(completed.returncode, completed.stdout, completed.stderr)


def _require_success(result: CommandResult, code: str) -> str:
    if result.returncode != 0:
        raise GateFailure(code)
    return result.stdout.strip()


def _docker(*args: str, timeout: int = 60) -> CommandResult:
    return _command(("docker", *args), timeout=timeout)


def _psql(container: str, database: str, sql: str, *, timeout: int = 180) -> CommandResult:
    return _command(
        (
            "docker", "exec", "-i", container,
            "psql", "-X", "--no-psqlrc", "--quiet", "--tuples-only", "--no-align",
            "--set", "ON_ERROR_STOP=1", "--set", "VERBOSITY=terse",
            "--host", "/var/run/postgresql", "--port", "5432",
            "--username", "postgres", "--dbname", database,
        ),
        input_text=sql,
        timeout=timeout,
    )


def _tested_commit(expected_commit: str | None) -> str:
    if not isinstance(expected_commit, str) or COMMIT_PATTERN.fullmatch(expected_commit.lower()) is None:
        raise GateFailure("expected-commit-invalid")
    expected = expected_commit.lower()
    actual = _require_success(_command(("git", "rev-parse", "HEAD"), timeout=10), "git-head-unavailable").lower()
    if actual != expected or COMMIT_PATTERN.fullmatch(actual) is None:
        raise GateFailure("expected-commit-mismatch")
    return actual


def _require_canonical_manifest(path: Path) -> Path:
    try:
        canonical = DEFAULT_MANIFEST.resolve(strict=True)
        candidate = path.resolve(strict=True)
    except OSError as exc:
        raise GateFailure("manifest-file-invalid") from exc
    if path.is_symlink() or candidate != canonical:
        raise GateFailure("manifest-path-not-canonical")
    return canonical


def _wait_for_postgres(container: str, database: str, timeout_seconds: int = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ready = _docker(
            "exec", container, "pg_isready", "--host", "/var/run/postgresql", "--port", "5432",
            "--username", "postgres", "--dbname", database, timeout=10,
        )
        if ready.returncode == 0:
            probe = _psql(container, database, "SELECT 1;", timeout=10)
            if probe.returncode == 0 and probe.stdout.strip() == "1":
                return
        state = _docker("inspect", "--format", "{{.State.Running}}", container, timeout=10)
        if state.returncode != 0 or state.stdout.strip().lower() != "true":
            raise GateFailure("container-exited-before-ready")
        time.sleep(1)
    raise GateFailure("postgres-health-timeout")


def build_chain_transaction(
    manifest: BootstrapManifest,
    *,
    expected_commit: str,
) -> str:
    if not isinstance(expected_commit, str) or COMMIT_PATTERN.fullmatch(expected_commit.lower()) is None:
        raise GateFailure("expected-commit-invalid")
    commit = expected_commit.lower()
    if not all(fragment in TARGET_PREFLIGHT_SQL for fragment in TARGET_PREFLIGHT_REQUIRED_FRAGMENTS):
        raise GateFailure("target-preflight-contract-incomplete")
    statements = [
        "BEGIN;",
        f"-- phase3m expected commit {commit}",
        "SET LOCAL lock_timeout = '5s';",
        "SET LOCAL statement_timeout = '180s';",
        "SET LOCAL idle_in_transaction_session_timeout = '180s';",
        (
            "SELECT pg_catalog.pg_advisory_xact_lock("
            f"pg_catalog.hashtextextended('phase3m:{manifest.bootstrap_id}:{manifest.chain_digest}', 0));"
        ),
        TARGET_PREFLIGHT_SQL,
    ]
    for migration in manifest.migrations:
        try:
            sql = migration.content.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise GateFailure("migration-read-failed") from exc
        statements.extend(
            (
                f"-- phase3m migration {migration.version} ({migration.path})",
                sql.rstrip(),
            )
        )
    statements.extend(
        (
            "CREATE SCHEMA phase3m_internal AUTHORIZATION postgres;",
            "REVOKE ALL ON SCHEMA phase3m_internal FROM PUBLIC, anon, authenticated, service_role;",
            """
CREATE TABLE phase3m_internal.bootstrap_history (
    ordinal INTEGER PRIMARY KEY CHECK (ordinal > 0),
    version TEXT NOT NULL UNIQUE CHECK (version ~ '^[0-9]{14}$'),
    path TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL CHECK (migration_sha256 ~ '^[0-9a-f]{64}$'),
    chain_digest TEXT NOT NULL CHECK (chain_digest ~ '^[0-9a-f]{64}$'),
    bootstrap_id TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    expected_commit_sha TEXT NOT NULL CHECK (expected_commit_sha ~ '^[0-9a-f]{40}$'),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
""".strip(),
            "REVOKE ALL ON TABLE phase3m_internal.bootstrap_history FROM PUBLIC, anon, authenticated, service_role;",
        )
    )
    history_values = ",\n".join(
        "(" + ", ".join(
            (
                str(ordinal),
                f"'{migration.version}'",
                f"'{migration.path}'",
                f"'{migration.source}'",
                f"'{migration.sha256}'",
                f"'{manifest.chain_digest}'",
                f"'{manifest.bootstrap_id}'",
                f"'{manifest.sha256}'",
                f"'{commit}'",
            )
        ) + ")"
        for ordinal, migration in enumerate(manifest.migrations, start=1)
    )
    statements.append(
        """
INSERT INTO phase3m_internal.bootstrap_history (
    ordinal, version, path, source, migration_sha256,
    chain_digest, bootstrap_id, manifest_sha256, expected_commit_sha
)
VALUES
""".strip()
        + "\n"
        + history_values
        + ";"
    )
    statements.append("COMMIT;")
    return "\n".join(statements) + "\n"


def _parse_contract_output(output: str) -> DatabaseResult:
    markers: set[str] = set()
    fingerprints: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("phase3m_check:"):
            markers.add(line.removeprefix("phase3m_check:"))
        elif line.startswith("phase3m_fingerprint:"):
            fingerprints.append(line.removeprefix("phase3m_fingerprint:"))
    if markers != REQUIRED_MARKERS:
        raise GateFailure("contract-markers-incomplete")
    if len(fingerprints) != 1 or re.fullmatch(r"[0-9a-f]{64}", fingerprints[0]) is None:
        raise GateFailure("schema-fingerprint-invalid")
    return DatabaseResult(fingerprint=fingerprints[0], markers=frozenset(markers))


def _assert_fresh_database(container: str, database: str) -> None:
    output = _require_success(
        _psql(
            container,
            database,
            """
SELECT count(*)
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname IN ('public', 'auth', 'storage', 'phase3m_internal')
  AND c.relkind IN ('r', 'p', 'v', 'm', 'S');
""",
            timeout=20,
        ),
        "fresh-database-preflight-failed",
    )
    if output != "0":
        raise GateFailure("preexisting-application-object-detected")


def _verify_bootstrap_history(
    container: str,
    database: str,
    manifest: BootstrapManifest,
    expected_commit: str,
) -> None:
    output = _require_success(
        _psql(
            container,
            database,
            """
SELECT concat_ws(
    '|', ordinal::TEXT, version, path, source, migration_sha256,
    chain_digest, bootstrap_id, manifest_sha256, expected_commit_sha
)
FROM phase3m_internal.bootstrap_history
ORDER BY ordinal;
""",
            timeout=20,
        ),
        "bootstrap-history-read-failed",
    )
    actual = output.splitlines() if output else []
    expected = [
        "|".join(
            (
                str(ordinal), entry.version, entry.path, entry.source, entry.sha256,
                manifest.chain_digest, manifest.bootstrap_id, manifest.sha256,
                expected_commit,
            )
        )
        for ordinal, entry in enumerate(manifest.migrations, start=1)
    ]
    if actual != expected:
        raise GateFailure("bootstrap-history-mismatch")


def _apply_fresh_database(
    container: str,
    database: str,
    manifest: BootstrapManifest,
    prelude_sql: str,
    contract_sql: str,
    chain_sql: str,
    expected_commit: str,
) -> DatabaseResult:
    _assert_fresh_database(container, database)
    _require_success(_psql(container, database, prelude_sql), "supabase-prelude-failed")
    _require_success(
        _psql(container, database, chain_sql, timeout=300),
        "manifest-chain-apply-failed",
    )
    _verify_bootstrap_history(container, database, manifest, expected_commit)
    output = _require_success(
        _psql(container, database, contract_sql, timeout=180),
        "bootstrap-contract-failed",
    )
    return _parse_contract_output(output)


def _container_absent(name: str) -> bool:
    result = _docker(
        "ps", "--all", "--filter", f"name=^/{name}$", "--format", "{{.Names}}", timeout=15,
    )
    return result.returncode == 0 and result.stdout.strip() == ""


def _read_required_sql(path: Path, *, expected_commit: str | None = None) -> tuple[str, str]:
    try:
        stat = path.stat()
        if not path.is_file() or path.is_symlink() or stat.st_size <= 0 or stat.st_size > MAX_MIGRATION_BYTES:
            raise GateFailure("contract-input-invalid")
        raw = _verified_git_bytes(path, expected_commit) if expected_commit else path.read_bytes()
        return raw.decode("utf-8", errors="strict"), _canonical_bytes_sha256(raw)
    except GateFailure:
        raise
    except (OSError, UnicodeError) as exc:
        raise GateFailure("contract-input-unavailable") from exc


def _empty_checks() -> dict[str, bool]:
    return {
        "manifest_valid": False,
        "image_pinned": False,
        "transaction_target_preflight_embedded": False,
        "local_docker_context": False,
        "network_isolated": False,
        "database_credentials_randomized": False,
        "first_target_fresh": False,
        "first_fresh_chain_applied": False,
        "first_bootstrap_history_verified": False,
        "first_contract_passed": False,
        "second_target_fresh": False,
        "second_fresh_chain_applied": False,
        "second_bootstrap_history_verified": False,
        "second_contract_passed": False,
        "fresh_database_replay": False,
        "schema_fingerprint_match": False,
        "markers_complete": False,
    }


def _write_report(report_path: Path, report: dict[str, Any], *, forbidden_values: Sequence[str] = ()) -> None:
    def contains_unsafe(value: Any) -> bool:
        if isinstance(value, dict):
            if any(str(key).lower() in {"dsn", "password", "raw_rows"} for key in value):
                return True
            return any(contains_unsafe(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains_unsafe(item) for item in value)
        if isinstance(value, str):
            lowered_value = value.lower()
            return (
                "postgresql://" in lowered_value
                or re.match(r"^[a-z]:[\\/]", value, re.IGNORECASE) is not None
                or value.startswith(("/", "\\\\", "file://"))
            )
        return False

    if contains_unsafe(report):
        raise GateFailure("report-sanitization-failed")
    payload = json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n"
    if (
        any(value and value in payload for value in forbidden_values)
    ):
        raise GateFailure("report-sanitization-failed")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_name(f".{report_path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, report_path)
    finally:
        temporary.unlink(missing_ok=True)


def run_gate(
    *,
    manifest_path: Path,
    expected_commit: str | None,
    report_path: Path = REPORT,
) -> int:
    checks = _empty_checks()
    cleanup = {"attempted": False, "container_absent": False, "workspace_absent": False}
    failure_code: str | None = None
    manifest: BootstrapManifest | None = None
    commit = ""
    prelude_hash = ""
    contract_hash = ""
    fingerprints: list[str] = []
    chain_sql = ""
    container = f"keiba-phase3m-{secrets.token_hex(8)}"
    first_database = f"phase3m_a_{secrets.token_hex(6)}"
    second_database = f"phase3m_b_{secrets.token_hex(6)}"
    password = secrets.token_urlsafe(32)
    workspace: Path | None = None
    local_context_confirmed = False
    docker_environment = ExitStack()

    try:
        canonical_manifest = _require_canonical_manifest(manifest_path)
        commit = _tested_commit(expected_commit)
        manifest = load_manifest(canonical_manifest, expected_commit=commit)
        checks["manifest_valid"] = True
        checks["image_pinned"] = manifest.postgres_image == IMAGE
        if not checks["image_pinned"]:
            raise GateFailure("image-not-pinned")
        prelude_sql, prelude_hash = _read_required_sql(PRELUDE, expected_commit=commit)
        contract_sql, contract_hash = _read_required_sql(CONTRACT, expected_commit=commit)
        chain_sql = build_chain_transaction(manifest, expected_commit=commit)
        checks["transaction_target_preflight_embedded"] = (
            "phase3m_target_preflight" in chain_sql
            and "target already contains bootstrap application objects" in chain_sql
        )
        if not checks["transaction_target_preflight_embedded"]:
            raise GateFailure("transaction-preflight-missing")

        workspace = Path(tempfile.mkdtemp(prefix="phase3m-bootstrap-"))
        cleanup["attempted"] = True
        docker_config = workspace / "docker-config"
        docker_home = workspace / "docker-home"
        docker_config.mkdir(mode=0o700)
        docker_home.mkdir(mode=0o700)
        (docker_config / "config.json").write_text("{}\n", encoding="utf-8")
        docker_environment.enter_context(
            patch.dict(
                os.environ,
                {"DOCKER_CONFIG": str(docker_config), "HOME": str(docker_home)},
                clear=False,
            )
        )

        endpoint = _require_success(
            _docker("context", "inspect", "--format", '{{(index .Endpoints "docker").Host}}', timeout=20),
            "docker-context-unavailable",
        )
        if not endpoint.startswith(LOCAL_DOCKER_ENDPOINTS):
            raise GateFailure("remote-docker-context-rejected")
        local_context_confirmed = True
        checks["local_docker_context"] = True
        _require_success(_docker("version", "--format", "{{.Server.Version}}", timeout=20), "docker-unavailable")
        _require_success(_docker("pull", IMAGE, timeout=240), "docker-image-unavailable")

        started = _require_success(
            _docker(
                "run", "--detach", "--name", container,
                "--network", "none", "--pull", "never",
                "--label", "keiba-ai-pro.phase3m-bootstrap=true",
                "--env", f"POSTGRES_DB={first_database}",
                "--env", "POSTGRES_USER=postgres",
                "--env", f"POSTGRES_PASSWORD={password}",
                IMAGE,
                timeout=40,
            ),
            "container-start-failed",
        )
        if CONTAINER_ID_PATTERN.fullmatch(started) is None:
            raise GateFailure("container-id-invalid")
        checks["database_credentials_randomized"] = (
            len(password) >= 32 and first_database != second_database and container not in {first_database, second_database}
        )
        if not checks["database_credentials_randomized"]:
            raise GateFailure("database-randomization-failed")
        network = _require_success(
            _docker("inspect", "--format", "{{.HostConfig.NetworkMode}}|{{json .NetworkSettings.Ports}}", container, timeout=20),
            "container-network-inspection-failed",
        )
        checks["network_isolated"] = network in {"none|null", "none|{}"}
        if not checks["network_isolated"]:
            raise GateFailure("container-network-not-isolated")
        _wait_for_postgres(container, first_database)

        first = _apply_fresh_database(
            container, first_database, manifest, prelude_sql, contract_sql, chain_sql, commit
        )
        fingerprints.append(first.fingerprint)
        checks["first_target_fresh"] = True
        checks["first_fresh_chain_applied"] = True
        checks["first_bootstrap_history_verified"] = True
        checks["first_contract_passed"] = first.markers == REQUIRED_MARKERS

        if re.fullmatch(r"phase3m_b_[0-9a-f]{12}", second_database) is None:
            raise GateFailure("second-database-name-invalid")
        _require_success(
            _psql(container, "postgres", f'CREATE DATABASE "{second_database}" TEMPLATE template0 ENCODING \'UTF8\';'),
            "second-database-create-failed",
        )
        second = _apply_fresh_database(
            container, second_database, manifest, prelude_sql, contract_sql, chain_sql, commit
        )
        fingerprints.append(second.fingerprint)
        checks["second_target_fresh"] = True
        checks["second_fresh_chain_applied"] = True
        checks["second_bootstrap_history_verified"] = True
        checks["second_contract_passed"] = second.markers == REQUIRED_MARKERS
        checks["fresh_database_replay"] = True
        checks["schema_fingerprint_match"] = first.fingerprint == second.fingerprint
        checks["markers_complete"] = first.markers == second.markers == REQUIRED_MARKERS
        if not all(checks.values()):
            raise GateFailure("bootstrap-check-incomplete")
    except GateFailure as exc:
        failure_code = exc.code
    except Exception:
        failure_code = "unexpected-gate-failure"
    finally:
        if local_context_confirmed:
            try:
                _docker("rm", "--force", container, timeout=40)
            except GateFailure:
                pass
            try:
                cleanup["container_absent"] = _container_absent(container)
            except GateFailure:
                cleanup["container_absent"] = False
        docker_environment.close()
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)
            cleanup["workspace_absent"] = not workspace.exists()
        else:
            cleanup["workspace_absent"] = True
        if failure_code is None and not all(cleanup.values()):
            failure_code = "cleanup-failed"

    success = failure_code is None and all(checks.values()) and all(cleanup.values())
    manifest_report: dict[str, Any] = {
        "schema_version": manifest.schema_version if manifest else None,
        "bootstrap_id": manifest.bootstrap_id if manifest else None,
        "manifest_sha256": manifest.sha256 if manifest else None,
        "chain_digest": manifest.chain_digest if manifest else None,
        "migration_count": len(manifest.migrations) if manifest else 0,
        "migrations": [
            {
                "version": entry.version,
                "path": entry.path,
                "source": entry.source,
                "sha256": entry.sha256,
            }
            for entry in (manifest.migrations if manifest else ())
        ],
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "evidence_mode": "synthetic",
        "environment": "ci-disposable",
        "database_scope": "two-independent-fresh-disposable-databases",
        "network_mode": "none",
        "image": IMAGE,
        "host_port_published": False,
        "external_credentials_used": False,
        "tested_commit_sha": commit or None,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "success": bool(success),
        "production_ready": False,
        "l3_eligible": False,
        "external_migration_applied": False,
        "manifest": manifest_report,
        "prelude_sha256": prelude_hash or None,
        "contract_sha256": contract_hash or None,
        "replay": {
            "mode": "same-chain-two-fresh-databases",
            "database_count": 2 if len(fingerprints) == 2 else len(fingerprints),
            "schema_fingerprints": fingerprints,
            "matched": len(fingerprints) == 2 and len(set(fingerprints)) == 1,
        },
        "checks": checks,
        "cleanup": cleanup,
        "failure_code": failure_code,
    }
    try:
        _write_report(
            report_path,
            report,
            forbidden_values=(password, container, first_database, second_database, str(workspace or "")),
        )
    except GateFailure:
        return 1
    return 0 if success else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Phase 3M disposable Supabase bootstrap gate.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--expected-commit", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_gate(manifest_path=args.manifest, expected_commit=args.expected_commit)


if __name__ == "__main__":
    raise SystemExit(main())

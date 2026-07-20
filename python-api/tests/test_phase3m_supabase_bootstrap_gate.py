from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "security" / "run_phase3m_supabase_bootstrap_gate.py"
MANIFEST_PATH = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
PRELUDE_PATH = ROOT / "supabase" / "bootstrap" / "v1" / "tests" / "supabase_prelude.sql"
CONTRACT_PATH = ROOT / "supabase" / "bootstrap" / "v1" / "tests" / "bootstrap_contract.sql"
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
EXPECTED_IMAGE = "postgres:17.6-bookworm@sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3"


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase3m_bootstrap_gate", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def runner() -> ModuleType:
    return _load_runner()


def _write_manifest(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def test_manifest_is_strict_ordered_and_content_addressed(runner: ModuleType) -> None:
    manifest = runner.load_manifest(MANIFEST_PATH)
    assert manifest.schema_version == 1
    assert manifest.postgres_image == EXPECTED_IMAGE == runner.IMAGE
    assert len(manifest.migrations) == 11
    assert [entry.version for entry in manifest.migrations] == sorted(
        entry.version for entry in manifest.migrations
    )
    assert len({entry.path for entry in manifest.migrations}) == len(manifest.migrations)
    assert re.fullmatch(r"[0-9a-f]{64}", manifest.sha256)
    assert re.fullmatch(r"[0-9a-f]{64}", manifest.chain_digest)
    assert all(re.fullmatch(r"[0-9a-f]{64}", entry.sha256) for entry in manifest.migrations)


def test_gate_accepts_only_the_canonical_manifest_path(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    assert runner._require_canonical_manifest(MANIFEST_PATH) == MANIFEST_PATH.resolve()
    copy = tmp_path / "manifest.json"
    copy.write_bytes(MANIFEST_PATH.read_bytes())
    with pytest.raises(runner.GateFailure, match="manifest-path-not-canonical"):
        runner._require_canonical_manifest(copy)


def test_gate_rejects_an_alternate_manifest_before_commit_or_docker_use(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    alternate = tmp_path / "manifest.json"
    alternate.write_bytes(MANIFEST_PATH.read_bytes())
    monkeypatch.setattr(
        runner,
        "_tested_commit",
        lambda _value: pytest.fail("commit validation must not run for an alternate manifest"),
    )
    monkeypatch.setattr(
        runner,
        "_docker",
        lambda *_args, **_kwargs: pytest.fail("Docker must not run for an alternate manifest"),
    )
    report_path = tmp_path / "report.json"

    assert runner.run_gate(
        manifest_path=alternate,
        expected_commit="a" * 40,
        report_path=report_path,
    ) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["success"] is False
    assert report["failure_code"] == "manifest-path-not-canonical"


@pytest.mark.parametrize(
    ("mutation", "failure_code"),
    [
        (lambda value: value.update(postgres_image="postgres:17.6-bookworm"), "manifest-image-not-pinned"),
        (
            lambda value: value["migrations"][0].update(path="../secrets.sql"),
            "manifest-migration-path-invalid",
        ),
        (
            lambda value: value["migrations"][0].update(path="scripts/not-a-bootstrap.sql"),
            "manifest-migration-path-outside-allowlist",
        ),
        (
            lambda value: value["migrations"].reverse(),
            "manifest-migration-order-invalid",
        ),
    ],
)
def test_manifest_tampering_fails_closed(
    runner: ModuleType,
    tmp_path: Path,
    mutation: Any,
    failure_code: str,
) -> None:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    mutation(value)
    path = tmp_path / "manifest.json"
    _write_manifest(path, value)
    with pytest.raises(runner.GateFailure) as caught:
        runner.load_manifest(path)
    assert caught.value.code == failure_code


def test_manifest_duplicate_json_key_fails_closed(runner: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"schema_version":1,"schema_version":1,"bootstrap_id":"phase3m-test",'
        f'"postgres_image":"{EXPECTED_IMAGE}","migrations":[]}}\n',
        encoding="utf-8",
    )
    with pytest.raises(runner.GateFailure) as caught:
        runner.load_manifest(path)
    assert caught.value.code == "manifest-json-invalid"


def test_chain_is_one_transaction_with_timeouts_lock_and_manifest_order(runner: ModuleType) -> None:
    manifest = runner.load_manifest(MANIFEST_PATH)
    commit = "a" * 40
    sql = runner.build_chain_transaction(manifest, expected_commit=commit)
    assert sql.startswith("BEGIN;\n")
    assert sql.rstrip().endswith("COMMIT;")
    assert "SET LOCAL lock_timeout = '5s';" in sql
    assert "SET LOCAL statement_timeout = '180s';" in sql
    assert "SET LOCAL idle_in_transaction_session_timeout = '180s';" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "phase3m_target_preflight" in sql
    assert "target already contains bootstrap application objects" in sql
    assert all(fragment in sql for fragment in runner.TARGET_PREFLIGHT_REQUIRED_FRAGMENTS)
    assert sql.index("$phase3m_target_preflight$") < sql.index("phase3m migration")
    assert "CREATE TABLE phase3m_internal.bootstrap_history" in sql
    assert f"-- phase3m expected commit {commit}" in sql
    assert "expected_commit_sha TEXT NOT NULL" in sql
    assert "REVOKE ALL ON TABLE phase3m_internal.bootstrap_history" in sql
    assert sql.count("INSERT INTO phase3m_internal.bootstrap_history") == 1
    assert all(entry.sha256 in sql for entry in manifest.migrations)
    assert sql.count(f"'{commit}'") == len(manifest.migrations)
    positions = [sql.index(f"phase3m migration {entry.version}") for entry in manifest.migrations]
    assert positions == sorted(positions)


def test_target_preflight_rejects_partial_hosted_bootstrap_signatures(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preflight = runner.TARGET_PREFLIGHT_SQL
    for token in (
        "c.relkind = 'S'",
        "c.relname = 'scrape_execution_reservation_fencing_seq'",
        "'consume_ocr_quota'",
        "'update_admin_profile_role'",
        "'admin_role_change_audit'",
        "FROM storage.buckets AS b",
        "b.id = 'models' OR b.name = 'models'",
        "FROM storage.objects AS o",
        "o.bucket_id = 'models'",
        "FROM pg_catalog.pg_policy AS pol",
        "pol.polname = 'phase3m_models_browser_deny'",
        "n.nspname = 'auth' AND c.relname = 'users' AND t.tgname = 'on_auth_user_created'",
    ):
        assert token in preflight
    assert re.search(
        r"OR EXISTS \(\s*SELECT 1\s*"
        r"FROM pg_catalog\.pg_class AS c\s*"
        r"JOIN pg_catalog\.pg_namespace AS n ON n\.oid = c\.relnamespace\s*"
        r"WHERE n\.nspname = 'public'\s*"
        r"AND c\.relkind = 'S'\s*"
        r"AND c\.relname = 'scrape_execution_reservation_fencing_seq'\s*\)",
        preflight,
    )
    assert "to_regnamespace('auth')" not in preflight
    assert "to_regnamespace('storage')" not in preflight

    monkeypatch.setattr(runner, "TARGET_PREFLIGHT_SQL", "DO $$ BEGIN NULL; END $$;")
    with pytest.raises(runner.GateFailure, match="target-preflight-contract-incomplete"):
        runner.build_chain_transaction(
            runner.load_manifest(MANIFEST_PATH),
            expected_commit="a" * 40,
        )


def test_target_preflight_contract_rejects_missing_standalone_fencing_sequence_signature(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sequence_fragment = runner.FENCING_SEQUENCE_PREFLIGHT_FRAGMENT
    assert sequence_fragment in runner.TARGET_PREFLIGHT_REQUIRED_FRAGMENTS
    assert sequence_fragment in runner.TARGET_PREFLIGHT_SQL

    monkeypatch.setattr(
        runner,
        "TARGET_PREFLIGHT_SQL",
        runner.TARGET_PREFLIGHT_SQL.replace(sequence_fragment, "c.relname = 'other_sequence'"),
    )
    with pytest.raises(runner.GateFailure, match="target-preflight-contract-incomplete"):
        runner.build_chain_transaction(
            runner.load_manifest(MANIFEST_PATH),
            expected_commit="a" * 40,
        )


def test_chain_uses_retained_verified_bytes_not_a_second_file_read(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    manifest = runner.load_manifest(MANIFEST_PATH)
    missing = tmp_path / "removed-after-validation.sql"
    first = replace(
        manifest.migrations[0],
        absolute_path=missing,
        content=b"SELECT 'retained-phase3m-bytes';\n",
    )
    retained = replace(manifest, migrations=(first, *manifest.migrations[1:]))
    sql = runner.build_chain_transaction(retained, expected_commit="a" * 40)
    assert "SELECT 'retained-phase3m-bytes';" in sql


def test_supabase_prelude_is_minimal_and_compatible() -> None:
    sql = PRELUDE_PATH.read_text(encoding="utf-8")
    for token in (
        "CREATE EXTENSION IF NOT EXISTS pgcrypto",
        'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"',
        "CREATE TABLE auth.users",
        "CREATE FUNCTION auth.uid()",
        "CREATE FUNCTION auth.role()",
        "CREATE FUNCTION auth.jwt()",
        "CREATE TABLE storage.buckets",
        "CREATE TABLE storage.objects",
        "REVOKE ALL ON TABLE auth.users, storage.buckets, storage.objects",
        "REVOKE CREATE ON SCHEMA extensions",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE storage.objects TO anon, authenticated",
    ):
        assert token in sql
    assert "CREATE TABLE public.profiles" not in sql


def test_contract_asserts_security_and_domain_invariants() -> None:
    sql = CONTRACT_PATH.read_text(encoding="utf-8")
    for marker in (
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
    ):
        assert f"phase3m_check:{marker}" in sql
    assert "has_function_privilege('authenticated', p.oid, 'EXECUTE')" in sql
    assert "authenticated SECURITY DEFINER execution detected" in sql
    assert "SET LOCAL ROLE authenticated" in sql
    assert "SET LOCAL ROLE service_role" in sql
    assert "phase3m_internal.bootstrap_history" in sql
    assert "expected_commit_sha" in sql
    assert "pol.polname = 'phase3m_models_browser_deny'" in sql
    assert "phase3m private model storage policy contract failed" in sql
    assert "CREATE DATABASE" not in sql
    assert "phase3m_fingerprint:" in sql
    assert sql.rstrip().endswith("ROLLBACK;")


def test_contract_output_requires_exact_markers_and_one_fingerprint(runner: ModuleType) -> None:
    fingerprint = "a" * 64
    output = "\n".join(
        [*(f"phase3m_check:{key}" for key in sorted(runner.REQUIRED_MARKERS)), f"phase3m_fingerprint:{fingerprint}"]
    )
    result = runner._parse_contract_output(output)
    assert result.fingerprint == fingerprint
    assert result.markers == runner.REQUIRED_MARKERS
    with pytest.raises(runner.GateFailure, match="contract-markers-incomplete"):
        runner._parse_contract_output(f"phase3m_fingerprint:{fingerprint}")
    with pytest.raises(runner.GateFailure, match="schema-fingerprint-invalid"):
        runner._parse_contract_output(output + f"\nphase3m_fingerprint:{fingerprint}")


def test_report_writer_rejects_credentials_dsn_and_absolute_paths(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.json"
    runner._write_report(report, {"success": True, "path": "supabase/bootstrap/v1/manifest.json"})
    assert json.loads(report.read_text(encoding="utf-8"))["success"] is True
    for unsafe in (
        {"dsn": "redacted"},
        {"url": "postgresql://user:secret@example.invalid/db"},
        {"path": str(ROOT)},
    ):
        with pytest.raises(runner.GateFailure, match="report-sanitization-failed"):
            runner._write_report(report, unsafe)


def test_git_bound_input_rejects_untracked_or_dirty_bytes(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual = MANIFEST_PATH.read_bytes()
    monkeypatch.setattr(
        runner,
        "_command_bytes",
        lambda *_args, **_kwargs: runner.BinaryCommandResult(0, actual, b""),
    )
    assert runner._verified_git_bytes(MANIFEST_PATH, "a" * 40) == actual
    monkeypatch.setattr(
        runner,
        "_command_bytes",
        lambda *_args, **_kwargs: runner.BinaryCommandResult(0, actual + b"drift", b""),
    )
    with pytest.raises(runner.GateFailure, match="git-input-drift"):
        runner._verified_git_bytes(MANIFEST_PATH, "a" * 40)
    monkeypatch.setattr(
        runner,
        "_command_bytes",
        lambda *_args, **_kwargs: runner.BinaryCommandResult(128, b"", b"missing"),
    )
    with pytest.raises(runner.GateFailure, match="git-input-untracked"):
        runner._verified_git_bytes(MANIFEST_PATH, "a" * 40)


def test_fresh_database_preflight_rejects_existing_objects(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner,
        "_psql",
        lambda *_args, **_kwargs: runner.CommandResult(0, "1\n", ""),
    )
    with pytest.raises(runner.GateFailure, match="preexisting-application-object-detected"):
        runner._assert_fresh_database("container", "database")


def _exercise_mocked_gate(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    fail_apply: bool,
) -> tuple[int, dict[str, Any], list[tuple[str, ...]], list[str]]:
    manifest = runner.load_manifest(MANIFEST_PATH)
    docker_calls: list[tuple[str, ...]] = []
    databases: list[str] = []

    def fake_docker(*args: str, timeout: int = 60) -> Any:
        del timeout
        docker_calls.append(tuple(args))
        if args[:2] == ("context", "inspect"):
            return runner.CommandResult(0, "unix:///var/run/docker.sock\n", "")
        if args[0] == "run":
            return runner.CommandResult(0, "a" * 64 + "\n", "")
        if args[:2] == ("inspect", "--format") and args[2].startswith("{{.HostConfig"):
            return runner.CommandResult(0, "none|null\n", "")
        return runner.CommandResult(0, "24.0\n", "")

    def fake_apply(
        _container: str,
        database: str,
        _manifest: Any,
        _prelude_sql: str,
        _contract_sql: str,
        _chain_sql: str,
        expected_commit: str,
    ) -> Any:
        assert expected_commit == "c" * 40
        databases.append(database)
        if fail_apply:
            raise runner.GateFailure("synthetic-apply-failure")
        return runner.DatabaseResult("b" * 64, runner.REQUIRED_MARKERS)

    monkeypatch.setattr(runner, "_tested_commit", lambda expected: "c" * 40)
    monkeypatch.setattr(runner, "load_manifest", lambda _path, **_kwargs: manifest)
    monkeypatch.setattr(runner, "_read_required_sql", lambda _path, **_kwargs: ("SELECT 1;", "d" * 64))
    monkeypatch.setattr(runner, "_docker", fake_docker)
    monkeypatch.setattr(runner, "_wait_for_postgres", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_psql", lambda *_args, **_kwargs: runner.CommandResult(0, "", ""))
    monkeypatch.setattr(runner, "_apply_fresh_database", fake_apply)
    monkeypatch.setattr(runner, "_container_absent", lambda _name: True)
    report_path = tmp_path / "phase3m-report.json"
    code = runner.run_gate(
        manifest_path=MANIFEST_PATH,
        expected_commit="c" * 40,
        report_path=report_path,
    )
    return code, json.loads(report_path.read_text(encoding="utf-8")), docker_calls, databases


def test_gate_uses_network_none_two_fresh_databases_and_sanitized_evidence(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    code, report, docker_calls, databases = _exercise_mocked_gate(
        runner, monkeypatch, tmp_path, fail_apply=False
    )
    assert code == 0
    assert report["success"] is True
    assert report["evidence_mode"] == "synthetic"
    assert report["network_mode"] == "none"
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False
    assert report["replay"] == {
        "mode": "same-chain-two-fresh-databases",
        "database_count": 2,
        "schema_fingerprints": ["b" * 64, "b" * 64],
        "matched": True,
    }
    assert len(databases) == 2 and databases[0] != databases[1]
    run_call = next(call for call in docker_calls if call[0] == "run")
    assert "--network" in run_call and run_call[run_call.index("--network") + 1] == "none"
    assert not any(argument in {"-p", "--publish", "--publish-all"} for argument in run_call)
    serialized = json.dumps(report)
    assert all(database not in serialized for database in databases)
    assert "POSTGRES_PASSWORD" not in serialized and "postgresql://" not in serialized
    assert report["cleanup"] == {"attempted": True, "container_absent": True, "workspace_absent": True}


def test_gate_failure_still_removes_container_and_fails_closed(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    code, report, docker_calls, databases = _exercise_mocked_gate(
        runner, monkeypatch, tmp_path, fail_apply=True
    )
    assert code == 1
    assert report["success"] is False
    assert report["failure_code"] == "synthetic-apply-failure"
    assert report["cleanup"]["container_absent"] is True
    assert len(databases) == 1
    assert any(call[0:2] == ("rm", "--force") for call in docker_calls)


def test_ci_keeps_phase3m_gate_inside_existing_container_job() -> None:
    ci = CI_PATH.read_text(encoding="utf-8")
    container_start = ci.index("  container-build-check:")
    container_section = ci[container_start:]
    assert "python-api/tests/test_phase3m_supabase_bootstrap_gate.py" in container_section
    assert "python-api/tests/test_phase3m_admin_atomic_role.py" in container_section
    assert "python-api/tests/test_phase3m_ocr_atomic_quota.py" in container_section
    assert "scripts/security/run_phase3m_supabase_bootstrap_gate.py" in container_section
    assert '--expected-commit "$GITHUB_SHA"' in container_section
    assert "reports/phase3m_supabase_bootstrap_gate.json" in container_section
    assert "phase3m-supabase-bootstrap-json" in container_section
    assert "if: always()" in container_section
    assert "if-no-files-found: error" in container_section
    assert "\n  phase3m-supabase-bootstrap" not in ci

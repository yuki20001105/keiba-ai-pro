from __future__ import annotations

import importlib.util
import json
import multiprocessing
import os
import subprocess
import sys
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "security" / "run_phase3j_saga_outbox_runtime_gate.py"
CONTRACT = ROOT / "python-api" / "tests" / "fixtures" / "phase3j_saga_outbox_failure_matrix_v1.json"
MIGRATION = ROOT / "supabase" / "migrations" / "20260720_scrape_execution_reservation.sql"
RUNTIME_DIR = ROOT / "python-api" / "scraping"

SPEC = importlib.util.spec_from_file_location("phase3j_runtime_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)
TESTED_COMMIT = runner._repository_head(ROOT)


def _sqlite_success(_workspace: Path, _runtime_dir: Path, _effects) -> tuple[dict[str, bool], dict[str, bool], int]:
    return (
        {key: True for key in runner._load_module(runner.VERIFIER_PATH, "phase3j_test_verifier").SCENARIO_KEYS if key.startswith("sqlite_")},
        {
            "atomic_prepare": True,
            "stable_operation_job_binding": True,
            "replay_idempotency": True,
            "single_claim_winner": True,
            "lease_fencing": True,
            "stale_ack_rejected": True,
            "ambiguous_remote_no_dispatch": True,
            "compensation_idempotent": True,
            "corruption_unavailable_fail_closed": True,
            "zero_operational_effects": True,
        },
        20,
    )


def _postgres_success(_container: str, _migration: Path) -> tuple[dict[str, bool], dict[str, bool], int]:
    return (
        {key: True for key in runner._load_module(runner.VERIFIER_PATH, "phase3j_test_verifier_pg").SCENARIO_KEYS if key.startswith("postgres_")},
        {
            "review_approval_not_execution_authority": True,
            "service_role_only_reservation": True,
        },
        17,
    )


def _report(tmp_path: Path, **kwargs: object) -> tuple[int, dict]:
    report = tmp_path / "phase3j-runtime.json"
    code = runner.run_gate(
        expected_commit=kwargs.pop("expected_commit", TESTED_COMMIT),
        contract_path=kwargs.pop("contract_path", CONTRACT),
        migration_path=kwargs.pop("migration_path", MIGRATION),
        runtime_dir=kwargs.pop("runtime_dir", RUNTIME_DIR),
        report_path=report,
        sqlite_runner=kwargs.pop("sqlite_runner", _sqlite_success),
        postgres_runner=kwargs.pop("postgres_runner", _postgres_success),
        docker_enabled=kwargs.pop("docker_enabled", False),
        **kwargs,
    )
    return code, json.loads(report.read_text(encoding="utf-8"))


def test_injected_disposable_contract_produces_strict_not_ready_evidence(tmp_path: Path) -> None:
    verifier = runner._load_module(runner.VERIFIER_PATH, "phase3j_test_expected")
    code, report = _report(tmp_path)
    assert code == 0
    assert report["success"] is True
    assert frozenset(report) == verifier.EVIDENCE_KEYS
    assert report["evidence_mode"] == "disposable-runtime"
    assert report["host_port_published"] is False
    assert report["external_credentials_used"] is False
    assert report["external_migration_applied"] is False
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False
    assert report["operational_effect_count"] == 0
    assert report["worker_dispatch_count"] == 0
    assert report["disposable_database_effect_count"] == 37
    assert report["disposable_database_effects"] == {"sqlite_writes": 20, "postgres_writes": 17}
    assert all(report["scenario_checks"].values())
    assert all(report["invariant_checks"].values())
    assert report["cleanup"] == {"attempted": True, "container_absent": True, "workspace_absent": True}


def test_actual_sqlite_runtime_executes_all_disposable_scenarios(tmp_path: Path) -> None:
    code, report = _report(tmp_path, sqlite_runner=runner._run_sqlite_contract)
    assert code == 0
    assert report["success"] is True
    sqlite_checks = {
        key: value for key, value in report["scenario_checks"].items() if key.startswith("sqlite_")
    }
    assert len(sqlite_checks) == 8
    assert all(sqlite_checks.values())
    assert report["disposable_database_effects"]["sqlite_writes"] > 0
    assert report["operational_effect_count"] == 0
    assert report["worker_dispatch_count"] == 0


def test_worker_dispatch_attempt_fails_closed_and_is_counted_separately(tmp_path: Path) -> None:
    def dispatch(_workspace: Path, _runtime_dir: Path, effects):
        effects.worker_dispatch += 1
        return _sqlite_success(_workspace, _runtime_dir, effects)

    code, report = _report(tmp_path, sqlite_runner=dispatch)
    assert code == 1
    assert report["success"] is False
    assert report["worker_dispatch_count"] == 1
    assert report["operational_effect_count"] == 1
    assert report["disposable_database_effect_count"] == 37
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False


def test_operational_write_outside_workspace_is_blocked_and_not_created(tmp_path: Path) -> None:
    target = tmp_path / "forbidden.txt"

    def write_outside(_workspace: Path, _runtime_dir: Path, _effects):
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("forbidden")
        raise AssertionError("guard failed")

    code, report = _report(tmp_path, sqlite_runner=write_outside)
    assert code == 1
    assert report["success"] is False
    assert report["operational_effects"]["operational_write"] == 1
    assert target.exists() is False
    assert report["cleanup"]["workspace_absent"] is True


def test_directory_creation_outside_workspace_is_blocked_and_counted(tmp_path: Path) -> None:
    target = tmp_path / "forbidden-directory"

    def create_directory(_workspace: Path, _runtime_dir: Path, _effects):
        os.mkdir(target)
        raise AssertionError("directory mutation guard failed")

    code, report = _report(tmp_path, sqlite_runner=create_directory)
    assert code == 1
    assert target.exists() is False
    assert report["operational_effects"]["operational_write"] == 1


def test_sqlite_database_outside_workspace_is_blocked_and_not_created(tmp_path: Path) -> None:
    target = tmp_path / "forbidden.sqlite3"

    def create_database(_workspace: Path, _runtime_dir: Path, _effects):
        sqlite3.connect(target)
        raise AssertionError("sqlite boundary guard failed")

    code, report = _report(tmp_path, sqlite_runner=create_database)
    assert code == 1
    assert target.exists() is False
    assert report["operational_effects"]["operational_write"] == 1


@pytest.mark.parametrize("process_api", ["subprocess-run", "subprocess-popen", "os-system"])
def test_process_dispatch_attempt_is_blocked_and_counted(tmp_path: Path, process_api: str) -> None:
    def dispatch(_workspace: Path, _runtime_dir: Path, _effects):
        if process_api == "subprocess-run":
            subprocess.run([sys.executable, "-c", "pass"], check=False)
        elif process_api == "subprocess-popen":
            subprocess.Popen([sys.executable, "-c", "pass"])
        else:
            os.system("echo phase3j-guard-probe")
        raise AssertionError("process guard failed")

    code, report = _report(tmp_path, sqlite_runner=dispatch)
    assert code == 1
    assert report["success"] is False
    assert report["worker_dispatch_count"] == 1
    assert report["operational_effect_count"] == 1
    assert report["cleanup"]["workspace_absent"] is True


@pytest.mark.parametrize(
    "process_api",
    ["fork", "forkpty", "posix_spawn", "posix_spawnp"],
)
def test_low_level_process_dispatch_is_blocked_when_platform_exposes_it(
    tmp_path: Path, process_api: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Supply a harmless platform stand-in on Windows; Linux exercises the real
    # capability name, which the guard replaces before this call can dispatch.
    if not hasattr(os, process_api):
        monkeypatch.setattr(os, process_api, lambda *_args, **_kwargs: None, raising=False)

    def dispatch(_workspace: Path, _runtime_dir: Path, _effects):
        getattr(os, process_api)()
        raise AssertionError("low-level process guard failed")

    code, report = _report(tmp_path, sqlite_runner=dispatch)
    assert code == 1
    assert report["worker_dispatch_count"] == 1
    assert report["operational_effect_count"] == 1


def test_multiprocessing_start_is_blocked_and_counted(tmp_path: Path) -> None:
    def dispatch(_workspace: Path, _runtime_dir: Path, _effects):
        multiprocessing.Process(target=lambda: None).start()
        raise AssertionError("multiprocessing guard failed")

    code, report = _report(tmp_path, sqlite_runner=dispatch)
    assert code == 1
    assert report["worker_dispatch_count"] == 1
    assert report["operational_effect_count"] == 1


def test_docker_client_credentials_are_isolated_inside_disposable_workspace(tmp_path: Path) -> None:
    observed: dict[str, str] = {}

    def inspect_environment(workspace: Path, runtime_dir: Path, effects):
        docker_config = Path(os.environ["DOCKER_CONFIG"])
        docker_home = Path(os.environ["HOME"])
        docker_config.resolve().relative_to(workspace.resolve())
        docker_home.resolve().relative_to(workspace.resolve())
        observed["config"] = (docker_config / "config.json").read_text(encoding="utf-8")
        return _sqlite_success(workspace, runtime_dir, effects)

    code, report = _report(tmp_path, sqlite_runner=inspect_environment)
    assert code == 0
    assert report["external_credentials_used"] is False
    assert observed == {"config": "{}\n"}


def test_container_cleanup_is_attempted_when_docker_run_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_docker(*args: str, **_kwargs: object):
        calls.append(tuple(args))
        if args[:2] == ("context", "inspect"):
            return runner.CommandResult(0, "unix:///var/run/docker.sock\n", "")
        if args and args[0] in {"version", "pull", "rm"}:
            return runner.CommandResult(0, "17.6\n", "")
        if args and args[0] == "run":
            raise runner.GateFailure("command-timeout")
        if args and args[0] == "ps":
            return runner.CommandResult(0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(runner, "_docker", fake_docker)
    code, report = _report(tmp_path, docker_enabled=True)
    assert code == 1
    assert report["cleanup"]["container_absent"] is True
    assert any(args[:2] == ("rm", "--force") for args in calls)


def test_missing_scenario_or_false_invariant_fails_closed(tmp_path: Path) -> None:
    def incomplete(_workspace: Path, _runtime_dir: Path, _effects):
        scenarios, invariants, writes = _sqlite_success(_workspace, _runtime_dir, _effects)
        scenarios.pop("sqlite_prepare_rollback")
        invariants["atomic_prepare"] = False
        return scenarios, invariants, writes

    code, report = _report(tmp_path, sqlite_runner=incomplete)
    assert code == 1
    assert report["success"] is False
    assert report["cleanup"]["workspace_absent"] is True


@pytest.mark.parametrize("commit", [None, "", "1" * 39, "G" * 40, True])
def test_expected_commit_is_strict_and_cleanup_report_is_still_written(tmp_path: Path, commit: object) -> None:
    code, report = _report(tmp_path, expected_commit=commit)
    assert code == 1
    assert report["success"] is False
    assert report["tested_commit_sha"] == ""
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False


def test_expected_commit_must_match_the_actual_checkout_head(tmp_path: Path) -> None:
    forged = "0" * 40 if TESTED_COMMIT != "0" * 40 else "1" * 40
    code, report = _report(tmp_path, expected_commit=forged)
    assert code == 1
    assert report["success"] is False
    assert report["tested_commit_sha"] == ""
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False


def test_contract_schema_and_hash_are_repository_owned(tmp_path: Path) -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    contract["scenarios"].pop()
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(contract), encoding="utf-8")
    code, report = _report(tmp_path, contract_path=invalid)
    assert code == 1
    assert report["success"] is False
    assert report["contract_sha256"] == ""


def test_report_write_is_atomic(tmp_path: Path) -> None:
    code, _ = _report(tmp_path)
    assert code == 0
    assert list(tmp_path.glob(".phase3j-runtime.json.*.tmp")) == []


def test_known_gate_failure_emits_only_repository_owned_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def rejected_postgres(_container: str, _migration: Path):
        raise runner.GateFailure("phase3j-migration-failed")

    code, report = _report(tmp_path, postgres_runner=rejected_postgres)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "failure_code": "phase3j-migration-failed",
        "success": False,
    }
    assert "failure_code" not in report


def test_unexpected_exception_diagnostic_never_exposes_exception_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_probe = "raw-exception-must-not-appear"

    def rejected_postgres(_container: str, _migration: Path):
        raise RuntimeError(secret_probe)

    code, report = _report(tmp_path, postgres_runner=rejected_postgres)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "failure_code": "unexpected-gate-failure",
        "success": False,
    }
    assert secret_probe not in captured.err
    assert secret_probe not in json.dumps(report, sort_keys=True)
    assert "failure_code" not in report


def test_unrecognized_gate_failure_is_collapsed_to_owned_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    untrusted_code = "not-a-repository-owned-code"

    def rejected_postgres(_container: str, _migration: Path):
        raise runner.GateFailure(untrusted_code)

    code, _ = _report(tmp_path, postgres_runner=rejected_postgres)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "failure_code": "unexpected-gate-failure",
        "success": False,
    }
    assert untrusted_code not in captured.err


def test_docker_invocation_is_digest_pinned_network_none_and_has_no_host_publish() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    assert runner.IMAGE.startswith("postgres:17.6-bookworm@sha256:")
    assert '"--network", "none"' in source
    assert '"--pull", "never", IMAGE' in source
    for forbidden in ('"--publish"', '"-p"', "DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        assert forbidden not in source


def test_postgres_contract_consumes_exact_repository_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = runner._load_module(runner.VERIFIER_PATH, "phase3j_pg_expected")
    scenario_keys = [key for key in verifier.SCENARIO_KEYS if key.startswith("postgres_")]
    catalog_keys = [
        "authorization_table_present", "reservation_table_present", "event_table_present",
        "rls_enabled", "no_browser_policies", "server_read_only_tables",
        "reservation_rpc_signatures", "rpc_security_definer", "rpc_search_path_fixed",
        "append_only_events", "authorization_bootstrap_only",
    ]
    marker_output = "\n".join(f"phase3j_check:{key}" for key in [*catalog_keys, *scenario_keys])
    calls: list[str] = []

    def fake_psql(_container: str, sql: str, **_kwargs: object):
        calls.append(sql)
        if sql == runner.PHASE3J_RUNTIME_CONTRACT.read_text(encoding="utf-8"):
            return runner.CommandResult(0, marker_output, "")
        if "SELECT\n  (SELECT count(*) FROM public.scrape_execution_authorizations)" in sql:
            return runner.CommandResult(0, "23\n", "")
        return runner.CommandResult(0, "1\n", "")

    monkeypatch.setattr(runner, "_psql", fake_psql)
    scenarios, facts, writes = runner._run_postgres_contract("container", MIGRATION)
    assert scenarios == {key: True for key in scenario_keys}
    assert facts == {
        "review_approval_not_execution_authority": True,
        "service_role_only_reservation": True,
    }
    assert writes == 23
    assert calls[:5] == [
        runner.PHASE3G_BOOTSTRAP.read_text(encoding="utf-8"),
        runner.PHASE3G_MIGRATION.read_text(encoding="utf-8"),
        MIGRATION.read_text(encoding="utf-8"),
        MIGRATION.read_text(encoding="utf-8"),
        runner.PHASE3J_BOOTSTRAP.read_text(encoding="utf-8"),
    ]


def test_postgres_contract_missing_marker_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_psql(_container: str, sql: str, **_kwargs: object):
        if sql == runner.PHASE3J_RUNTIME_CONTRACT.read_text(encoding="utf-8"):
            return runner.CommandResult(0, "phase3j_check:authorization_table_present\n", "")
        return runner.CommandResult(0, "1\n", "")

    monkeypatch.setattr(runner, "_psql", fake_psql)
    with pytest.raises(runner.GateFailure, match="postgres-runtime-markers-incomplete"):
        runner._run_postgres_contract("container", MIGRATION)


def test_runner_does_not_import_api_worker_or_external_clients() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "routers.scrape", "scraping.jobs", "import requests", "import httpx",
        "from supabase", "import supabase",
    ):
        assert forbidden not in source

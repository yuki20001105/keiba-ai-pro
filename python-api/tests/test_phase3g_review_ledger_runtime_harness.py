from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "security" / "run_phase3g_review_ledger_runtime_gate.py"


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase3g_runtime_gate", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def runner() -> ModuleType:
    return _load_runner()


def _successful_docker(runner: ModuleType, calls: list[tuple[str, ...]]):
    def fake_docker(*args: str, timeout: int = 60):
        del timeout
        calls.append(tuple(args))
        if args[:2] == ("version", "--format"):
            return runner.CommandResult(0, "27.0.0\n", "")
        if args[:2] == ("context", "inspect"):
            return runner.CommandResult(0, "unix:///var/run/docker.sock\n", "")
        if args[0] == "pull":
            return runner.CommandResult(0, "pulled\n", "")
        if args[0] == "run":
            return runner.CommandResult(0, "a" * 64 + "\n", "")
        if args[0] in {"rm", "ps"}:
            return runner.CommandResult(0, "", "")
        return runner.CommandResult(0, "", "")

    return fake_docker


def _all_markers(runner: ModuleType) -> str:
    keys = (*runner.CATALOG_CHECK_KEYS, *runner.BEHAVIORAL_CHECK_KEYS)
    return "\n".join(f"{runner.MARKER_PREFIX}{key}" for key in keys)


def _configure_success(monkeypatch: pytest.MonkeyPatch, runner: ModuleType, report: Path):
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(runner, "REPORT", report)
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)
    monkeypatch.setattr(runner, "_docker", _successful_docker(runner, calls))
    monkeypatch.setattr(runner, "_wait_for_postgres", lambda container: None)
    monkeypatch.setattr(runner, "_run_concurrency_checks", lambda container: None)
    monkeypatch.setattr(runner, "_container_absent", lambda name: True)
    monkeypatch.setattr(
        runner,
        "_psql",
        lambda container, sql, timeout=90: runner.CommandResult(0, _all_markers(runner), ""),
    )
    return calls


def test_command_uses_no_shell_and_does_not_forward_database_environment(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setenv("DATABASE_URL", "postgresql://shared.invalid/forbidden")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://shared.invalid/forbidden")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner._command(("docker", "version"))

    assert result.returncode == 0
    assert captured["shell"] is False
    assert captured["args"] == ["docker", "version"]
    assert "DATABASE_URL" not in captured["env"]
    assert "SUPABASE_DB_URL" not in captured["env"]


def test_missing_command_fails_closed(monkeypatch: pytest.MonkeyPatch, runner: ModuleType) -> None:
    def missing(*args, **kwargs):
        del args, kwargs
        raise FileNotFoundError("docker")

    monkeypatch.setattr(runner.subprocess, "run", missing)
    with pytest.raises(runner.GateFailure, match="command_missing"):
        runner._command(("docker", "version"))


def test_psql_process_uses_only_container_internal_unix_socket(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
) -> None:
    captured: dict[str, object] = {}

    class DummyProcess:
        pass

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return DummyProcess()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    process = runner._start_psql("synthetic-container", "SELECT 1")

    assert isinstance(process, DummyProcess)
    args = captured["args"]
    assert args[:4] == ["docker", "exec", "-i", "synthetic-container"]
    assert args[args.index("--host") + 1] == "/var/run/postgresql"
    assert args[args.index("--dbname") + 1] == "phase3g_runtime"
    assert not any("postgresql://" in value for value in args)
    assert captured["shell"] is False


def test_migration_hash_is_stable_across_checkout_line_endings(
    tmp_path: Path,
    runner: ModuleType,
) -> None:
    lf_path = tmp_path / "migration-lf.sql"
    crlf_path = tmp_path / "migration-crlf.sql"
    changed_path = tmp_path / "migration-changed.sql"
    lf_path.write_bytes(b"SELECT 1;\nSELECT 2;\n")
    crlf_path.write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")
    changed_path.write_bytes(b"SELECT 1;\nSELECT 3;\n")

    assert runner._sha256(lf_path) == runner._sha256(crlf_path)
    assert runner._sha256(lf_path) != runner._sha256(changed_path)


def test_success_report_is_synthetic_non_l3_and_container_is_network_isolated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: ModuleType,
) -> None:
    report = tmp_path / "runtime.json"
    calls = _configure_success(monkeypatch, runner, report)

    assert runner.run_gate() == 0
    payload = json.loads(report.read_text(encoding="utf-8"))

    assert payload["success"] is True
    assert payload["schema_version"] == 1
    assert payload["evidence_mode"] == "synthetic"
    assert payload["environment"] == "ci-disposable"
    assert payload["database_scope"] == "disposable_docker"
    assert payload["network_mode"] == "none"
    assert payload["image"] == runner.IMAGE
    assert payload["tested_commit_sha"] == "f" * 40
    assert payload["migration_sha256"] == runner._sha256(runner.MIGRATION)
    assert payload["synthetic"] is True
    assert payload["l3_eligible"] is False
    assert all(payload["catalog_checks"].values())
    assert all(payload["behavioral_checks"].values())
    assert payload["cleanup"] == {"attempted": True, "container_absent": True}

    run_call = next(call for call in calls if call[0] == "run")
    assert run_call[run_call.index("--network") + 1] == "none"
    assert "--publish" not in run_call
    assert "-p" not in run_call
    assert run_call[-1] == runner.IMAGE
    assert "@sha256:" in runner.IMAGE
    serialized = report.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD" not in serialized
    assert "postgresql://" not in serialized
    assert str(ROOT) not in serialized


def test_remote_docker_context_is_rejected_before_container_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: ModuleType,
) -> None:
    report = tmp_path / "runtime.json"
    monkeypatch.setattr(runner, "REPORT", report)
    monkeypatch.setenv("GITHUB_SHA", "e" * 40)
    calls: list[tuple[str, ...]] = []

    def fake_docker(*args: str, timeout: int = 60):
        del timeout
        calls.append(tuple(args))
        if args[0] == "version":
            return runner.CommandResult(0, "27.0.0", "")
        if args[0] == "context":
            return runner.CommandResult(0, "tcp://shared-docker.invalid:2376", "")
        if args[0] == "ps":
            return runner.CommandResult(0, "", "")
        return runner.CommandResult(0, "", "")

    monkeypatch.setattr(runner, "_docker", fake_docker)
    monkeypatch.setattr(runner, "_container_absent", lambda name: True)

    assert runner.run_gate() == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["failure_code"] == "remote_docker_context_rejected"
    assert payload["success"] is False
    assert not any(call[0] == "version" for call in calls)
    assert not any(call[0] == "run" for call in calls)
    assert not any(call[0] in {"rm", "ps"} for call in calls)
    assert payload["cleanup"] == {"attempted": False, "container_absent": False}


def test_failed_docker_run_still_forces_named_container_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: ModuleType,
) -> None:
    report = tmp_path / "runtime.json"
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(runner, "REPORT", report)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)

    def fake_docker(*args: str, timeout: int = 60):
        del timeout
        calls.append(tuple(args))
        if args[0] == "context":
            return runner.CommandResult(0, "unix:///var/run/docker.sock", "")
        if args[0] == "version":
            return runner.CommandResult(0, "27.0.0", "")
        if args[0] == "pull":
            return runner.CommandResult(0, "pulled", "")
        if args[0] == "run":
            return runner.CommandResult(125, "", "start failed after create")
        return runner.CommandResult(0, "", "")

    monkeypatch.setattr(runner, "_docker", fake_docker)
    monkeypatch.setattr(runner, "_container_absent", lambda name: True)

    assert runner.run_gate() == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["failure_code"] == "container_start_failed"
    assert payload["cleanup"] == {"attempted": True, "container_absent": True}
    assert any(call[0:2] == ("rm", "--force") for call in calls)


def test_docker_missing_still_writes_fail_closed_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: ModuleType,
) -> None:
    report = tmp_path / "runtime.json"
    monkeypatch.setattr(runner, "REPORT", report)
    monkeypatch.setenv("GITHUB_SHA", "c" * 40)

    def missing_docker(*args: str, timeout: int = 60):
        del args, timeout
        raise runner.GateFailure("command_missing")

    monkeypatch.setattr(runner, "_docker", missing_docker)

    assert runner.run_gate() == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["failure_code"] == "command_missing"
    assert payload["success"] is False
    assert payload["cleanup"] == {"attempted": False, "container_absent": False}


def test_invalid_container_id_still_forces_named_container_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: ModuleType,
) -> None:
    report = tmp_path / "runtime.json"
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(runner, "REPORT", report)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)

    def fake_docker(*args: str, timeout: int = 60):
        del timeout
        calls.append(tuple(args))
        if args[0] == "version":
            return runner.CommandResult(0, "27.0.0", "")
        if args[0] == "context":
            return runner.CommandResult(0, "unix:///var/run/docker.sock", "")
        if args[0] == "pull":
            return runner.CommandResult(0, "pulled", "")
        if args[0] == "run":
            return runner.CommandResult(0, "not-a-container-id", "")
        return runner.CommandResult(0, "", "")

    monkeypatch.setattr(runner, "_docker", fake_docker)
    monkeypatch.setattr(runner, "_container_absent", lambda name: True)

    assert runner.run_gate() == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["failure_code"] == "container_id_invalid"
    assert payload["cleanup"] == {"attempted": True, "container_absent": True}
    assert any(call[0:2] == ("rm", "--force") for call in calls)


def test_sql_failure_still_forces_cleanup_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: ModuleType,
) -> None:
    report = tmp_path / "runtime.json"
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(runner, "REPORT", report)
    monkeypatch.setenv("GITHUB_SHA", "d" * 40)
    monkeypatch.setattr(runner, "_docker", _successful_docker(runner, calls))
    monkeypatch.setattr(runner, "_wait_for_postgres", lambda container: None)
    monkeypatch.setattr(runner, "_container_absent", lambda name: True)
    monkeypatch.setattr(
        runner,
        "_psql",
        lambda container, sql, timeout=90: runner.CommandResult(1, "sensitive row", "raw SQL error"),
    )

    assert runner.run_gate() == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["failure_code"] == "bootstrap_failed"
    assert payload["success"] is False
    assert payload["cleanup"] == {"attempted": True, "container_absent": True}
    assert any(call[0:2] == ("rm", "--force") for call in calls)
    serialized = report.read_text(encoding="utf-8")
    assert "sensitive row" not in serialized
    assert "raw SQL error" not in serialized


def test_cleanup_failure_overrides_otherwise_successful_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: ModuleType,
) -> None:
    report = tmp_path / "runtime.json"
    _configure_success(monkeypatch, runner, report)
    monkeypatch.setattr(runner, "_container_absent", lambda name: False)

    assert runner.run_gate() == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["failure_code"] == "container_cleanup_failed"
    assert payload["success"] is False
    assert payload["cleanup"] == {"attempted": True, "container_absent": False}


def test_incomplete_runtime_markers_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: ModuleType,
) -> None:
    report = tmp_path / "runtime.json"
    _configure_success(monkeypatch, runner, report)
    monkeypatch.setattr(
        runner,
        "_psql",
        lambda container, sql, timeout=90: runner.CommandResult(
            0, f"{runner.MARKER_PREFIX}request_table_present", ""
        ),
    )

    assert runner.run_gate() == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["failure_code"] == "runtime_markers_incomplete"
    assert payload["success"] is False


def test_report_check_key_sets_are_frozen(runner: ModuleType) -> None:
    assert set(runner.CATALOG_CHECK_KEYS) == {
        "migration_compiles", "request_table_present", "event_table_present",
        "rls_enabled", "no_browser_policies", "no_browser_table_grants",
        "service_role_rpc_signatures", "rpc_security_definer", "rpc_search_path_fixed",
        "immutable_event_trigger", "review_only_constraints", "no_execution_rpc",
    }
    assert set(runner.BEHAVIORAL_CHECK_KEYS) == {
        "idempotent_create", "self_approval_rejected", "cas_conflict_rejected",
        "concurrent_create_serialized", "concurrent_decision_serialized",
        "expiry_materialized", "immutable_event_mutation_rejected",
        "review_only_flags_enforced", "no_execution_rpc_observed",
    }

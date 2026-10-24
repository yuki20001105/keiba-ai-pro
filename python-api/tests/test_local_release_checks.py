"""The local check runner must never select real-world workflow actions."""
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("local_release_checks", ROOT / "scripts/check_local_release.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


def test_commands_use_explicit_safe_python_contracts():
    for name in checks.PYTHON_TESTS:
        assert (ROOT / name).is_file()
    commands = checks.check_commands("node", build=False, e2e=False)
    assert all("real-workflow" not in " ".join(command) for _, command in commands)
    assert not any(name in {"isolated-build", "fixture-e2e"} for name, _ in commands)
    assert dict(commands)["python-contracts"][3] == "scripts/run_local_release_pytests.py"
    matrix_command = dict(commands)["authorization-matrix"]
    assert "build_matrix_payload" in matrix_command[-1]
    assert "--sync" not in matrix_command


def test_e2e_never_runs_the_entire_suite():
    command = dict(checks.check_commands("node", build=True, e2e=True))["fixture-e2e"]
    assert len(checks.FIXTURE_E2E) >= 2
    assert all(name in command and (ROOT / name).is_file() for name in checks.FIXTURE_E2E)
    assert "real-workflow.spec.ts" not in " ".join(command)
    assert "--retries=0" in command


def test_fixture_environment_is_local_and_uses_only_dummy_auth(monkeypatch):
    monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_URL", "https://not-a-test-project.example")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "never-send-this-key")
    env = checks.check_environment("fixture-e2e")
    assert env["PW_BASE_URL"] == "http://127.0.0.1:3101"
    assert env["SUPABASE_URL"] == "http://127.0.0.1:54321"
    assert env["NEXT_PUBLIC_SUPABASE_URL"] == "http://127.0.0.1:54321"
    assert env["SUPABASE_SERVICE_ROLE_KEY"] == "e2e-dummy-service-role-key"
    assert env["NEXT_RELEASE_CHECK"] == "1"


def test_build_is_opt_in_and_isolated():
    env = checks.check_environment("isolated-build")
    assert env["NEXT_RELEASE_CHECK"] == "1"
    assert dict(checks.check_commands("node", build=True, e2e=False))["isolated-build"][-2:] == ["build", "--webpack"]

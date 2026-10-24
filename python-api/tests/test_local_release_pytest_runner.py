from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_script("release_pytest_runner", "scripts/run_local_release_pytests.py")


def test_parent_release_selection_is_fully_allowlisted():
    checker = load_script("release_checker_for_allowlist", "scripts/check_local_release.py")
    assert set(checker.PYTHON_TESTS) <= runner.ALLOWED_TESTS


def test_selection_keeps_order_and_removes_duplicates():
    selected = ["python-api/tests/test_server_scrape_batch.py", "python-api/tests/test_model_training_guard.py"]
    assert runner.validate_selection(selected + selected[:1]) == selected


@pytest.mark.parametrize("selection", [
    [], ["e2e/real-workflow.spec.ts"], ["python-api/tests/not-audited.py"],
    ["../python-api/tests/test_server_scrape_batch.py"],
    ["--override-ini"], ["python-api/tests/test_server_scrape_batch.py::test_date_bounds_keep_legacy_limits_and_partial_calendar_months"],
])
def test_unreviewed_selection_or_flags_rejected(selection):
    with pytest.raises(ValueError):
        runner.validate_selection(selection)


def guard_for(tmp_path):
    temporary = tmp_path / "allowed-temporary"
    temporary.mkdir()
    return runner.ReleaseIOGuard(temp_root=temporary)


def test_memory_and_temporary_sqlite_allowed(tmp_path):
    guard = guard_for(tmp_path)
    for database in (":memory:", guard.temp_root / "fixture.db"):
        with guard.sqlite_connect(database) as connection:
            connection.execute("CREATE TABLE example(value)")
    assert guard.exit_code(0) == 0


def test_non_temporary_sqlite_write_denied_even_if_caught(tmp_path):
    guard = guard_for(tmp_path)
    target = tmp_path / "protected.db"
    with pytest.raises(sqlite3.OperationalError):
        guard.sqlite_connect(target)
    assert not target.exists()
    assert guard.sqlite_denials == 1 and guard.exit_code(0) == 1


def test_explicit_readonly_non_temp_database_allowed_and_query_only(tmp_path):
    guard = guard_for(tmp_path)
    target = tmp_path / "existing.db"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE example(value)")
        connection.execute("INSERT INTO example VALUES (7)")
    before = target.read_bytes()
    with guard.sqlite_connect(target.as_uri() + "?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT value FROM example").fetchone() == (7,)
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM example")
    assert target.read_bytes() == before
    assert guard.exit_code(0) == 0


def test_ro_text_without_uri_option_not_treated_as_readonly(tmp_path):
    guard = guard_for(tmp_path)
    with pytest.raises(sqlite3.OperationalError):
        guard.sqlite_connect((tmp_path / "protected.db").as_uri() + "?mode=ro")
    assert guard.sqlite_denials == 1


def test_duplicate_mode_cannot_masquerade_as_readonly(tmp_path):
    guard = guard_for(tmp_path)
    with pytest.raises(sqlite3.OperationalError):
        guard.sqlite_connect((tmp_path / "protected.db").as_uri() + "?mode=ro&mode=rw", uri=True)
    assert guard.sqlite_denials == 1


def test_remote_sqlite_uri_denied_before_connect(tmp_path):
    guard = guard_for(tmp_path)
    with pytest.raises(sqlite3.OperationalError):
        guard.sqlite_connect("file://remote.invalid/share/data.db?mode=ro", uri=True)
    assert guard.network_denials == 1 and guard.exit_code(0) == 1


def test_only_known_module_import_schema_attempt_is_nonfatal(tmp_path):
    guard = guard_for(tmp_path)
    target = tmp_path / "protected.db"
    source = "def _init_jobs_db():\n    try:\n        sqlite3.connect(target)\n    except sqlite3.OperationalError:\n        pass\n_init_jobs_db()\n"
    namespace = {"sqlite3": SimpleNamespace(connect=guard.sqlite_connect, OperationalError=sqlite3.OperationalError), "target": target}
    exec(compile(source, str(ROOT / "python-api/scraping/jobs.py"), "exec"), namespace)
    assert not target.exists()
    assert guard.known_import_sqlite_denials == 1 and guard.sqlite_denials == 0
    assert guard.exit_code(0) == 0
    # The same function invoked later is no longer the exempt import context.
    namespace["_init_jobs_db"]()
    assert guard.sqlite_denials == 1 and guard.exit_code(0) == 1


def test_identical_function_name_in_another_module_not_exempt(tmp_path):
    guard = guard_for(tmp_path)
    source = "def _init_jobs_db():\n    try:\n        sqlite3.connect(target)\n    except sqlite3.OperationalError:\n        pass\n_init_jobs_db()\n"
    namespace = {"sqlite3": SimpleNamespace(connect=guard.sqlite_connect, OperationalError=sqlite3.OperationalError), "target": tmp_path / "protected.db"}
    exec(compile(source, str(tmp_path / "unreviewed.py"), "exec"), namespace)
    assert guard.known_import_sqlite_denials == 0 and guard.sqlite_denials == 1


def test_socket_and_http_loopback_are_denied_without_opening_connection(tmp_path):
    guard = guard_for(tmp_path)
    calls = []
    guard.original_socket_connect = lambda *_args: calls.append(True)
    with pytest.raises(RuntimeError):
        guard.socket_connect(object(), ("127.0.0.1", 8000))
    with pytest.raises(RuntimeError):
        guard.reject_network("https://provider.invalid", "private-token")
    assert calls == []
    assert guard.network_denials == 2 and guard.exit_code(0) == 1
    assert "private-token" not in str(guard.summary(0))


def test_exact_socketpair_caller_only_is_permitted(tmp_path):
    guard = guard_for(tmp_path)
    calls = []
    guard.original_socket_connect = lambda *_args: calls.append(True)

    def simulated_stdlib_socketpair():
        return guard.socket_connect(object(), ("127.0.0.1", 12345))

    guard.socketpair_code = simulated_stdlib_socketpair.__code__
    simulated_stdlib_socketpair()
    assert calls == [True] and guard.internal_socketpairs == 1
    assert guard.network_denials == 0


def test_restoration_is_process_local_and_pytest_failure_preserved(tmp_path):
    guard = guard_for(tmp_path)
    target = SimpleNamespace(value="original")
    guard._replace(target, "value", "temporary")
    assert target.value == "temporary"
    guard.restore()
    assert target.value == "original"
    assert guard.exit_code(2) == 2
    assert guard.summary(0)["guard_scope"] == "child_python_entrypoints_not_os_sandbox"

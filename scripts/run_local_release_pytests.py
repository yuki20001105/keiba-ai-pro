"""Run only audited local-release fixtures with process-local I/O guards.

Not an OS sandbox: Python socket/SQLite entrypoints are guarded and caught
violations still fail the run. Native libraries or tests replacing these guards
could bypass them, so the fixed test allowlist remains essential. No production
process, collector, credentials, or runtime source is changed. The existing
scraping.jobs import-time schema initializer is blocked and counted separately;
its existing OperationalError handling allows collection to continue.
"""
from __future__ import annotations

import argparse
import json
import logging.handlers
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
from types import FrameType
from urllib.parse import parse_qs, urlsplit
from urllib.request import url2pathname

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_TESTS = frozenset({
    "python-api/tests/test_prediction_compute.py",
    "python-api/tests/test_prediction_explainability.py",
    "python-api/tests/test_prediction_history_loader.py",
    "python-api/tests/test_prediction_input_contract.py",
    "python-api/tests/test_model_predictor_legacy_contract.py",
    "python-api/tests/test_predict_data_refresh.py",
    "python-api/tests/test_race_entry_fallback.py",
    "python-api/tests/test_betting_value_policy.py",
    "python-api/tests/test_bet_export_policy.py",
    "python-api/tests/test_betting_performance.py",
    "python-api/tests/test_performance_routes.py",
    "python-api/tests/test_phase2_authz.py",
    "python-api/tests/test_automated_betting_guard.py",
    "python-api/tests/test_local_retrain.py",
    "python-api/tests/test_phase2_authz_matrix_gate.py",
    "python-api/tests/test_phase3l_deployment_safety.py",
    "python-api/tests/test_forward_hash_compatibility_audit.py",
    "python-api/tests/test_forward_paper_compatible_report.py",
    "python-api/tests/test_active_model_input_compatibility_audit.py",
    "python-api/tests/test_local_release_checks.py",
    "python-api/tests/test_local_release_readonly_smoke.py",
    "python-api/tests/test_local_authenticated_readonly_smoke.py",
    "python-api/tests/test_local_candidate_input.py",
    "python-api/tests/test_local_candidate_evaluation.py",
    "python-api/tests/test_local_candidate_serving.py",
    "python-api/tests/test_local_candidate_serving_smoke.py",
    "python-api/tests/test_db_shared_features.py",
    "python-api/tests/test_db_shared_fit.py",
    "python-api/tests/test_db_shared_serving.py",
    "python-api/tests/test_benchmark_shared_db_fit.py",
    "python-api/tests/test_local_workflow_proof.py",
    "python-api/tests/test_reference_bets.py",
    "python-api/tests/test_server_scrape_batch.py",
    "python-api/tests/test_scrape_storage_checkpoint.py",
    "python-api/tests/test_scrape_job_restart.py",
    "python-api/tests/test_phase3e_scrape_job_security.py",
    "python-api/tests/test_local_retrain_worker_main.py",
    "python-api/tests/test_model_training_guard.py",
    "python-api/tests/test_model_deletion_guard.py",
    "python-api/tests/test_local_release_pytest_runner.py",
    "keiba/keiba_ai/tests/test_prediction_history_performance.py",
    "keiba/keiba_ai/tests/test_point_in_time_history.py",
    "keiba/keiba_ai/tests/test_feature_history_leakage.py",
    "keiba/keiba_ai/tests/test_feature_engineering.py",
})


def validate_selection(files: list[str]) -> list[str]:
    if not files or any(value not in ALLOWED_TESTS for value in files):
        raise ValueError("Only explicitly audited release test files are allowed")
    return list(dict.fromkeys(files))


class ReleaseIOGuard:
    def __init__(self, *, temp_root: Path | None = None):
        self.temp_root = (temp_root or Path(tempfile.gettempdir())).resolve()
        self.original_sqlite_connect = sqlite3.connect
        self.original_socket_connect = socket.socket.connect
        self.socketpair_code = getattr(socket.socketpair, "__code__", None)
        self.known_import_sqlite_denials = 0
        self.sqlite_denials = 0
        self.network_denials = 0
        self.internal_socketpairs = 0
        self.sqlite_denial_sites: list[str] = []
        self._originals: list[tuple[object, str, object]] = []

    @staticmethod
    def _known_jobs_import(frame: FrameType) -> bool:
        caller = frame.f_back
        expected = ROOT / "python-api/scraping/jobs.py"
        return (
            frame.f_code.co_name == "_init_jobs_db"
            and Path(frame.f_code.co_filename).resolve() == expected
            and caller is not None and caller.f_code.co_name == "<module>"
            and Path(caller.f_code.co_filename).resolve() == expected
        )

    def sqlite_connect(self, database, *args, **kwargs):
        value = str(database)
        uri = kwargs.get("uri", args[6] if len(args) > 6 else False) is True
        parsed = urlsplit(value) if uri and value.startswith("file:") else None
        if parsed and parsed.netloc not in ("", "localhost"):
            self.network_denials += 1
            raise sqlite3.OperationalError("release fixtures forbid remote SQLite locations")
        query = parse_qs(parsed.query) if parsed else {}
        mode = query.get("mode", [])
        if value == ":memory:" or (parsed and (parsed.path == ":memory:" or mode == ["memory"])):
            return self.original_sqlite_connect(database, *args, **kwargs)
        raw_path = url2pathname(parsed.path) if parsed else value
        if raw_path.startswith("\\\\"):
            self.network_denials += 1
            raise sqlite3.OperationalError("release fixtures forbid remote SQLite locations")
        path = Path(raw_path).resolve()
        readonly = parsed is not None and mode == ["ro"]
        if not path.is_relative_to(self.temp_root) and not readonly:
            if self._known_jobs_import(sys._getframe(1)):
                self.known_import_sqlite_denials += 1
            else:
                self.sqlite_denials += 1
                frame = sys._getframe(1)
                source = Path(frame.f_code.co_filename).resolve()
                if len(self.sqlite_denial_sites) < 10:
                    self.sqlite_denial_sites.append(
                        f"{source.relative_to(ROOT).as_posix()}:{frame.f_code.co_name}"
                        if source.is_relative_to(ROOT) else "outside_reviewed_source"
                    )
            raise sqlite3.OperationalError("release fixtures forbid non-temporary SQLite writes")
        connection = self.original_sqlite_connect(database, *args, **kwargs)
        if readonly:
            connection.execute("PRAGMA query_only=ON")
        return connection

    def reject_network(self, *_args, **_kwargs):
        self.network_denials += 1
        raise RuntimeError("release fixtures forbid HTTP/socket network access")

    def socket_connect(self, sock, address, *, caller_code=None):
        # Windows' stdlib socketpair implements asyncio's internal wakeup pipe
        # using loopback. Only this exact function's direct call is allowed;
        # ordinary loopback HTTP/API connections are still forbidden.
        caller_code = sys._getframe(1).f_code if caller_code is None else caller_code
        if self.socketpair_code is not None and caller_code is self.socketpair_code:
            self.internal_socketpairs += 1
            return self.original_socket_connect(sock, address)
        return self.reject_network(sock, address)

    def _replace(self, target: object, name: str, value: object) -> None:
        self._originals.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def install(self, log_directory: Path) -> None:
        import dotenv

        self._replace(dotenv, "load_dotenv", lambda *_args, **_kwargs: False)
        original_handler = logging.handlers.RotatingFileHandler

        class TemporaryLogHandler(original_handler):
            def __init__(self, _filename, *args, **kwargs):
                super().__init__(log_directory / "runtime-import.log", *args, **kwargs)

        self._replace(logging.handlers, "RotatingFileHandler", TemporaryLogHandler)
        self._replace(sqlite3, "connect", self.sqlite_connect)
        # Assigning a bound method to socket.socket would bind the socket twice.
        guard = self
        self._replace(socket.socket, "connect", lambda sock, address: guard.socket_connect(sock, address, caller_code=sys._getframe(1).f_code))
        self._replace(socket.socket, "connect_ex", lambda *_args, **_kwargs: self.reject_network())
        self._replace(socket.socket, "sendto", lambda *_args, **_kwargs: self.reject_network())
        if hasattr(socket.socket, "sendmsg"):
            self._replace(socket.socket, "sendmsg", lambda *_args, **_kwargs: self.reject_network())
        self._replace(socket, "create_connection", self.reject_network)
        self._replace(socket, "getaddrinfo", self.reject_network)
        for name in ("gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
            self._replace(socket, name, self.reject_network)

    def restore(self) -> None:
        for target, name, value in reversed(self._originals):
            setattr(target, name, value)
        self._originals.clear()

    def exit_code(self, pytest_exit_code: int) -> int:
        return int(pytest_exit_code) or int(self.sqlite_denials > 0 or self.network_denials > 0)

    def summary(self, pytest_exit_code: int) -> dict[str, object]:
        return {
            "pytest_exit_code": int(pytest_exit_code), "final_exit_code": self.exit_code(pytest_exit_code),
            "known_import_schema_initializations_blocked": self.known_import_sqlite_denials,
            "other_sqlite_write_denials": self.sqlite_denials,
            "other_sqlite_denial_sites": self.sqlite_denial_sites,
            "network_denials": self.network_denials, "stdlib_internal_socketpairs": self.internal_socketpairs,
            "guard_scope": "child_python_entrypoints_not_os_sandbox",
            "known_import_reason": "scraping.jobs module-level _init_jobs_db is denied; existing exception handling continues collection",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+")
    args = parser.parse_args(argv)
    try:
        files = validate_selection(args.files)
    except ValueError:
        print("Only explicitly audited release test files are allowed", file=sys.stderr)
        return 2
    os.chdir(ROOT)
    os.environ.update({"APP_ENV": "test", "SUPABASE_DATA_ENABLED": "false", "AUTOMATED_BETTING_ENABLED": "false", "PYTHONUTF8": "1"})
    for key in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY", "NEXT_PUBLIC_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_ANON_KEY", "E2E_EMAIL", "E2E_PASSWORD", "LOCAL_AUTO_LOGIN_TOKEN"):
        os.environ.pop(key, None)
    sys.dont_write_bytecode = True
    guard = ReleaseIOGuard()
    with tempfile.TemporaryDirectory(prefix="keiba-release-pytest-") as temporary:
        guard.install(Path(temporary))
        try:
            import pytest
            result = int(pytest.main(["-q", "--tb=short", "-p", "no:cacheprovider", *files]))
            print("LOCAL_RELEASE_PYTEST_GUARD=" + json.dumps(guard.summary(result), sort_keys=True))
            return guard.exit_code(result)
        finally:
            # Close test-only runtime logs before Windows removes the scratch
            # directory. All state belongs solely to this pytest subprocess.
            logging.shutdown()
            guard.restore()


if __name__ == "__main__":
    raise SystemExit(main())

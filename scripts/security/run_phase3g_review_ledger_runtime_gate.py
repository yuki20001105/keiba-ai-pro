from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
IMAGE = "postgres:17.6-bookworm@sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3"
DATABASE = "phase3g_runtime"
MIGRATION = ROOT / "supabase" / "migrations" / "20260718_scrape_uncertainty_review_ledger.sql"
BOOTSTRAP = ROOT / "supabase" / "tests" / "phase3g_review_ledger_bootstrap.sql"
RUNTIME_CONTRACT = ROOT / "supabase" / "tests" / "phase3g_review_ledger_runtime_contract.sql"
REPORT = ROOT / "reports" / "phase3g_review_ledger_runtime.json"

CATALOG_CHECK_KEYS = (
    "migration_compiles",
    "request_table_present",
    "event_table_present",
    "rls_enabled",
    "no_browser_policies",
    "no_browser_table_grants",
    "service_role_rpc_signatures",
    "rpc_security_definer",
    "rpc_search_path_fixed",
    "immutable_event_trigger",
    "review_only_constraints",
    "no_execution_rpc",
)
BEHAVIORAL_CHECK_KEYS = (
    "idempotent_create",
    "self_approval_rejected",
    "cas_conflict_rejected",
    "concurrent_create_serialized",
    "concurrent_decision_serialized",
    "expiry_materialized",
    "immutable_event_mutation_rejected",
    "review_only_flags_enforced",
    "no_execution_rpc_observed",
)

MARKER_PREFIX = "phase3g_check:"
LOCAL_DOCKER_ENDPOINTS = ("unix://", "npipe://")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{12,64}$")

REQUESTER = "11111111-1111-4111-8111-111111111111"
REVIEWER = "22222222-2222-4222-8222-222222222222"
CREATE_CLIENT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
DECISION_CLIENT = "dddddddd-dddd-4ddd-8ddd-ddddddddddd1"


class GateFailure(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _safe_environment() -> dict[str, str]:
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "HOME", "TMP", "TEMP")
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _command(
    args: Sequence[str],
    *,
    input_text: str | None = None,
    timeout: int = 60,
) -> CommandResult:
    try:
        result = subprocess.run(
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
        raise GateFailure("command_missing") from exc
    except subprocess.TimeoutExpired as exc:
        raise GateFailure("command_timeout") from exc
    return CommandResult(result.returncode, result.stdout, result.stderr)


def _require_success(result: CommandResult, code: str) -> str:
    if result.returncode != 0:
        raise GateFailure(code)
    return result.stdout.strip()


def _docker(*args: str, timeout: int = 60) -> CommandResult:
    return _command(("docker", *args), timeout=timeout)


def _psql(container: str, sql: str, *, timeout: int = 90) -> CommandResult:
    return _command(
        (
            "docker",
            "exec",
            "-i",
            container,
            "psql",
            "-X",
            "--no-psqlrc",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
            "--set",
            "VERBOSITY=verbose",
            "--host",
            "/var/run/postgresql",
            "--port",
            "5432",
            "--username",
            "postgres",
            "--dbname",
            DATABASE,
        ),
        input_text=sql,
        timeout=timeout,
    )


def _tested_commit_sha() -> str:
    github_sha = os.environ.get("GITHUB_SHA", "").lower()
    if SHA_PATTERN.fullmatch(github_sha):
        return github_sha
    result = _command(("git", "rev-parse", "HEAD"), timeout=10)
    value = _require_success(result, "git_head_unavailable").lower()
    if not SHA_PATTERN.fullmatch(value):
        raise GateFailure("git_head_invalid")
    return value


def _sha256(path: Path) -> str:
    raw = path.read_bytes()
    canonical = raw.replace(b"\r\n", b"\n")
    return hashlib.sha256(canonical).hexdigest()


def _wait_for_postgres(container: str, timeout_seconds: int = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    stable_start_time: str | None = None
    consecutive_stable_probes = 0
    while time.monotonic() < deadline:
        ready = _docker(
            "exec",
            container,
            "pg_isready",
            "--host",
            "/var/run/postgresql",
            "--port",
            "5432",
            "--username",
            "postgres",
            "--dbname",
            DATABASE,
            timeout=10,
        )
        if ready.returncode == 0:
            probe = _psql(
                container,
                "SELECT pg_postmaster_start_time()::text;",
                timeout=10,
            )
            start_time = probe.stdout.strip() if probe.returncode == 0 else ""
            if start_time:
                if start_time == stable_start_time:
                    consecutive_stable_probes += 1
                else:
                    stable_start_time = start_time
                    consecutive_stable_probes = 1
                if consecutive_stable_probes >= 2:
                    return
            else:
                stable_start_time = None
                consecutive_stable_probes = 0
        else:
            stable_start_time = None
            consecutive_stable_probes = 0
        running = _docker("inspect", "--format", "{{.State.Running}}", container, timeout=10)
        if running.returncode != 0 or running.stdout.strip().lower() != "true":
            raise GateFailure("container_exited_before_ready")
        time.sleep(1)
    raise GateFailure("postgres_health_timeout")


def _advisory_lock_sql(client_request_id: str, seconds: int = 3) -> str:
    return f"""
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('{REQUESTER}:{client_request_id}', 0));
SELECT pg_sleep({seconds});
COMMIT;
"""


def _create_sql(client_request_id: str, reason: str) -> str:
    return f"""
SET ROLE service_role;
SELECT review_id::text
FROM public.create_scrape_uncertainty_review(
  '{REQUESTER}'::uuid,
  '{client_request_id}'::uuid,
  'monitoring', '2026-01', '2026-01', FALSE,
  (SELECT occurred_at FROM phase3g_test.runtime_clock WHERE singleton),
  '{reason}', TRUE, TRUE
);
"""


def _decision_sql(action: str) -> str:
    return f"""
SET ROLE service_role;
SELECT status
FROM public.transition_scrape_uncertainty_review(
  '{REVIEWER}'::uuid,
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '{DECISION_CLIENT}'::uuid),
  1, '{action}',
  'Independent reviewer recorded a synthetic runtime decision.'
);
"""


def _start_psql(container: str, sql: str) -> subprocess.Popen[str]:
    try:
        return subprocess.Popen(
            [
                "docker", "exec", "-i", container, "psql",
                "-X", "--no-psqlrc", "--quiet", "--tuples-only", "--no-align",
                "--set", "ON_ERROR_STOP=1", "--set", "VERBOSITY=verbose",
                "--host", "/var/run/postgresql", "--port", "5432",
                "--username", "postgres", "--dbname", DATABASE,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            env=_safe_environment(),
        )
    except FileNotFoundError as exc:
        raise GateFailure("command_missing") from exc


def _wait_for_lock(container: str, *, waiting: bool, timeout_seconds: int = 10) -> None:
    predicate = "NOT granted" if waiting else "granted"
    sql = (
        "SELECT count(*) FROM pg_locks "
        f"WHERE locktype = 'advisory' AND {predicate};"
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _psql(container, sql, timeout=10)
        if result.returncode == 0:
            try:
                if int(result.stdout.strip() or "0") >= 1:
                    return
            except ValueError:
                pass
        time.sleep(0.1)
    raise GateFailure("concurrency_lock_not_observed")


def _wait_for_relation_lock(container: str, relation: str, timeout_seconds: int = 10) -> None:
    sql = (
        "SELECT count(*) FROM pg_locks "
        "WHERE locktype = 'relation' AND granted "
        f"AND relation = '{relation}'::regclass AND mode = 'RowShareLock';"
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _psql(container, sql, timeout=10)
        if result.returncode == 0:
            try:
                if int(result.stdout.strip() or "0") >= 1:
                    return
            except ValueError:
                pass
        time.sleep(0.1)
    raise GateFailure("concurrency_row_lock_not_observed")


def _communicate(process: subprocess.Popen[str], sql: str, timeout: int = 15) -> tuple[int, str, str]:
    try:
        stdout, stderr = process.communicate(sql, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise GateFailure("concurrency_timeout") from exc
    return process.returncode, stdout, stderr


def _run_concurrency_checks(container: str) -> None:
    create_reason = "Concurrent monitoring uncertainty requires independent review."
    blocker = _start_psql(container, _advisory_lock_sql(CREATE_CLIENT))
    if blocker.stdin is None:
        raise GateFailure("concurrency_start_failed")
    blocker.stdin.write(_advisory_lock_sql(CREATE_CLIENT))
    blocker.stdin.close()
    _wait_for_lock(container, waiting=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_psql, container, _create_sql(CREATE_CLIENT, create_reason), timeout=20) for _ in range(2)]
        _wait_for_lock(container, waiting=True)
        blocker.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    if blocker.returncode != 0 or any(result.returncode != 0 for result in results):
        raise GateFailure("concurrent_create_failed")
    identifiers = [result.stdout.strip() for result in results]
    if len(set(identifiers)) != 1 or not re.fullmatch(r"[0-9a-f-]{36}", identifiers[0]):
        raise GateFailure("concurrent_create_not_idempotent")
    counts = _require_success(
        _psql(
            container,
            f"""
SELECT count(*)::text || ':' ||
       (SELECT count(*) FROM public.scrape_uncertainty_review_events e
        JOIN public.scrape_uncertainty_review_requests r USING (review_id)
        WHERE r.client_request_id = '{CREATE_CLIENT}'::uuid)::text
FROM public.scrape_uncertainty_review_requests
WHERE client_request_id = '{CREATE_CLIENT}'::uuid;
""",
        ),
        "concurrent_create_count_failed",
    )
    if counts != "1:1":
        raise GateFailure("concurrent_create_count_mismatch")

    _require_success(
        _psql(container, _create_sql(DECISION_CLIENT, "A second pending request exists for concurrent decision verification.")),
        "concurrent_decision_setup_failed",
    )
    review_id_sql = (
        f"SELECT review_id FROM public.scrape_uncertainty_review_requests "
        f"WHERE client_request_id = '{DECISION_CLIENT}'::uuid"
    )
    blocker_sql = f"BEGIN; SELECT 1 FROM public.scrape_uncertainty_review_requests WHERE review_id = ({review_id_sql}) FOR UPDATE; SELECT pg_sleep(3); COMMIT;"
    blocker = _start_psql(container, blocker_sql)
    if blocker.stdin is None:
        raise GateFailure("concurrency_start_failed")
    blocker.stdin.write(blocker_sql)
    blocker.stdin.close()
    _wait_for_relation_lock(container, "public.scrape_uncertainty_review_requests")
    with ThreadPoolExecutor(max_workers=2) as pool:
        approve_future = pool.submit(_psql, container, _decision_sql("approve"), timeout=20)
        reject_future = pool.submit(_psql, container, _decision_sql("reject"), timeout=20)
        blocker.wait(timeout=10)
        decisions = [approve_future.result(timeout=20), reject_future.result(timeout=20)]
    if blocker.returncode != 0:
        raise GateFailure("concurrent_decision_blocker_failed")
    successes = [result for result in decisions if result.returncode == 0]
    failures = [result for result in decisions if result.returncode != 0]
    if len(successes) != 1 or len(failures) != 1 or "40001" not in failures[0].stderr:
        raise GateFailure("concurrent_decision_not_serialized")
    state = _require_success(
        _psql(
            container,
            f"""
SELECT status || ':' || version::text || ':' ||
       (SELECT count(*) FROM public.scrape_uncertainty_review_events e WHERE e.review_id = r.review_id)::text
FROM public.scrape_uncertainty_review_requests r
WHERE client_request_id = '{DECISION_CLIENT}'::uuid;
""",
        ),
        "concurrent_decision_state_failed",
    )
    if state not in {"approved:2:2", "rejected:2:2"}:
        raise GateFailure("concurrent_decision_state_mismatch")


def _container_absent(name: str) -> bool:
    result = _docker(
        "ps", "--all", "--filter", f"name=^/{name}$", "--format", "{{.Names}}", timeout=15,
    )
    return result.returncode == 0 and result.stdout.strip() == ""


def _write_report(report: dict[str, object]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(REPORT)


def run_gate() -> int:
    catalog = {key: False for key in CATALOG_CHECK_KEYS}
    behavioral = {key: False for key in BEHAVIORAL_CHECK_KEYS}
    cleanup = {"attempted": False, "container_absent": False}
    failure_code: str | None = None
    container = f"keiba-phase3g-{secrets.token_hex(8)}"
    local_context_confirmed = False
    migration_hash = ""
    commit_sha = ""

    try:
        for path in (MIGRATION, BOOTSTRAP, RUNTIME_CONTRACT):
            if not path.is_file():
                raise GateFailure("required_input_missing")
        migration_hash = _sha256(MIGRATION)
        commit_sha = _tested_commit_sha()

        context = _docker("context", "inspect", "--format", "{{(index .Endpoints \"docker\").Host}}", timeout=20)
        endpoint = _require_success(context, "docker_context_unavailable")
        if not endpoint.startswith(LOCAL_DOCKER_ENDPOINTS):
            raise GateFailure("remote_docker_context_rejected")
        local_context_confirmed = True
        daemon = _docker("version", "--format", "{{.Server.Version}}", timeout=20)
        _require_success(daemon, "docker_daemon_unavailable")
        _require_success(_docker("pull", IMAGE, timeout=180), "docker_image_unavailable")

        password = secrets.token_urlsafe(24)
        started_output = _require_success(
            _docker(
                "run", "--detach", "--name", container,
                "--network", "none",
                "--label", "keiba-ai-pro.phase3g-runtime=true",
                "--env", f"POSTGRES_DB={DATABASE}",
                "--env", "POSTGRES_USER=postgres",
                "--env", f"POSTGRES_PASSWORD={password}",
                "--pull", "never", IMAGE,
                timeout=30,
            ),
            "container_start_failed",
        )
        # A successful `docker run` may have created the named container even
        # when its returned identifier is malformed. Cleanup must start before
        # validating any output derived from that command.
        if not CONTAINER_ID_PATTERN.fullmatch(started_output):
            raise GateFailure("container_id_invalid")
        _wait_for_postgres(container)

        _require_success(_psql(container, BOOTSTRAP.read_text(encoding="utf-8")), "bootstrap_failed")
        migration_sql = MIGRATION.read_text(encoding="utf-8")
        _require_success(_psql(container, migration_sql), "migration_apply_failed")
        catalog["migration_compiles"] = True
        _require_success(_psql(container, migration_sql), "migration_replay_failed")

        _run_concurrency_checks(container)
        behavioral["concurrent_create_serialized"] = True
        behavioral["concurrent_decision_serialized"] = True

        contract = _require_success(
            _psql(container, RUNTIME_CONTRACT.read_text(encoding="utf-8"), timeout=120),
            "runtime_contract_failed",
        )
        markers = {
            line.strip()[len(MARKER_PREFIX):]
            for line in contract.splitlines()
            if line.strip().startswith(MARKER_PREFIX)
        }
        for key in CATALOG_CHECK_KEYS:
            if key != "migration_compiles":
                catalog[key] = key in markers
        for key in BEHAVIORAL_CHECK_KEYS:
            if key not in {"concurrent_create_serialized", "concurrent_decision_serialized"}:
                behavioral[key] = key in markers
        if not all(catalog.values()) or not all(behavioral.values()):
            raise GateFailure("runtime_markers_incomplete")
    except GateFailure as exc:
        failure_code = exc.code
    except Exception:
        failure_code = "unexpected_gate_failure"
    finally:
        cleanup["attempted"] = local_context_confirmed
        if local_context_confirmed:
            # A failed `docker run` can still leave the named container behind.
            # The randomized name is ours, so removal is safe even when output
            # validation never confirmed a usable container identifier.
            try:
                _docker("rm", "--force", container, timeout=30)
            except GateFailure:
                pass
            try:
                cleanup["container_absent"] = _container_absent(container)
            except GateFailure:
                cleanup["container_absent"] = False
        if not cleanup["container_absent"] and failure_code is None:
            failure_code = "container_cleanup_failed"

    success = failure_code is None and all(catalog.values()) and all(behavioral.values()) and cleanup["container_absent"]
    report: dict[str, object] = {
        "schema_version": 1,
        "evidence_mode": "synthetic",
        "environment": "ci-disposable",
        "database_scope": "disposable_docker",
        "network_mode": "none",
        "image": IMAGE,
        "tested_commit_sha": commit_sha,
        "migration_sha256": migration_hash,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "synthetic": True,
        "l3_eligible": False,
        "success": success,
        "catalog_checks": catalog,
        "behavioral_checks": behavioral,
        "cleanup": cleanup,
    }
    if failure_code is not None:
        report["failure_code"] = failure_code
    try:
        _write_report(report)
    except Exception:
        return 1
    return 0 if success else 1


def main() -> None:
    raise SystemExit(run_gate())


if __name__ == "__main__":
    main()

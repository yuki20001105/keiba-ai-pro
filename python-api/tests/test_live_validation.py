from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.routing import APIRoute

sys.path.insert(0, "python-api")

import main  # type: ignore  # noqa: E402
from deps import auth as deps_auth  # type: ignore  # noqa: E402
from middleware import auth as middleware_auth  # type: ignore  # noqa: E402
from routers import live_validation as live_router  # type: ignore  # noqa: E402
from services.live_validation import (  # type: ignore  # noqa: E402
    LiveValidationCoordinator,
    LiveValidationRequest,
    LiveValidationResponse,
    LiveValidationService,
    LiveValidationServiceError,
    _project_validation_report,
)

ROOT = Path(__file__).resolve().parents[2]


def _planner_sample() -> dict[str, Any]:
    return {
        "url": "https://db.netkeiba.com/race/202601010101/",
        "url_type": "result_page",
        "race_id": "202601010101",
        "horse_id": None,
        "reason": "true-missing",
        "column": "finish_position",
        "priority": "P0",
        "source": "ultimate-db",
        "recommended_next_action": "targeted refetch live validation",
    }


def _planner_report() -> dict[str, Any]:
    return {
        "target": "all",
        "verdict": "warn",
        "verdict_reason": "targeted-refetch-dry-run",
        "unique_url_count": 1,
        "sample_urls": {
            "result_page": [_planner_sample()],
            "race_detail": [],
            "horse_detail": [],
            "pedigree": [],
        },
        "safety_flags": {
            "read_only": True,
            "no_db_write": True,
            "no_http_access": True,
            "no_scrape_execute": True,
            "no_upsert": True,
            "no_force_refresh_execute": True,
        },
    }


def _validation_report() -> dict[str, Any]:
    return {
        "input_refetch_plan": "must-not-be-projected",
        "target": "all",
        "url_type": "all",
        "max_urls": 1,
        "max_urls_applied": 1,
        "attempted_url_count": 1,
        "http_success_count": 1,
        "http_error_count": 0,
        "parse_success_count": 1,
        "parse_failed_count": 0,
        "would_fix_count": 1,
        "would_not_fix_count": 0,
        "required_field_missing_count": 9,
        "no_downgrade_skip_count": 0,
        "repairable_from_live_count": 1,
        "elapsed_seconds": 0.25,
        "estimated_full_refetch_runtime_seconds": 0.25,
        "excluded_schema_review_count": 0,
        "excluded_domain_allowed_count": 0,
        "excluded_metadata_repair_count": 0,
        "excluded_cache_available_count": 0,
        "excluded_unsafe_url_count": 0,
        "fetch_metrics": {
            "network_requests": 1,
            "retry_count": 0,
            "backoff_count": 0,
        },
        "sample_results": [
            {
                "url": "https://db.netkeiba.com/race/202601010101/",
                "url_type": "result_page",
                "race_id": "202601010101",
                "horse_id": None,
                "http_status": 200,
                "parse_status": "parse_success",
                "missing_fields_before": ["finish_position"],
                "fields_found_after": ["finish_position"],
                "would_fix_columns": ["finish_position"],
                "action": "would-fix",
                "reason": "true-missing",
                "recommended_next_action": "targeted refetch live validation",
            }
        ],
        "recommended_next_actions": ["review the bounded result"],
        "rate_limit_policy": {
            "max_urls": 1,
            "max_supported_urls": 10,
            "min_interval_sec": 1.0,
            "max_retries": 1,
            "retry_base_sec": 0.0,
            "retry_jitter_sec": 0.0,
            "retry_after_enabled": False,
            "max_retry_after_sec": 0.0,
            "per_request_timeout_sec": 10.0,
            "total_timeout_sec": 45.0,
            "max_body_bytes": 2 * 1024 * 1024,
            "circuit_breaker": {"threshold": 3, "cooldown_sec": 120.0},
            "parallelism": 1,
            "fetch_pipeline_used": True,
        },
        "safety_flags": {
            "small_live_validation_only": True,
            "max_urls_limited": True,
            "no_db_write": True,
            "no_upsert": True,
            "no_repair_execute": True,
            "no_production_table_write": True,
            "no_force_refresh_execute": True,
            "no_bulk_refetch": True,
            "redirects_disabled": True,
            "bounded_response_body": True,
            "bounded_total_runtime": True,
        },
        "verdict": "pass",
        "verdict_reason": "small-live-validation",
    }


def _request_model() -> LiveValidationRequest:
    return LiveValidationRequest(
        target="all",
        url_type="all",
        max_urls=1,
        confirm_live_fetch=True,
    )


def _provision_runtime_prerequisites(tmp_path: Path) -> tuple[Path, Path]:
    data_dir = tmp_path / "runtime-data"
    input_dir = tmp_path / "runtime-inputs"
    data_dir.mkdir()
    input_dir.mkdir()
    main_db = sqlite3.connect(data_dir / "keiba_ultimate.db")
    main_db.execute("CREATE TABLE race_results_ultimate (race_id TEXT, data TEXT)")
    main_db.commit()
    main_db.close()
    cache_db = sqlite3.connect(data_dir / "fetch_cache.db")
    cache_db.execute(
        """
        CREATE TABLE http_cache (
            normalized_url TEXT PRIMARY KEY,
            final_url TEXT NOT NULL,
            expires_at REAL NOT NULL
        )
        """
    )
    cache_db.commit()
    cache_db.close()
    for filename in (
        "scrape_missingness_audit.json",
        "p0_scrape_repair_plan.json",
        "p0_cache_coverage_diagnosis.json",
    ):
        (input_dir / filename).write_text("{}", encoding="utf-8")
    return data_dir, input_dir


def _service_with_runtime(
    tmp_path: Path,
    runner,
) -> LiveValidationService:
    data_dir, input_dir = _provision_runtime_prerequisites(tmp_path)
    return LiveValidationService(
        project_root=ROOT,
        planner_script=ROOT / "scripts" / "plan_p0_targeted_refetch.py",
        validator_script=ROOT / "scripts" / "validate_p0_targeted_refetch_live.py",
        data_dir=data_dir,
        runtime_input_dir=input_dir,
        command_runner=runner,
    )


def _response_model(tmp_path: Path) -> LiveValidationResponse:
    async def runner(command: tuple[str, ...], _cwd: Path, _timeout: float, stage: str) -> None:
        output = Path(command[command.index("--output") + 1])
        payload = _planner_report() if stage == "planner" else _validation_report()
        output.write_text(json.dumps(payload), encoding="utf-8")

    service = _service_with_runtime(tmp_path, runner)
    return asyncio.run(service.run(_request_model()))


async def _api_request(method: str, path: str, *, token: str | None = None, json_body: Any = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, headers=headers, json=json_body)


def _run_api_request(method: str, path: str, *, token: str | None = None, json_body: Any = None) -> httpx.Response:
    return asyncio.run(_api_request(method, path, token=token, json_body=json_body))


@pytest.fixture(autouse=True)
def _auth_and_fresh_coordinator(monkeypatch: pytest.MonkeyPatch):
    middleware_auth.SUPABASE_URL = "http://127.0.0.1:54321"

    async def verify(token: str):
        role = "admin" if token == "admin" else "user"
        return {"sub": f"user-{token}", "app_metadata": {"role": role, "subscription_tier": "free"}}

    monkeypatch.setattr(middleware_auth, "verify_jwt", verify)
    monkeypatch.setattr(
        deps_auth,
        "_get_profile_from_db",
        lambda user_id: {"role": "admin" if user_id == "user-admin" else "user", "subscription_tier": "free"},
    )
    monkeypatch.setattr(live_router, "live_validation_coordinator", LiveValidationCoordinator())


def test_route_uses_admin_dependency_and_rejects_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    route = next(
        item
        for item in main.app.routes
        if isinstance(item, APIRoute) and item.path == "/api/scrape/live-validation"
    )
    assert any(dependency.call is deps_auth.require_admin for dependency in route.dependant.dependencies)

    class NeverRun:
        async def run(self, _request):
            raise AssertionError("service must not run for non-admin")

    monkeypatch.setattr(live_router, "live_validation_service", NeverRun())
    response = _run_api_request(
        "POST",
        "/api/scrape/live-validation",
        token="free",
        json_body={"target": "all", "url_type": "all", "max_urls": 1, "confirm_live_fetch": True},
    )
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"


def test_auth_and_request_validation_responses_are_no_store(monkeypatch: pytest.MonkeyPatch) -> None:
    class NeverRun:
        async def run(self, _request):
            raise AssertionError("invalid or unauthenticated request reached service")

    monkeypatch.setattr(live_router, "live_validation_service", NeverRun())
    unauthenticated = _run_api_request(
        "POST",
        "/api/scrape/live-validation",
        json_body={"target": "all", "url_type": "all", "max_urls": 1, "confirm_live_fetch": True},
    )
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["cache-control"] == "no-store"

    invalid = _run_api_request(
        "POST",
        "/api/scrape/live-validation",
        token="admin",
        json_body={"target": "all", "url_type": "all", "max_urls": 4, "confirm_live_fetch": True},
    )
    assert invalid.status_code == 422
    assert invalid.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "body",
    [
        {"target": "all", "url_type": "all", "max_urls": 1},
        {"target": "all", "url_type": "all", "max_urls": 1, "confirm_live_fetch": False},
        {"target": "all", "url_type": "all", "max_urls": True, "confirm_live_fetch": True},
        {"target": "../../secret", "url_type": "all", "max_urls": 1, "confirm_live_fetch": True},
        {
            "target": "all",
            "url_type": "all",
            "max_urls": 1,
            "confirm_live_fetch": True,
            "url": "https://attacker.invalid/",
        },
        {
            "target": "all",
            "url_type": "all",
            "max_urls": 1,
            "confirm_live_fetch": True,
            "fixture_json": "C:\\secret\\fixture.json",
        },
        {
            "target": "all",
            "url_type": "all",
            "max_urls": 1,
            "confirm_live_fetch": True,
            "cache_db": "/tmp/cache.db",
        },
        {
            "target": "all",
            "url_type": "all",
            "max_urls": 1,
            "confirm_live_fetch": True,
            "plan": {"sample_urls": []},
        },
    ],
)
def test_strict_request_rejects_missing_false_coerced_path_and_client_plan_keys(
    monkeypatch: pytest.MonkeyPatch,
    body: dict[str, Any],
) -> None:
    class NeverRun:
        async def run(self, _request):
            raise AssertionError("invalid request reached service")

    monkeypatch.setattr(live_router, "live_validation_service", NeverRun())
    response = _run_api_request("POST", "/api/scrape/live-validation", token="admin", json_body=body)
    assert response.status_code == 422


def test_missing_runtime_prerequisite_fails_503_before_any_command(tmp_path: Path) -> None:
    calls: list[str] = []

    async def runner(_command: tuple[str, ...], _cwd: Path, _timeout: float, stage: str) -> None:
        calls.append(stage)

    service = _service_with_runtime(tmp_path, runner)
    (tmp_path / "runtime-inputs" / "p0_scrape_repair_plan.json").unlink()

    with pytest.raises(LiveValidationServiceError) as exc:
        asyncio.run(service.run(_request_model()))
    assert exc.value.status_code == 503
    assert exc.value.detail == "live validation prerequisites are unavailable"
    assert calls == []


def test_relative_runtime_input_env_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, _input_dir = _provision_runtime_prerequisites(tmp_path)
    monkeypatch.setenv("LIVE_VALIDATION_INPUT_DIR", "relative/reports")
    service = LiveValidationService(
        project_root=ROOT,
        planner_script=ROOT / "scripts" / "plan_p0_targeted_refetch.py",
        validator_script=ROOT / "scripts" / "validate_p0_targeted_refetch_live.py",
        data_dir=data_dir,
        command_runner=lambda *_args: None,  # never reached
    )
    with pytest.raises(LiveValidationServiceError) as exc:
        asyncio.run(service.run(_request_model()))
    assert exc.value.status_code == 503


def test_missing_cache_uses_cleaned_request_scoped_empty_read_only_cache(tmp_path: Path) -> None:
    data_dir, input_dir = _provision_runtime_prerequisites(tmp_path)
    (data_dir / "fetch_cache.db").unlink()
    cache_paths: list[Path] = []

    async def runner(command: tuple[str, ...], _cwd: Path, _timeout: float, stage: str) -> None:
        cache_path = Path(command[command.index("--cache-db") + 1])
        cache_paths.append(cache_path)
        assert cache_path.is_file()
        connection = sqlite3.connect(f"{cache_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            assert connection.execute("SELECT COUNT(*) FROM http_cache").fetchone()[0] == 0
        finally:
            connection.close()
        output = Path(command[command.index("--output") + 1])
        output.write_text(
            json.dumps(_planner_report() if stage == "planner" else _validation_report()),
            encoding="utf-8",
        )

    service = LiveValidationService(
        project_root=ROOT,
        planner_script=ROOT / "scripts" / "plan_p0_targeted_refetch.py",
        validator_script=ROOT / "scripts" / "validate_p0_targeted_refetch_live.py",
        data_dir=data_dir,
        runtime_input_dir=input_dir,
        command_runner=runner,
    )
    response = asyncio.run(service.run(_request_model()))
    assert response.result.verdict == "pass"
    assert len(set(cache_paths)) == 1
    assert not cache_paths[0].exists()
    assert not (data_dir / "fetch_cache.db").exists()


def test_container_runtime_mount_contract_excludes_real_data_from_image() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "reports/" in dockerignore
    assert "keiba/data/" in dockerignore
    assert "COPY reports" not in dockerfile
    assert "COPY keiba/data" not in dockerfile
    assert "LIVE_VALIDATION_INPUT_DIR=/app/keiba/data/live-validation-inputs" in dockerfile
    assert "./keiba/data:/app/keiba/data" in compose
    assert "./reports:/app/keiba/data/live-validation-inputs:ro" in compose


def test_fixed_commands_projection_and_success_cleanup(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], Path, float, str]] = []
    workspaces: list[Path] = []

    async def runner(command: tuple[str, ...], cwd: Path, timeout: float, stage: str) -> None:
        calls.append((command, cwd, timeout, stage))
        output = Path(command[command.index("--output") + 1])
        workspaces.append(output.parent)
        if stage == "planner":
            for flag in ("--input-audit", "--input-p0-plan", "--input-cache-diagnosis"):
                snapshot = Path(command[command.index(flag) + 1])
                assert snapshot.parent == output.parent / "inputs"
                assert snapshot.read_text(encoding="utf-8") == "{}"
        if stage == "validator":
            planner_input = Path(command[command.index("--input-refetch-plan") + 1])
            assert planner_input.is_file()
        payload = _planner_report() if stage == "planner" else _validation_report()
        output.write_text(json.dumps(payload), encoding="utf-8")

    service = _service_with_runtime(tmp_path, runner)
    response = asyncio.run(service.run(_request_model())).model_dump()

    assert [stage for _, _, _, stage in calls] == ["planner", "validator"]
    planner_command, validator_command = calls[0][0], calls[1][0]
    assert planner_command[:2] == (sys.executable, str((ROOT / "scripts" / "plan_p0_targeted_refetch.py").resolve()))
    assert validator_command[:2] == (sys.executable, str((ROOT / "scripts" / "validate_p0_targeted_refetch_live.py").resolve()))
    assert "--fixture-json" not in validator_command
    assert "https://db.netkeiba.com/race/202601010101/" not in validator_command
    assert validator_command[validator_command.index("--cache-db") + 1] == str(
        (tmp_path / "runtime-data" / "fetch_cache.db").resolve()
    )
    assert all(cwd == ROOT.resolve() for _, cwd, _, _ in calls)
    assert len(set(workspaces)) == 1
    assert not workspaces[0].exists()

    assert response["live_validation"] is True
    assert response["bounded"] is True
    assert response["external_http"] is True
    assert response["read_only"] is True
    assert response["execution_enabled"] is False
    expected_result_keys = {
        "target",
        "url_type",
        "max_urls_applied",
        "attempted_url_count",
        "http_success_count",
        "http_error_count",
        "parse_success_count",
        "parse_error_count",
        "would_fix_count",
        "would_not_fix_count",
        "no_downgrade_count",
        "repairable_count",
        "excluded_schema_review_count",
        "excluded_domain_allowed_count",
        "excluded_metadata_repair_count",
        "excluded_cache_available_count",
        "elapsed_seconds",
        "estimated_full_refetch_runtime_seconds",
        "sample_results",
        "recommended_next_actions",
        "rate_limit_policy",
        "safety_flags",
        "verdict",
        "verdict_reason",
    }
    assert set(response["result"]) == expected_result_keys
    assert response["result"]["parse_error_count"] == 0
    assert response["result"]["no_downgrade_count"] == 0
    assert response["result"]["repairable_count"] == 1
    serialized = json.dumps(response)
    assert "input_refetch_plan" not in serialized
    assert "required_field_missing_count" not in serialized
    assert "must-not-be-projected" not in serialized


def test_zero_attempt_warn_is_valid_but_zero_attempt_pass_is_rejected() -> None:
    report = _validation_report()
    report.update(
        {
            "attempted_url_count": 0,
            "http_success_count": 0,
            "http_error_count": 0,
            "parse_success_count": 0,
            "parse_failed_count": 0,
            "would_fix_count": 0,
            "would_not_fix_count": 0,
            "no_downgrade_skip_count": 0,
            "repairable_from_live_count": 0,
            "sample_results": [],
            "verdict": "warn",
        }
    )
    report["fetch_metrics"]["network_requests"] = 0
    projected = _project_validation_report(report, _request_model())
    assert projected.verdict == "warn"
    assert projected.attempted_url_count == 0

    report["verdict"] = "pass"
    with pytest.raises(LiveValidationServiceError) as exc:
        _project_validation_report(report, _request_model())
    assert exc.value.status_code == 502
    assert exc.value.detail == "live validation verdict is inconsistent"


def test_sample_aggregates_are_recomputed_and_raw_count_mismatch_is_rejected() -> None:
    report = _validation_report()
    report["http_success_count"] = 0
    report["http_error_count"] = 1
    with pytest.raises(LiveValidationServiceError) as exc:
        _project_validation_report(report, _request_model())
    assert exc.value.status_code == 502
    assert exc.value.detail == "live validation result counts are inconsistent"


@pytest.mark.parametrize(
    "mutate_sample",
    [
        lambda sample: sample.update({"fields_found_after": ["horse_name"]}),
        lambda sample: sample.update(
            {"would_fix_columns": [], "action": "no-downgrade-skip"}
        ),
        lambda sample: sample.update(
            {"missing_fields_before": ["finish_position", "finish_position"]}
        ),
    ],
)
def test_would_fix_columns_must_exactly_match_missing_found_intersection(mutate_sample: Any) -> None:
    report = _validation_report()
    mutate_sample(report["sample_results"][0])
    with pytest.raises(LiveValidationServiceError) as exc:
        _project_validation_report(report, _request_model())
    assert exc.value.status_code == 502


def test_race_without_horse_data_requires_explicit_check_evidence() -> None:
    report = _validation_report()
    sample = report["sample_results"][0]
    sample.update(
        {
            "reason": "consistency:race_without_horse_data",
            "missing_fields_before": [
                "horse_id",
                "horse_name",
                "frame_number",
                "horse_number",
                "(check)",
            ],
            "fields_found_after": ["(check)"],
            "would_fix_columns": ["(check)"],
            "action": "would-fix",
        }
    )
    projected = _project_validation_report(report, _request_model())
    assert projected.sample_results[0].would_fix_columns == ["(check)"]

    sample["missing_fields_before"].remove("(check)")
    with pytest.raises(LiveValidationServiceError) as exc:
        _project_validation_report(report, _request_model())
    assert exc.value.status_code == 502


@pytest.mark.parametrize(
    ("mutate", "expected_detail"),
    [
        (
            lambda report: report["rate_limit_policy"].update({"max_retries": 2}),
            "live validation rate-limit policy is invalid",
        ),
        (
            lambda report: report["fetch_metrics"].update({"network_requests": 2}),
            "live validation network budget was exceeded",
        ),
        (
            lambda report: report["fetch_metrics"].update({"retry_count": 1}),
            "live validation network budget was exceeded",
        ),
    ],
)
def test_retry_policy_and_total_outbound_budget_are_fail_closed(mutate: Any, expected_detail: str) -> None:
    report = _validation_report()
    mutate(report)
    with pytest.raises(LiveValidationServiceError) as exc:
        _project_validation_report(report, _request_model())
    assert exc.value.status_code == 502
    assert exc.value.detail == expected_detail


def test_http_503_sample_cannot_claim_parse_success_or_pass() -> None:
    report = _validation_report()
    sample = report["sample_results"][0]
    sample.update(
        {
            "http_status": 503,
            "parse_status": "http_error",
            "fields_found_after": [],
            "would_fix_columns": [],
            "action": "http_error",
        }
    )
    with pytest.raises(LiveValidationServiceError) as aggregate_mismatch:
        _project_validation_report(report, _request_model())
    assert aggregate_mismatch.value.status_code == 502
    assert aggregate_mismatch.value.detail == "live validation result counts are inconsistent"

    report.update(
        {
            "http_success_count": 0,
            "http_error_count": 1,
            "parse_success_count": 0,
            "parse_failed_count": 1,
            "would_fix_count": 0,
            "would_not_fix_count": 1,
            "repairable_from_live_count": 0,
            "verdict": "pass",
        }
    )
    with pytest.raises(LiveValidationServiceError) as exc:
        _project_validation_report(report, _request_model())
    assert exc.value.status_code == 502
    assert exc.value.detail == "live validation verdict is inconsistent"

    report["verdict"] = "warn"
    projected = _project_validation_report(report, _request_model())
    assert projected.http_error_count == 1
    assert projected.parse_error_count == 1
    assert projected.verdict == "warn"


def test_partial_parse_success_remains_pass_when_samples_and_counts_agree() -> None:
    request = LiveValidationRequest(
        target="all",
        url_type="all",
        max_urls=2,
        confirm_live_fetch=True,
    )
    report = _validation_report()
    error_sample = json.loads(json.dumps(report["sample_results"][0]))
    error_sample.update(
        {
            "url": "https://db.netkeiba.com/race/202601010102/",
            "race_id": "202601010102",
            "http_status": 503,
            "parse_status": "http_error",
            "fields_found_after": [],
            "would_fix_columns": [],
            "action": "http_error",
        }
    )
    report.update(
        {
            "max_urls": 2,
            "max_urls_applied": 2,
            "attempted_url_count": 2,
            "http_success_count": 1,
            "http_error_count": 1,
            "parse_success_count": 1,
            "parse_failed_count": 1,
            "would_fix_count": 1,
            "would_not_fix_count": 1,
            "repairable_from_live_count": 1,
            "sample_results": [report["sample_results"][0], error_sample],
            "verdict": "pass",
        }
    )
    report["rate_limit_policy"]["max_urls"] = 2
    report["fetch_metrics"]["network_requests"] = 2
    projected = _project_validation_report(report, request)
    assert projected.verdict == "pass"
    assert projected.parse_success_count == 1
    assert projected.parse_error_count == 1


@pytest.mark.parametrize(
    ("failure_stage", "expected_status"),
    [("planner", 504), ("validator", 502)],
)
def test_workspace_cleanup_on_command_and_report_failures(
    tmp_path: Path,
    failure_stage: str,
    expected_status: int,
) -> None:
    workspaces: list[Path] = []

    async def runner(command: tuple[str, ...], _cwd: Path, _timeout: float, stage: str) -> None:
        output = Path(command[command.index("--output") + 1])
        workspaces.append(output.parent)
        if stage == failure_stage:
            if stage == "planner":
                raise LiveValidationServiceError(504, "live validation timed out")
            output.write_text("not-json", encoding="utf-8")
            return
        output.write_text(json.dumps(_planner_report()), encoding="utf-8")

    service = _service_with_runtime(tmp_path, runner)
    with pytest.raises(LiveValidationServiceError) as exc:
        asyncio.run(service.run(_request_model()))
    assert exc.value.status_code == expected_status
    assert workspaces
    assert all(not workspace.exists() for workspace in workspaces)


def test_planner_preflight_blocks_hidden_or_unsafe_urls_before_validator(tmp_path: Path) -> None:
    planner = _planner_report()
    planner["url_candidates"] = [{"url": "https://attacker.invalid/"}]
    stages: list[str] = []

    async def runner(command: tuple[str, ...], _cwd: Path, _timeout: float, stage: str) -> None:
        stages.append(stage)
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps(planner), encoding="utf-8")

    service = _service_with_runtime(tmp_path, runner)
    with pytest.raises(LiveValidationServiceError) as exc:
        asyncio.run(service.run(_request_model()))
    assert exc.value.status_code == 502
    assert stages == ["planner"]


def test_single_flight_and_per_user_cooldown() -> None:
    now = [100.0]
    coordinator = LiveValidationCoordinator(cooldown_seconds=60.0, clock=lambda: now[0])
    coordinator.acquire("alice")
    with pytest.raises(LiveValidationServiceError) as busy:
        coordinator.acquire("bob")
    assert busy.value.status_code == 409

    coordinator.release("alice")
    with pytest.raises(LiveValidationServiceError) as cooling_down:
        coordinator.acquire("alice")
    assert cooling_down.value.status_code == 429
    assert cooling_down.value.headers == {"Retry-After": "60"}

    now[0] += 61.0
    coordinator.acquire("alice")
    coordinator.release("alice")


@pytest.mark.parametrize(("mode", "status"), [("busy", 409), ("cooldown", 429)])
def test_api_maps_single_flight_and_cooldown_without_running_service(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    status: int,
) -> None:
    now = [100.0]
    coordinator = LiveValidationCoordinator(cooldown_seconds=60.0, clock=lambda: now[0])
    if mode == "busy":
        coordinator.acquire("another-user")
    else:
        coordinator.acquire("user-admin")
        coordinator.release("user-admin")

    class NeverRun:
        async def run(self, _request):
            raise AssertionError("guarded request reached service")

    monkeypatch.setattr(live_router, "live_validation_coordinator", coordinator)
    monkeypatch.setattr(live_router, "live_validation_service", NeverRun())
    response = _run_api_request(
        "POST",
        "/api/scrape/live-validation",
        token="admin",
        json_body={"target": "all", "url_type": "all", "max_urls": 1, "confirm_live_fetch": True},
    )
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    if mode == "cooldown":
        assert response.headers["retry-after"] == "60"


@pytest.mark.parametrize(
    ("status", "detail"),
    [
        (500, "live validation planner failed"),
        (502, "live validation result is invalid"),
        (504, "live validation timed out"),
    ],
)
def test_service_error_status_and_safe_detail_mapping(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    detail: str,
) -> None:
    class FailingService:
        async def run(self, _request):
            raise LiveValidationServiceError(status, detail)

    monkeypatch.setattr(live_router, "live_validation_service", FailingService())
    response = _run_api_request(
        "POST",
        "/api/scrape/live-validation",
        token="admin",
        json_body={"target": "all", "url_type": "all", "max_urls": 1, "confirm_live_fetch": True},
    )
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == "no-store"


def test_api_success_is_no_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    response_model = _response_model(tmp_path)

    class SuccessfulService:
        async def run(self, _request):
            return response_model

    monkeypatch.setattr(live_router, "live_validation_service", SuccessfulService())
    response = _run_api_request(
        "POST",
        "/api/scrape/live-validation",
        token="admin",
        json_body={"target": "all", "url_type": "all", "max_urls": 1, "confirm_live_fetch": True},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["result"]["repairable_count"] == 1

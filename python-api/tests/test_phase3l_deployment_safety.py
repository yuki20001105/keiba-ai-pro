from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
PYTHON_API = ROOT / "python-api"
sys.path.insert(0, str(PYTHON_API))

import app_config  # noqa: E402
import main  # noqa: E402
import scheduler  # noqa: E402


def test_scheduler_is_disabled_by_default_and_for_ambiguous_values() -> None:
    assert scheduler.scheduler_enabled({}) is False
    for value in ("", "false", "0", "no", "off", "on", "enabled", "maybe"):
        assert scheduler.scheduler_enabled({"SCHEDULER_ENABLED": value}) is False


@pytest.mark.parametrize("value", ["true", "TRUE", " 1 ", "yes"])
def test_scheduler_requires_an_explicit_recognized_opt_in(value: str) -> None:
    assert scheduler.scheduler_enabled(
        {"SCHEDULER_ENABLED": value, "APP_ENV": "test"}
    ) is True


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_legacy_scheduler_is_forbidden_in_deployed_environments(app_env: str) -> None:
    assert scheduler.scheduler_enabled(
        {"SCHEDULER_ENABLED": "true", "APP_ENV": app_env}
    ) is False


def test_scheduler_fails_closed_for_an_ambiguous_managed_runtime() -> None:
    assert scheduler.scheduler_enabled(
        {"SCHEDULER_ENABLED": "true", "RENDER": "true"}
    ) is False


def test_railway_scheduler_fails_closed_without_app_env() -> None:
    assert scheduler.scheduler_enabled(
        {
            "SCHEDULER_ENABLED": "true",
            "RAILWAY_ENVIRONMENT_NAME": "production",
        }
    ) is False


def test_scheduler_default_cannot_construct_a_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    monkeypatch.setattr(scheduler, "_APSCHEDULER_AVAILABLE", True)

    def unexpected_constructor(*args: object, **kwargs: object) -> None:
        raise AssertionError("scheduler must remain disabled without explicit opt-in")

    monkeypatch.setattr(
        scheduler,
        "AsyncIOScheduler",
        unexpected_constructor,
        raising=False,
    )
    scheduler.start_scheduler()
    assert scheduler._scheduler is None


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_deployed_scheduler_opt_in_cannot_construct_a_scheduler(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
) -> None:
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setattr(scheduler, "_APSCHEDULER_AVAILABLE", True)

    def unexpected_constructor(*args: object, **kwargs: object) -> None:
        raise AssertionError("legacy scheduler must not start in a deployed environment")

    monkeypatch.setattr(
        scheduler,
        "AsyncIOScheduler",
        unexpected_constructor,
        raising=False,
    )
    scheduler.start_scheduler()
    assert scheduler._scheduler is None


@pytest.mark.parametrize("value", [None, "", "development", "dev", "local", "test"])
def test_local_and_test_app_env_behavior_remains_development(value: str | None) -> None:
    environ = {} if value is None else {"APP_ENV": value}
    assert app_config.resolve_app_env(environ) == "development"


@pytest.mark.parametrize("marker", app_config._MANAGED_RUNTIME_MARKERS)
def test_managed_runtime_without_app_env_fails_closed(marker: str) -> None:
    with pytest.raises(app_config.DeploymentConfigurationError, match="app-env-missing"):
        app_config.resolve_app_env({marker: "present"})


@pytest.mark.parametrize("value", ["prod", "stage", "live", "unknown", "production-ish"])
def test_ambiguous_app_env_never_falls_back_to_development(value: str) -> None:
    with pytest.raises(app_config.DeploymentConfigurationError, match="app-env-invalid"):
        app_config.resolve_app_env({"APP_ENV": value, "RENDER": "true"})


@pytest.mark.parametrize("value", ["development", "dev", "local", "test"])
def test_managed_runtime_cannot_masquerade_as_local(value: str) -> None:
    with pytest.raises(app_config.DeploymentConfigurationError, match="app-env-invalid"):
        app_config.resolve_app_env({"APP_ENV": value, "RENDER": "true"})


@pytest.mark.parametrize("value", ["production", "staging"])
def test_deployed_app_env_requires_and_accepts_exact_names(value: str) -> None:
    assert app_config.resolve_app_env({"APP_ENV": value, "RENDER": "true"}) == value


def _clean_deployment_environment() -> dict[str, str]:
    environ = os.environ.copy()
    for name in (
        "APP_ENV",
        "ALLOWED_ORIGINS",
        *app_config._MANAGED_RUNTIME_MARKERS,
    ):
        environ.pop(name, None)
    environ["PYTHONUTF8"] = "1"
    return environ


def test_managed_runtime_import_actually_stops_when_app_env_is_missing() -> None:
    environ = _clean_deployment_environment()
    environ["RENDER"] = "true"
    result = subprocess.run(
        [sys.executable, "-c", "import app_config"],
        cwd=PYTHON_API,
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0
    assert "app-env-missing" in result.stderr


def test_explicit_production_import_uses_the_public_https_allowlist() -> None:
    environ = _clean_deployment_environment()
    environ.update(
        {
            "RENDER": "true",
            "APP_ENV": "production",
            "ALLOWED_ORIGINS": "https://keiba-ai-pro.vercel.app",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import app_config; "
                "assert app_config.APP_ENV == 'production'; "
                "assert app_config.ALLOWED_ORIGINS == "
                "('https://keiba-ai-pro.vercel.app',)"
            ),
        ],
        cwd=PYTHON_API,
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_development_has_safe_local_cors_defaults() -> None:
    assert app_config.resolve_allowed_origins("development", {}) == (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    )


def test_deployed_cors_allowlist_is_required() -> None:
    for app_env in ("staging", "production"):
        with pytest.raises(
            app_config.DeploymentConfigurationError,
            match="allowed-origins-missing",
        ):
            app_config.resolve_allowed_origins(app_env, {})


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "http://example.com",
        "http://localhost:3000",
        "https://127.0.0.1:3000",
        "https://10.0.0.8",
        "https://frontend.local",
        "https://user:password@example.com",
        "https://example.com/path",
        "https://example.com?query=1",
        "file:///tmp/frontend",
    ],
)
def test_deployed_cors_rejects_wildcard_local_and_non_origin_values(origin: str) -> None:
    with pytest.raises(app_config.DeploymentConfigurationError):
        app_config.resolve_allowed_origins(
            "production",
            {"ALLOWED_ORIGINS": origin},
        )


def test_deployed_cors_accepts_only_normalized_public_https_origins() -> None:
    assert app_config.resolve_allowed_origins(
        "staging",
        {
            "ALLOWED_ORIGINS": (
                "https://preview.example.com/, https://preview.example.com,"
                "https://admin.example.com"
            )
        },
    ) == ("https://preview.example.com", "https://admin.example.com")


def test_main_uses_the_resolved_cors_allowlist() -> None:
    cors = next(
        middleware
        for middleware in main.app.user_middleware
        if middleware.cls.__name__ == "CORSMiddleware"
    )
    assert cors.kwargs["allow_origins"] == list(app_config.ALLOWED_ORIGINS)
    assert "*" not in cors.kwargs["allow_origins"]


def test_render_blueprint_has_explicit_fail_closed_production_defaults() -> None:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    assert isinstance(blueprint, dict)
    services = blueprint.get("services")
    assert isinstance(services, list) and len(services) == 1
    service = services[0]
    assert service["type"] == "web"
    assert service["healthCheckPath"] == "/health"
    assert "--reload" not in service["startCommand"]

    env_vars = {entry["key"]: entry for entry in service["envVars"]}
    expected_values = {
        "APP_ENV": "production",
        "SCHEDULER_ENABLED": "false",
        "SUPABASE_DATA_ENABLED": "false",
        "NETKEIBA_RACE_WRITE_ENABLED": "false",
        "ALLOW_STAGING_WRITE": "false",
        "PRED_LIMIT_ALLOW_FAIL_OPEN": "false",
        "PHASE3J_SAGA_RUNTIME_MODE": "disabled",
        "PHASE3J_REMOTE_EFFECTS_ENABLED": "false",
        "PHASE3J_WORKER_DISPATCH_ENABLED": "false",
        "PHASE3J_EXECUTION_UNLOCK_ENABLED": "false",
        "ALLOWED_ORIGINS": "https://keiba-ai-pro.vercel.app",
    }
    for key, expected in expected_values.items():
        assert env_vars[key]["value"] == expected

    origins = app_config.resolve_allowed_origins(
        "production",
        {"ALLOWED_ORIGINS": env_vars["ALLOWED_ORIGINS"]["value"]},
    )
    assert origins == ("https://keiba-ai-pro.vercel.app",)

    supabase_url = urlsplit(env_vars["SUPABASE_URL"]["value"])
    assert supabase_url.scheme == "https"
    assert supabase_url.hostname
    assert supabase_url.username is None and supabase_url.password is None
    assert env_vars["SUPABASE_SERVICE_KEY"] == {
        "key": "SUPABASE_SERVICE_KEY",
        "sync": False,
    }


def _workflow_triggers(workflow: dict[object, object]) -> dict[str, object]:
    # PyYAML 1.1 may decode the key ``on`` as boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return triggers


def test_staging_evidence_runs_only_from_immutable_trusted_producer() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "staging-evidence.yml").read_text(
            encoding="utf-8"
        )
    )
    inputs = _workflow_triggers(workflow)["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"expected_commit", "trusted_producer_sha", "max_age_seconds"}
    assert inputs["trusted_producer_sha"]["required"] is True
    trusted_ref = "refs/heads/security/phase3n-trusted-producer-v1"
    assert workflow["env"]["TRUSTED_REF"] == trusted_ref

    jobs = workflow["jobs"]
    context_steps = jobs["context-gate"]["steps"]
    checkout = context_steps[0]
    assert checkout["uses"] == "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
    assert checkout["with"]["ref"] == "${{ inputs.trusted_producer_sha }}"
    assert checkout["with"]["persist-credentials"] is False

    gate = next(
        step
        for step in context_steps
        if step.get("name") == "Fail closed unless producer and candidate are independently bound"
    )["run"]
    for required in (
        '[[ "$GITHUB_REF" == "$TRUSTED_REF" ]]',
        '[[ "$GITHUB_WORKFLOW_REF" == "$GITHUB_REPOSITORY/.github/workflows/staging-evidence.yml@$TRUSTED_REF" ]]',
        '[[ "$GITHUB_SHA" == "$TRUSTED_PRODUCER_SHA" ]]',
        '[[ "$(git rev-parse origin/develop)" == "$EXPECTED_COMMIT" ]]',
        'git diff --quiet "$TRUSTED_PRODUCER_SHA" "$EXPECTED_COMMIT"',
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        ".github/workflows/staging-evidence.yml",
        "scripts/verify_phase3h_production_readiness.py",
        "scripts/security/verify_phase3n_staging_evidence.py",
        "scripts/security/build_phase3n_staging_evidence.py",
    ):
        assert required in gate

    for job_name in ("staging-observation", "verify-trusted-evidence"):
        job_checkouts = [
            step for step in jobs[job_name]["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")
        ]
        assert len(job_checkouts) == 1
        assert job_checkouts[0]["with"]["ref"] == "${{ inputs.trusted_producer_sha }}"
        assert job_checkouts[0]["with"]["persist-credentials"] is False


def test_ci_requires_fixed_trusted_attestation_for_main_promotion() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    triggers = _workflow_triggers(workflow)
    assert set(triggers["push"]["branches"]) == {"main", "release"}
    assert workflow["permissions"] == {"actions": "read", "contents": "read"}

    jobs = workflow["jobs"]
    python_steps = jobs["python-static-and-tests"]["steps"]
    assert any(
        "test_phase3l_deployment_safety.py" in step.get("run", "")
        for step in python_steps
    )
    assert any(
        "test_phase3n_operational_saga_runtime.py" in step.get("run", "")
        for step in python_steps
    )
    assert any(
        "test_phase3n_staging_evidence_gate.py" in step.get("run", "")
        for step in python_steps
    )

    readiness_steps = jobs["phase3h-production-readiness"]["steps"]
    readiness_checkout = next(
        step
        for step in readiness_steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert readiness_checkout["with"]["fetch-depth"] == 0
    assert readiness_checkout["with"]["persist-credentials"] is False

    resolver_steps = [
        step
        for step in readiness_steps
        if step.get("name") == "Resolve exact promotion candidate and trusted attestation run"
    ]
    assert len(resolver_steps) == 1
    resolver = resolver_steps[0]
    assert "actions/runs/$STAGING_EVIDENCE_RUN_ID" in resolver["run"]
    assert "develop -> main" in resolver["run"]
    assert '.path == ".github/workflows/staging-evidence.yml"' in resolver["run"]
    assert '.head_branch == "security/phase3n-trusted-producer-v1"' in resolver["run"]
    assert ".head_sha == $producer" in resolver["run"]
    assert ".run_attempt >= 1" in resolver["run"]
    assert "^{tree}" in resolver["run"]
    for required in (
        "git fetch --no-tags --prune origin main:refs/remotes/origin/main",
        'git show -s --format=%P "$GITHUB_SHA"',
        '"${#parents[@]}" -ne 2',
        '"${parents[0]}" != "$(git rev-parse origin/main)"',
        '"${parents[1]}" != "$PR_HEAD_SHA"',
        'git rev-parse "$GITHUB_SHA^{tree}"',
        'git rev-parse "$PR_HEAD_SHA^{tree}"',
    ):
        assert required in resolver["run"]
    assert resolver["env"]["STAGING_EVIDENCE_RUN_ID"] == "${{ vars.PHASE3N_STAGING_EVIDENCE_RUN_ID }}"
    assert resolver["env"]["TRUSTED_PRODUCER_SHA"] == "${{ vars.PHASE3N_TRUSTED_PRODUCER_SHA }}"
    assert 'echo "run_attempt=$run_attempt"' in resolver["run"]
    assert 'echo "producer_sha=$TRUSTED_PRODUCER_SHA"' in resolver["run"]

    downloads = [
        step
        for step in readiness_steps
        if step.get("name") == "Download trusted Phase3N staging attestation"
    ]
    assert len(downloads) == 1
    assert downloads[0]["with"]["name"] == "phase3n-staging-evidence-json"
    assert downloads[0]["with"]["run-id"] == "${{ steps.trusted-attestation.outputs.run_id }}"
    assert downloads[0]["with"]["github-token"] == "${{ github.token }}"

    provenance = next(
        step
        for step in readiness_steps
        if step.get("name") == "Verify GitHub-signed staging evidence provenance"
    )
    assert "gh attestation verify" in provenance["run"]
    assert "phase3n_staging_evidence_gate.json" in provenance["run"]
    assert "--signer-workflow" in provenance["run"]
    assert "--source-ref" in provenance["run"]
    assert "--source-digest" in provenance["run"]
    assert "--signer-digest" in provenance["run"]
    assert "--deny-self-hosted-runners" in provenance["run"]

    promotion_steps = [
        step
        for step in readiness_steps
        if step.get("name") == "Require trusted READY evidence for promotion"
    ]
    assert len(promotion_steps) == 1
    promotion = promotion_steps[0]["run"]
    assert "--trusted-attestation" in promotion
    assert "--expected-attestation-run-id" in promotion
    assert "--expected-attestation-run-attempt" in promotion
    assert "--expected-repository" in promotion
    assert "--expected-repository-id" in promotion
    assert "--require-ready" in promotion

    high_trust_uses = [step["uses"] for step in readiness_steps if "uses" in step]
    assert all(not value.endswith(("@v4", "@v5")) for value in high_trust_uses)

    for job_name in ("phase3i-saga-failure-injection", "phase3j-saga-outbox-runtime"):
        condition = jobs[job_name]["if"]
        assert "github.base_ref == 'main'" in condition
        assert "github.ref_name == 'main'" in condition


def test_legacy_daily_scrape_workflow_is_a_manual_fail_closed_tombstone() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "daily-scrape.yml").read_text(
            encoding="utf-8"
        )
    )
    triggers = _workflow_triggers(workflow)
    assert set(triggers) == {"workflow_dispatch"}
    assert "schedule" not in triggers
    assert workflow["permissions"] == {"contents": "read"}

    jobs = workflow["jobs"]
    assert set(jobs) == {"unsafe-production-write-blocked"}
    steps = jobs["unsafe-production-write-blocked"]["steps"]
    assert len(steps) == 1
    assert "exit 1" in steps[0]["run"]
    rendered = yaml.safe_dump(workflow, sort_keys=True)
    assert "run_scrape.py" not in rendered
    assert "SUPABASE_SERVICE_KEY" not in rendered


def test_release_workflow_authorizes_only_exact_attested_main_merge() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
    )
    assert set(_workflow_triggers(workflow)) == {"workflow_dispatch"}
    inputs = _workflow_triggers(workflow)["workflow_dispatch"]["inputs"]
    assert set(inputs) == {
        "production_commit",
        "attested_candidate_sha",
        "staging_evidence_run_id",
    }
    assert all(value["required"] is True for value in inputs.values())
    assert workflow["permissions"] == {"actions": "read", "contents": "read"}
    jobs = workflow["jobs"]
    assert set(jobs) == {"production-release-authorization"}
    job = jobs["production-release-authorization"]
    assert job["environment"] == "production-release"
    steps = job["steps"]

    merge_gate = next(step for step in steps if step.get("name") == "Verify exact main merge and attested tree")
    assert "GITHUB_REF_NAME" in merge_gate["run"]
    assert '"$PRODUCTION_COMMIT" != "$GITHUB_SHA"' in merge_gate["run"]
    assert "git rev-parse origin/main" in merge_gate["run"]
    assert '"$STAGING_EVIDENCE_RUN_ID" != "$EXPECTED_STAGING_EVIDENCE_RUN_ID"' in merge_gate["run"]
    assert "${parents[1]}" in merge_gate["run"]
    assert "^{tree}" in merge_gate["run"]

    run_gate = next(
        step
        for step in steps
        if step.get("name") == "Verify selected staging evidence run provenance"
    )
    for required in (
        "actions/runs/$STAGING_EVIDENCE_RUN_ID",
        '.path == ".github/workflows/staging-evidence.yml"',
        ".head_sha == $producer",
        '.head_branch == "security/phase3n-trusted-producer-v1"',
        '.event == "workflow_dispatch"',
        '.conclusion == "success"',
        ".run_attempt >= 1",
        'echo "run_attempt=$(jq -r',
    ):
        assert required in run_gate["run"]

    download = next(step for step in steps if step.get("name") == "Download trusted Phase3N staging attestation")
    assert download["with"]["name"] == "phase3n-staging-evidence-json"
    assert download["with"]["run-id"] == "${{ inputs.staging_evidence_run_id }}"
    assert download["with"]["github-token"] == "${{ github.token }}"

    provenance = next(
        step
        for step in steps
        if step.get("name") == "Verify GitHub-signed staging evidence provenance"
    )
    assert "gh attestation verify" in provenance["run"]
    assert "phase3n_staging_evidence_gate.json" in provenance["run"]
    assert "--signer-workflow" in provenance["run"]
    assert "--source-ref" in provenance["run"]
    assert "--source-digest" in provenance["run"]
    assert "--signer-digest" in provenance["run"]
    assert "--deny-self-hosted-runners" in provenance["run"]

    readiness = next(step for step in steps if step.get("name") == "Require trusted Phase3H READY decision")
    assert "--trusted-attestation" in readiness["run"]
    assert "--expected-attestation-run-id" in readiness["run"]
    assert "--expected-attestation-run-attempt" in readiness["run"]
    assert "--expected-repository" in readiness["run"]
    assert "--expected-repository-id" in readiness["run"]
    assert "--require-ready" in readiness["run"]

    high_trust_uses = [step["uses"] for step in steps if "uses" in step]
    assert all(not value.endswith(("@v4", "@v5")) for value in high_trust_uses)

    rendered = yaml.safe_dump(workflow, sort_keys=True).lower()
    for forbidden in (
        "softprops/action-gh-release",
        "vercel --prod",
        "railway up",
        "render deploy",
        "supabase db push",
    ):
        assert forbidden not in rendered


def _production_template_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in (ROOT / ".env.production.template").read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        assert separator == "=", f"invalid environment template line: {raw_line!r}"
        assert key and key not in values, f"duplicate environment key: {key!r}"
        values[key] = value
    return values


def test_production_template_uses_canonical_urls_and_safe_switches() -> None:
    values = _production_template_values()
    assert values["APP_ENV"] == "production"
    for key in ("ML_API_URL", "SCRAPE_API_URL", "NEXT_PUBLIC_API_URL"):
        parsed = urlsplit(values[key])
        assert parsed.scheme == "https"
        assert parsed.hostname and not app_config._is_local_hostname(parsed.hostname)
        assert parsed.path in {"", "/"} and not parsed.query and not parsed.fragment

    for key in (
        "NETKEIBA_RACE_WRITE_ENABLED",
        "ALLOW_STAGING_WRITE",
        "PRED_LIMIT_ALLOW_FAIL_OPEN",
        "SCHEDULER_ENABLED",
        "PHASE3J_REMOTE_EFFECTS_ENABLED",
        "PHASE3J_WORKER_DISPATCH_ENABLED",
        "PHASE3J_EXECUTION_UNLOCK_ENABLED",
    ):
        assert values[key] == "false"
    assert values["PHASE3J_SAGA_RUNTIME_MODE"] == "disabled"
    assert "NEXT_PUBLIC_ML_API_URL" not in values
    assert "NEXT_PUBLIC_SCRAPING_API_URL" not in values


def test_phase3l_document_cannot_self_claim_ready_or_l3() -> None:
    document = (ROOT / "docs" / "phase3l_staging_readiness_gate.md").read_text(
        encoding="utf-8"
    )
    assert "L2" in document
    assert "Production NOT_READY" in document
    assert "production_ready=false" in document
    assert "l3_eligible=false" in document
    assert "External changes performed by Phase 3L-A: **0**" in document
    assert "production_ready=true" not in document
    assert "l3_eligible=true" not in document


def test_all_remote_write_flags_remain_explicit_opt_in() -> None:
    for name in (
        "SUPABASE_DATA_ENABLED",
        "NETKEIBA_RACE_WRITE_ENABLED",
        "ALLOW_STAGING_WRITE",
    ):
        assert app_config._explicit_opt_in(name, {}) is False
        assert app_config._explicit_opt_in(name, {name: "maybe"}) is False
        assert app_config._explicit_opt_in(name, {name: "true"}) is True

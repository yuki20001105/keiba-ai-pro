from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python-api"))

from scraping.saga_runtime_config import (  # noqa: E402
    SagaRuntimeConfig,
    SagaRuntimeConfigError,
    SagaRuntimeMode,
    load_saga_runtime_config,
)


def test_default_configuration_is_disabled_and_has_no_database() -> None:
    config = load_saga_runtime_config({})
    assert config.mode is SagaRuntimeMode.DISABLED
    assert config.executable is False
    assert config.sqlite_path is None
    assert config.remote_effects_enabled is False
    assert config.worker_dispatch_enabled is False
    assert config.execution_unlock_enabled is False


def test_ci_disposable_accepts_only_a_temp_sqlite_file(tmp_path: Path) -> None:
    config = SagaRuntimeConfig.ci_disposable(tmp_path / "phase3j.sqlite")
    assert config.executable is True
    assert config.sqlite_path == (tmp_path / "phase3j.sqlite").resolve()

    with pytest.raises(SagaRuntimeConfigError, match="sqlite-path-not-disposable"):
        SagaRuntimeConfig.ci_disposable(ROOT / "operational.sqlite")
    with pytest.raises(SagaRuntimeConfigError, match="sqlite-path-invalid"):
        SagaRuntimeConfig.ci_disposable(tmp_path / "no-extension")


@pytest.mark.parametrize("environment", ["prod", "production", "PRODUCTION", "prd", "live"])
def test_production_can_never_enable_the_runtime(tmp_path: Path, environment: str) -> None:
    with pytest.raises(SagaRuntimeConfigError, match="production-runtime-forbidden"):
        SagaRuntimeConfig.ci_disposable(
            tmp_path / "phase3j.sqlite", environment=environment
        )
    disabled = SagaRuntimeConfig(environment=environment)
    assert disabled.executable is False


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("PHASE3J_REMOTE_EFFECTS_ENABLED", "remote-effects-forbidden"),
        ("PHASE3J_WORKER_DISPATCH_ENABLED", "worker-dispatch-forbidden"),
        ("PHASE3J_EXECUTION_UNLOCK_ENABLED", "execution-unlock-forbidden"),
    ],
)
@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "maybe"])
def test_every_effect_or_unlock_flag_fails_closed(
    name: str, code: str, value: str
) -> None:
    with pytest.raises(SagaRuntimeConfigError, match=code):
        load_saga_runtime_config({name: value})


@pytest.mark.parametrize("value", ["", "0", "false", "False", "no", "off"])
def test_explicit_false_effect_flags_remain_disabled(value: str) -> None:
    config = load_saga_runtime_config(
        {
            "PHASE3J_REMOTE_EFFECTS_ENABLED": value,
            "PHASE3J_WORKER_DISPATCH_ENABLED": value,
            "PHASE3J_EXECUTION_UNLOCK_ENABLED": value,
        }
    )
    assert config.executable is False


def test_unknown_mode_and_invalid_timeout_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(SagaRuntimeConfigError, match="mode-invalid"):
        load_saga_runtime_config({"PHASE3J_SAGA_RUNTIME_MODE": "enabled"})
    with pytest.raises(SagaRuntimeConfigError, match="busy-timeout-invalid"):
        load_saga_runtime_config(
            {
                "PHASE3J_SAGA_RUNTIME_MODE": "ci-disposable",
                "PHASE3J_SAGA_SQLITE_PATH": str(tmp_path / "phase3j.sqlite"),
                "PHASE3J_SAGA_BUSY_TIMEOUT_MS": "nan",
            }
        )


@pytest.mark.parametrize(
    "environment", ["unknown", "development", "dev", "staging", "live", "prd"]
)
def test_executable_runtime_environment_has_an_explicit_allowlist(
    tmp_path: Path, environment: str
) -> None:
    code = (
        "production-runtime-forbidden"
        if environment in {"live", "prd"}
        else "executable-environment-forbidden"
    )
    with pytest.raises(SagaRuntimeConfigError, match=code):
        SagaRuntimeConfig.ci_disposable(
            tmp_path / "phase3j.sqlite", environment=environment
        )


@pytest.mark.parametrize("environment", ["ci", "test", "local"])
def test_only_explicit_nonproduction_environments_can_execute(
    tmp_path: Path, environment: str
) -> None:
    assert SagaRuntimeConfig.ci_disposable(
        tmp_path / f"{environment}.sqlite", environment=environment
    ).executable


def test_dataclass_constructor_rejects_boolean_widening() -> None:
    for field, code in (
        ("remote_effects_enabled", "remote-effects-forbidden"),
        ("worker_dispatch_enabled", "worker-dispatch-forbidden"),
        ("execution_unlock_enabled", "execution-unlock-forbidden"),
    ):
        with pytest.raises(SagaRuntimeConfigError, match=code):
            SagaRuntimeConfig(**{field: True})

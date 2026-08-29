from __future__ import annotations

import importlib
import sys
import uuid
from pathlib import Path

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
entrypoint = importlib.import_module("retrain_evaluator_main")
JOB_ID = str(uuid.uuid4())


def _values(tmp_path: Path) -> dict[str, str]:
    return {
        "APP_ENV": "staging",
        "MODEL_RETRAIN_EVALUATION_ENABLED": "true",
        "MODEL_RETRAIN_EVALUATOR_ID": "staging-evaluator-01",
        "APP_COMMIT_SHA": "a" * 40,
        "MODEL_RETRAIN_JOB_ID": JOB_ID,
        "MODEL_RETRAIN_JOB_VERSION": "7",
        "MODEL_RETRAIN_OBSERVATIONS_PATH": str((tmp_path / "observations.json").resolve()),
    }


def test_valid_evaluator_config_is_exact_and_staging_only(tmp_path: Path) -> None:
    config = entrypoint.load_config(_values(tmp_path))
    assert config.job_id == JOB_ID
    assert config.expected_version == 7
    assert config.observations_path.is_absolute()


@pytest.mark.parametrize("environment", ["", "local", "development", "production"])
def test_evaluator_is_forbidden_outside_isolated_environment(
    tmp_path: Path,
    environment: str,
) -> None:
    values = _values(tmp_path)
    values["APP_ENV"] = environment
    with pytest.raises(entrypoint.RetrainEvaluatorConfigError, match="environment-forbidden"):
        entrypoint.load_config(values)


def test_evaluator_requires_separate_enable(tmp_path: Path) -> None:
    values = _values(tmp_path)
    values["MODEL_RETRAIN_EVALUATION_ENABLED"] = "false"
    with pytest.raises(entrypoint.RetrainEvaluatorConfigError, match="not-enabled"):
        entrypoint.load_config(values)


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("MODEL_RETRAIN_EVALUATOR_ID", "bad id", "id-invalid"),
        ("APP_COMMIT_SHA", "a" * 39, "commit-invalid"),
        ("APP_COMMIT_SHA", "0" * 40, "commit-invalid"),
        ("MODEL_RETRAIN_JOB_ID", "not-a-uuid", "job-invalid"),
        ("MODEL_RETRAIN_JOB_VERSION", "0", "version-invalid"),
        ("MODEL_RETRAIN_JOB_VERSION", "true", "version-invalid"),
        ("MODEL_RETRAIN_OBSERVATIONS_PATH", "relative.json", "observations-invalid"),
    ],
)
def test_evaluator_configuration_fails_closed(
    tmp_path: Path,
    key: str,
    value: str,
    code: str,
) -> None:
    values = _values(tmp_path)
    values[key] = value
    with pytest.raises(entrypoint.RetrainEvaluatorConfigError, match=code):
        entrypoint.load_config(values)

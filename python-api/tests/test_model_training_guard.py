from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
train = importlib.import_module("routers.train")
models = importlib.import_module("models")


@pytest.mark.parametrize("environment", ["staging", "production", "prod", "", "unknown"])
def test_deployed_or_unknown_environment_cannot_enable_direct_training(
    environment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "true")

    with pytest.raises(HTTPException) as captured:
        train._require_legacy_model_training_allowed()

    assert captured.value.status_code == 409
    assert "approval-bound durable job" in str(captured.value.detail)


def test_local_training_is_disabled_without_exact_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "false")

    with pytest.raises(HTTPException) as captured:
        train._require_legacy_model_training_allowed()

    assert captured.value.status_code == 409


def test_explicit_local_opt_in_allows_legacy_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "TRUE")

    assert train._require_legacy_model_training_allowed() is None


def test_blocked_training_does_not_create_job_or_write_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MODEL_TRAINING_LOCAL_ENABLED", "true")
    monkeypatch.setattr(train.joblib, "dump", lambda *_args, **_kwargs: pytest.fail("artifact write"))
    before = dict(train._train_jobs)

    with pytest.raises(HTTPException) as captured:
        asyncio.run(train.train_start(models.TrainRequest(), {"user_id": "premium-user"}))

    assert captured.value.status_code == 409
    assert train._train_jobs == before

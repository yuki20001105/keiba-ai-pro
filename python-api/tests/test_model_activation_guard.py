from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
models_mgmt = importlib.import_module("routers.models_mgmt")


@pytest.mark.parametrize("environment", ["staging", "production", "prod", "", "unknown"])
def test_deployed_or_unknown_environment_cannot_enable_legacy_activation(
    environment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("MODEL_ACTIVATION_LOCAL_ENABLED", "true")

    with pytest.raises(HTTPException) as captured:
        asyncio.run(models_mgmt.activate_model("candidate", {}))

    assert captured.value.status_code == 409
    assert "separate durable approval" in str(captured.value.detail)


def test_local_activation_is_disabled_without_exact_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("MODEL_ACTIVATION_LOCAL_ENABLED", "false")

    with pytest.raises(HTTPException) as captured:
        asyncio.run(models_mgmt.activate_model("candidate", {}))

    assert captured.value.status_code == 409


def test_explicit_local_opt_in_can_update_only_an_existing_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "model_win_lightgbm_local_candidate"
    (tmp_path / f"{model_id}.joblib").write_bytes(b"local-test-placeholder")
    activated: list[str] = []
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MODEL_ACTIVATION_LOCAL_ENABLED", "TRUE")
    monkeypatch.setattr(models_mgmt, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(models_mgmt, "set_active_model_id", activated.append)

    result = asyncio.run(models_mgmt.activate_model(model_id, {}))

    assert result == {"success": True, "active_model_id": model_id}
    assert activated == [model_id]


def test_local_opt_in_does_not_create_or_activate_a_missing_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activated: list[str] = []
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MODEL_ACTIVATION_LOCAL_ENABLED", "true")
    monkeypatch.setattr(models_mgmt, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(models_mgmt, "set_active_model_id", activated.append)

    with pytest.raises(HTTPException) as captured:
        asyncio.run(models_mgmt.activate_model("missing", {}))

    assert captured.value.status_code == 404
    assert activated == []

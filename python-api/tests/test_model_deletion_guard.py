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
def test_deployed_or_unknown_environment_cannot_enable_legacy_deletion(
    environment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("MODEL_DELETION_LOCAL_ENABLED", "true")
    unlink = monkeypatch.setattr(Path, "unlink", lambda *_args, **_kwargs: pytest.fail("artifact deletion"))

    with pytest.raises(HTTPException) as captured:
        asyncio.run(models_mgmt.delete_model("candidate", {}))

    assert captured.value.status_code == 409
    assert "retirement approval" in str(captured.value.detail)
    assert unlink is None


def test_local_deletion_is_disabled_without_exact_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("MODEL_DELETION_LOCAL_ENABLED", "false")

    with pytest.raises(HTTPException) as captured:
        asyncio.run(models_mgmt.delete_model("candidate", {}))

    assert captured.value.status_code == 409


def test_explicit_local_opt_in_can_delete_local_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "model_win_lightgbm_candidate.joblib"
    artifact.write_bytes(b"local-test-placeholder")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MODEL_DELETION_LOCAL_ENABLED", "TRUE")
    monkeypatch.setattr(models_mgmt, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(models_mgmt, "SUPABASE_DATA_ENABLED", False)

    result = asyncio.run(models_mgmt.delete_model("candidate", {}))

    assert result == {"success": True, "deleted": [artifact.name]}
    assert not artifact.exists()

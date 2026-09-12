from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
models_mgmt = importlib.import_module("routers.models_mgmt")


def test_saved_models_are_sorted_by_creation_time_not_training_period(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    older = tmp_path / (
        "model_speed_deviation_lightgbm_20250101_20250118_20260829_2156.joblib"
    )
    newest = tmp_path / (
        "model_speed_deviation_lightgbm_20180106_20260706_20260912_2159_02101032.joblib"
    )
    older.write_bytes(b"older")
    newest.write_bytes(b"newest")

    bundles = {
        older.name: {
            "created_at": "20260829_2156",
            "target": "speed_deviation",
            "model_type": "lightgbm",
            "ultimate_mode": True,
            "metrics": {"auc": 0.72},
        },
        newest.name: {
            "created_at": "20260912_2159",
            "target": "speed_deviation",
            "model_type": "lightgbm",
            "ultimate_mode": True,
            "metrics": {"auc": 0.7595},
        },
    }

    monkeypatch.setattr(models_mgmt, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(models_mgmt, "get_active_model_id", lambda: "active-model")
    monkeypatch.setattr(models_mgmt.joblib, "load", lambda path: bundles[path.name])

    response = asyncio.run(models_mgmt.list_models())

    assert response["count"] == 2
    assert [model["model_id"] for model in response["models"]] == [
        newest.stem,
        older.stem,
    ]


def test_model_creation_sort_key_falls_back_to_file_mtime(tmp_path: Path) -> None:
    model_path = tmp_path / "model_win_lightgbm_legacy.joblib"
    model_path.write_bytes(b"legacy")

    assert models_mgmt._model_created_sort_key(
        model_path,
        {"created_at": "unknown"},
    ) == model_path.stat().st_mtime

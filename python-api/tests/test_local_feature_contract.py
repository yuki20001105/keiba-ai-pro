from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
feature_contract = importlib.import_module("training.local_feature_contract")


def test_period_filter_uses_race_date_then_race_id_fallback() -> None:
    frame = pd.DataFrame(
        {
            "race_id": ["201912010101", "202001010101", "202102010101"],
            "race_date": ["20191231", None, "invalid"],
            "odds": [1.0, 2.0, 3.0],
        }
    )

    selected = feature_contract.filter_training_period(
        frame,
        training_date_from="2020-01",
        training_date_to="2021-02",
    )

    assert selected["odds"].tolist() == [2.0, 3.0]


def test_shared_lightgbm_frame_preserves_numeric_order_and_excludes_non_features(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    optimized = pd.DataFrame(
        {
            "race_id": ["r1"],
            "odds": [2.5],
            "horse_name": pd.Series(["horse"], dtype="string"),
            "speed": [42.0],
            "speed_deviation": [1.5],
        }
    )
    optimizer = object()
    monkeypatch.setattr(
        "keiba_ai.lightgbm_feature_optimizer.prepare_for_lightgbm_ultimate",
        lambda *_args, **_kwargs: (optimized, optimizer, ["venue_encoded"]),
    )

    prepared = feature_contract.prepare_lightgbm_feature_frame(
        pd.DataFrame({"source": [1]}),
        target="speed_deviation",
    )

    assert prepared.features.columns.tolist() == ["odds", "speed"]
    assert prepared.optimizer is optimizer
    assert prepared.categorical_features == ("venue_encoded",)


def test_schema_preflight_is_read_only_ordered_and_reports_real_phase_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot.db"
    snapshot.write_bytes(b"immutable-test-snapshot")
    loaded = pd.DataFrame(
        {
            "race_id": ["202401010101", "202501010101"],
            "race_date": ["20240101", "20250101"],
            "odds": [2.0, 3.0],
        }
    )
    observed_rows: list[list[float]] = []
    read_modes: list[bool] = []

    def fake_load(path: Path, *, read_only: bool = False) -> pd.DataFrame:
        assert path == snapshot
        read_modes.append(read_only)
        return loaded

    def fake_engineer(frame: pd.DataFrame) -> pd.DataFrame:
        observed_rows.append(frame["odds"].tolist())
        return frame

    def fake_prepare(frame: pd.DataFrame, *, target: str):
        assert target == "speed_deviation"
        features = frame.loc[:, ["odds"]].copy()
        features["generated"] = 1.0
        return feature_contract.PreparedLightGBMFrame(
            optimized=frame,
            features=features,
            optimizer=object(),
            categorical_features=(),
        )

    monkeypatch.setattr(
        "keiba_ai.db_ultimate_loader.load_ultimate_training_frame",
        fake_load,
    )
    monkeypatch.setattr(feature_contract, "engineer_training_features", fake_engineer)
    monkeypatch.setattr(feature_contract, "prepare_lightgbm_feature_frame", fake_prepare)
    phases: list[tuple[str, int]] = []

    schema = feature_contract.derive_local_feature_schema(
        snapshot,
        target="speed_deviation",
        training_date_from="2025-01",
        training_date_to="2025-12",
        observe_phase=lambda message, pct: phases.append((message, pct)),
    )

    assert read_modes == [True]
    assert observed_rows == [[3.0]]
    assert schema == ("odds", "generated")
    assert [message for message, _pct in phases] == [
        "特徴量契約: データ読込中",
        "特徴量契約: 期間抽出中",
        "特徴量契約: 特徴量生成中",
        "特徴量契約: スキーマ確定中",
        "特徴量契約を確認済み",
    ]


@pytest.mark.parametrize(
    ("columns", "error"),
    [
        (["odds", "odds"], "duplicate"),
        (["odds", "finish_position"], "future-field"),
    ],
)
def test_schema_preflight_rejects_unsafe_produced_schema(
    columns: list[str],
    error: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot.db"
    snapshot.write_bytes(b"immutable-test-snapshot")
    frame = pd.DataFrame({"race_id": ["202501010101"], "odds": [2.0]})
    monkeypatch.setattr(
        "keiba_ai.db_ultimate_loader.load_ultimate_training_frame",
        lambda *_args, **_kwargs: frame,
    )
    monkeypatch.setattr(
        feature_contract,
        "engineer_training_features",
        lambda value: value,
    )
    unsafe = pd.DataFrame([[1.0] * len(columns)], columns=columns)
    monkeypatch.setattr(
        feature_contract,
        "prepare_lightgbm_feature_frame",
        lambda *_args, **_kwargs: feature_contract.PreparedLightGBMFrame(
            optimized=unsafe,
            features=unsafe,
            optimizer=object(),
            categorical_features=(),
        ),
    )

    with pytest.raises(feature_contract.LocalRetrainError, match=error):
        feature_contract.derive_local_feature_schema(
            snapshot,
            target="speed_deviation",
            training_date_from=None,
            training_date_to=None,
        )

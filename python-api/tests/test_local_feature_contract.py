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


def test_history_is_bounded_by_last_supervised_date() -> None:
    frame = pd.DataFrame(
        {
            "race_date": ["20240101", "2025-01-15", "2025/02/01", "20250301", "bad"],
            "value": [1, 2, 3, 4, 5],
        }
    )
    target = pd.DataFrame({"race_date": ["20250115", "20250201"]})

    selected = feature_contract.filter_history_through_last_target(frame, target)

    assert selected["value"].tolist() == [1, 2, 3]


def test_time_holdout_keeps_dates_and_races_on_one_side() -> None:
    rows = []
    for day in range(1, 31):
        race_id = f"202501{day:02d}0101"
        for horse_number in range(1, 11):
            rows.append(
                {
                    "race_id": race_id,
                    "race_date": f"202501{day:02d}",
                    "horse_number": horse_number,
                }
            )
    frame = pd.DataFrame(rows)

    holdout = feature_contract.select_training_holdout(
        frame,
        target="win",
        test_size=0.2,
    )

    train = frame.iloc[list(holdout.train_positions)]
    validation = frame.iloc[list(holdout.validation_positions)]
    assert holdout.time_based is True
    assert train["race_date"].max() < validation["race_date"].min()
    assert set(train["race_id"]).isdisjoint(set(validation["race_id"]))


def test_holdout_rejects_missing_chronology_instead_of_random_fallback() -> None:
    frame = pd.DataFrame(
        [
            {
                "race_id": f"legacy-{race_number}",
                "race_date": "unknown",
                "horse_number": horse_number,
            }
            for race_number in range(10)
            for horse_number in range(1, 4)
        ]
    )

    with pytest.raises(ValueError, match="valid race_date"):
        feature_contract.select_training_holdout(
            frame,
            target="win",
            test_size=0.2,
            min_train_rows=1,
            min_validation_rows=1,
        )


def test_holdout_with_one_invalid_date_fails_closed() -> None:
    rows = []
    for day in range(1, 31):
        for horse_number in range(1, 11):
            rows.append(
                {
                    "race_id": f"race-{day}",
                    "race_date": "invalid" if day == 1 else f"202501{day:02d}",
                    "horse_number": horse_number,
                }
            )
    frame = pd.DataFrame(rows)

    with pytest.raises(ValueError, match="valid race_date"):
        feature_contract.select_training_holdout(
            frame,
            target="win",
            test_size=0.2,
        )


def test_small_time_holdout_fails_with_a_clear_minimum_error() -> None:
    frame = pd.DataFrame(
        {
            "race_id": [f"race-{index // 3}" for index in range(30)],
            "race_date": [f"202501{index // 3 + 1:02d}" for index in range(30)],
        }
    )

    with pytest.raises(ValueError, match="minimum rows"):
        feature_contract.select_training_holdout(
            frame,
            target="win",
            test_size=0.2,
        )


def test_internal_cv_is_forward_in_time_and_keeps_races_whole() -> None:
    rows = [
        {
            "race_id": f"race-{day}-{race_number}",
            "race_date": f"202501{day:02d}",
        }
        for day in range(1, 13)
        for race_number in range(2)
        for _horse in range(3)
    ]
    frame = pd.DataFrame(rows)

    plan = feature_contract.build_training_cv_plan(frame, n_splits=3)

    assert plan.time_based is True
    assert len(plan.folds) == 3
    for train_positions, validation_positions in plan.folds:
        train = frame.iloc[list(train_positions)]
        validation = frame.iloc[list(validation_positions)]
        assert train["race_date"].max() < validation["race_date"].min()
        assert set(train["race_id"]).isdisjoint(set(validation["race_id"]))


def test_internal_cv_rejects_missing_chronology() -> None:
    frame = pd.DataFrame(
        {
            "race_id": [f"race-{race}" for race in range(6) for _ in range(3)],
            "race_date": ["invalid"] * 18,
        }
    )

    with pytest.raises(ValueError, match="valid race_date"):
        feature_contract.build_training_cv_plan(frame, n_splits=3)


def test_ranking_helpers_make_queries_contiguous_and_size_them() -> None:
    frame = pd.DataFrame({"race_id": ["b", "a", "b", "a", "c"]})

    order = feature_contract.stable_race_order(frame)
    ordered = frame.iloc[list(order)]["race_id"].tolist()

    assert ordered == ["a", "a", "b", "b", "c"]
    assert feature_contract.ranking_group_sizes(ordered) == (2, 2, 1)
    with pytest.raises(ValueError, match="contiguous"):
        feature_contract.ranking_group_sizes(["a", "b", "a"])


def test_speed_target_baseline_ignores_validation_outcomes() -> None:
    base = pd.DataFrame(
        {
            "distance": [1600, 1600, 1600, 1600],
            "surface": ["芝"] * 4,
            "time_seconds": [100.0, 80.0, 70.0, 60.0],
        }
    )
    changed_validation = base.copy()
    changed_validation.loc[2:, "time_seconds"] = [200.0, 300.0]

    first = feature_contract.prepare_training_target(
        base,
        target="speed_deviation",
        train_positions=(0, 1),
    )
    second = feature_contract.prepare_training_target(
        changed_validation,
        target="speed_deviation",
        train_positions=(0, 1),
    )

    assert first.speed_deviation_baseline == second.speed_deviation_baseline
    assert first.values.iloc[:2].tolist() == second.values.iloc[:2].tolist()


def test_rank_target_is_race_relative_not_validation_relative() -> None:
    frame = pd.DataFrame(
        {
            "race_id": ["train"] * 3 + ["validation"] * 5,
            "num_horses": [3] * 3 + [5] * 5,
            "finish": [1, 2, 3, 1, 2, 3, 4, 5],
        }
    )

    prepared = feature_contract.prepare_training_target(
        frame,
        target="rank",
        train_positions=(0, 1, 2),
    )

    assert prepared.values.tolist() == [3, 2, 1, 5, 4, 3, 2, 1]


def test_feature_split_reuses_train_optimizer_and_column_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    optimizer = object()
    train_optimized = pd.DataFrame(
        {"race_id": ["train"], "kept": [1.0], "train_only": [2.0]}
    )
    validation_optimized = pd.DataFrame(
        {"race_id": ["validation"], "kept": [3.0], "holdout_only": [4.0]}
    )
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        feature_contract,
        "prepare_lightgbm_feature_frame",
        lambda *_args, **_kwargs: feature_contract.PreparedLightGBMFrame(
            optimized=train_optimized,
            features=train_optimized[["kept", "train_only"]],
            optimizer=optimizer,
            categorical_features=(),
        ),
    )

    def fake_transform(frame, *, target_col, is_training, optimizer):
        observed.update(
            frame=frame,
            target_col=target_col,
            is_training=is_training,
            optimizer=optimizer,
        )
        return validation_optimized, optimizer, []

    monkeypatch.setattr(
        "keiba_ai.lightgbm_feature_optimizer.prepare_for_lightgbm_ultimate",
        fake_transform,
    )

    result = feature_contract.prepare_lightgbm_feature_split(
        pd.DataFrame({"source": [1]}),
        pd.DataFrame({"source": [2]}),
        target="speed_deviation",
    )

    assert observed["is_training"] is False
    assert observed["optimizer"] is optimizer
    assert result.validation.features.columns.tolist() == ["kept", "train_only"]
    assert result.validation.features["kept"].tolist() == [3.0]
    assert result.validation.features["train_only"].isna().all()


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
            "race_id": ["202401010101", "202501010101", "202601010101"],
            "race_date": ["20240101", "20250101", "20260101"],
            "odds": [2.0, 3.0, 4.0],
        }
    )
    observed_rows: list[list[float]] = []
    observed_history_rows: list[list[float]] = []
    read_modes: list[bool] = []

    def fake_load(path: Path, *, read_only: bool = False) -> pd.DataFrame:
        assert path == snapshot
        read_modes.append(read_only)
        return loaded

    def fake_engineer(
        frame: pd.DataFrame,
        *,
        full_history_frame: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        observed_rows.append(frame["odds"].tolist())
        assert full_history_frame is not None
        observed_history_rows.append(full_history_frame["odds"].tolist())
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
    monkeypatch.setattr(
        feature_contract,
        "select_training_eligible_rows",
        lambda frame, **_kwargs: type("Selection", (), {"frame": frame})(),
    )
    monkeypatch.setattr(feature_contract, "load_recorded_quality_states", lambda _path: {})
    monkeypatch.setattr(
        feature_contract,
        "select_training_holdout",
        lambda *_args, **_kwargs: feature_contract.TrainingHoldout((0,), (0,), False),
    )
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
    assert observed_history_rows == [[2.0, 3.0]]
    assert schema == ("odds", "generated")
    assert [message for message, _pct in phases] == [
        "特徴量契約: データ読込中",
        "特徴量契約: 期間抽出中",
        "特徴量契約: 学習可能データ判定中",
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
    frame = pd.DataFrame(
        {"race_id": ["202501010101"], "race_date": ["20250101"], "odds": [2.0]}
    )
    monkeypatch.setattr(
        "keiba_ai.db_ultimate_loader.load_ultimate_training_frame",
        lambda *_args, **_kwargs: frame,
    )
    monkeypatch.setattr(
        feature_contract,
        "engineer_training_features",
        lambda value, **_kwargs: value,
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
    monkeypatch.setattr(
        feature_contract,
        "select_training_eligible_rows",
        lambda value, **_kwargs: type("Selection", (), {"frame": value})(),
    )
    monkeypatch.setattr(feature_contract, "load_recorded_quality_states", lambda _path: {})
    monkeypatch.setattr(
        feature_contract,
        "select_training_holdout",
        lambda *_args, **_kwargs: feature_contract.TrainingHoldout((0,), (0,), False),
    )

    with pytest.raises(feature_contract.LocalRetrainError, match=error):
        feature_contract.derive_local_feature_schema(
            snapshot,
            target="speed_deviation",
            training_date_from=None,
            training_date_to=None,
        )

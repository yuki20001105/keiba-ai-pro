from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from keiba_ai.constants import FUTURE_FIELDS
from training.local_retrain import LocalRetrainError


PhaseObserver = Callable[[str, int], None]


@dataclass(frozen=True)
class PreparedLightGBMFrame:
    """One deterministic output of the training feature pipeline."""

    optimized: Any
    features: Any
    optimizer: Any
    categorical_features: tuple[str, ...]


def filter_training_period(
    frame: Any,
    *,
    training_date_from: str | None,
    training_date_to: str | None,
) -> Any:
    """Apply the same YYYYMM filter used by local and approved training."""

    import pandas as pd

    if not training_date_from and not training_date_to:
        return frame
    if "race_date" in frame.columns:
        race_dates = frame["race_date"].astype(str).str.strip()
        valid_race_dates = race_dates.str.match(r"^\d{8}$")
    else:
        race_dates = pd.Series([""] * len(frame), index=frame.index)
        valid_race_dates = pd.Series([False] * len(frame), index=frame.index)
    if "race_id" in frame.columns:
        fallback_months = frame["race_id"].astype(str).str[:6]
    else:
        fallback_months = pd.Series(["000000"] * len(frame), index=frame.index)
    observed_months = race_dates.str[:6].where(valid_race_dates, fallback_months)
    selected = pd.Series(True, index=frame.index)
    if training_date_from:
        selected &= observed_months >= training_date_from.replace("-", "")
    if training_date_to:
        selected &= observed_months <= training_date_to.replace("-", "")
    return frame.loc[selected]


def engineer_training_features(frame: Any) -> Any:
    """Run the shared pre-result feature engineering and leak removal."""

    from keiba_ai.feature_engineering import add_derived_features

    engineered = add_derived_features(frame, full_history_df=frame)
    engineered = engineered.loc[:, ~engineered.columns.duplicated()]
    future_columns = [column for column in FUTURE_FIELDS if column in engineered.columns]
    if future_columns:
        engineered = engineered.drop(columns=future_columns)
    return engineered


def prepare_lightgbm_feature_frame(frame: Any, *, target: str) -> PreparedLightGBMFrame:
    """Produce the ordered model matrix used by the non-OOT training path."""

    from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate

    optimized, optimizer, categorical_features = prepare_for_lightgbm_ultimate(
        frame,
        target_col=target,
        is_training=True,
    )
    excluded_columns = {
        target,
        "race_id",
        "horse_id",
        "jockey_id",
        "trainer_id",
        "owner_id",
        "finish_position",
    }
    feature_frame = optimized.drop(
        [column for column in optimized.columns if column in excluded_columns],
        axis=1,
    )
    string_columns = feature_frame.select_dtypes(
        include=["object", "string"],
    ).columns.tolist()
    if string_columns:
        feature_frame = feature_frame.drop(columns=string_columns)
    return PreparedLightGBMFrame(
        optimized=optimized,
        features=feature_frame,
        optimizer=optimizer,
        categorical_features=tuple(categorical_features),
    )


def derive_local_feature_schema(
    snapshot_path: Path,
    *,
    target: str,
    training_date_from: str | None,
    training_date_to: str | None,
    observe_phase: PhaseObserver | None = None,
) -> tuple[str, ...]:
    """Derive and order-bind the exact schema from an immutable SQLite snapshot.

    The observer is invoked only at real phase boundaries.  The caller may use
    those boundaries to renew its preparation capability; this function never
    starts an independent watchdog that could keep a hung preparation alive.
    """

    from keiba_ai.db_ultimate_loader import load_ultimate_training_frame

    def observe(message: str, pct: int) -> None:
        if observe_phase is not None:
            observe_phase(message, pct)

    observe("特徴量契約: データ読込中", 4)
    frame = load_ultimate_training_frame(snapshot_path, read_only=True)
    observe("特徴量契約: 期間抽出中", 4)
    frame = filter_training_period(
        frame,
        training_date_from=training_date_from,
        training_date_to=training_date_to,
    )
    if frame.empty:
        raise LocalRetrainError("local-feature-contract-data-empty")
    observe("特徴量契約: 特徴量生成中", 4)
    frame = engineer_training_features(frame)
    observe("特徴量契約: スキーマ確定中", 4)
    prepared = prepare_lightgbm_feature_frame(frame, target=target)
    columns = tuple(prepared.features.columns.tolist())
    if not columns:
        raise LocalRetrainError("local-feature-contract-empty")
    if len(set(columns)) != len(columns):
        raise LocalRetrainError("local-feature-contract-duplicate")
    if set(columns).intersection(FUTURE_FIELDS):
        raise LocalRetrainError("local-feature-contract-future-field")
    observe("特徴量契約を確認済み", 5)
    return columns

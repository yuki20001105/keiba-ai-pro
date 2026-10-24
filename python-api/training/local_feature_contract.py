from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from keiba_ai.constants import FUTURE_FIELDS
from training.eligibility import (
    TrainingEligibilityError,
    load_recorded_quality_states,
    select_training_eligible_rows,
)
from training.local_retrain import LocalRetrainError


PhaseObserver = Callable[[str, int], None]


@dataclass(frozen=True)
class PreparedLightGBMFrame:
    """One deterministic output of the training feature pipeline."""

    optimized: Any
    features: Any
    optimizer: Any
    categorical_features: tuple[str, ...]


@dataclass(frozen=True)
class TrainingHoldout:
    """Position-based split that keeps complete races on one side."""

    train_positions: tuple[int, ...]
    validation_positions: tuple[int, ...]
    time_based: bool


@dataclass(frozen=True)
class PreparedLightGBMSplit:
    """Optimizer fitted on training rows and reused unchanged for holdout."""

    train: PreparedLightGBMFrame
    validation: PreparedLightGBMFrame


@dataclass(frozen=True)
class PreparedTrainingTarget:
    """Target values plus any train-only normalization state."""

    values: Any
    speed_deviation_baseline: dict[str, Any] | None


@dataclass(frozen=True)
class TrainingCVPlan:
    """Race-safe internal folds used only inside the outer training set."""

    folds: tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]
    time_based: bool


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


def filter_history_through_last_target(frame: Any, target_frame: Any) -> Any:
    """Keep history no later than the final supervised race date.

    Every history helper is point-in-time aware, but bounding its input as well
    provides a second line of defense against a future-aware derived feature.
    Rows with missing or unsupported dates are excluded here and will also be
    rejected by the eligibility policy when evaluated directly.
    """

    import pandas as pd

    def parse_dates(value: Any) -> Any:
        if "race_date" not in value.columns:
            return pd.Series(pd.NaT, index=value.index, dtype="datetime64[ns]")
        raw = value["race_date"].astype("string").fillna("").str.strip()
        supported = raw.str.fullmatch(
            r"(?:\d{8}|\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2})"
        )
        compact = raw.str.replace(r"\D", "", regex=True).where(supported, "")
        return pd.to_datetime(compact, format="%Y%m%d", errors="coerce")

    target_dates = parse_dates(target_frame)
    if target_dates.empty or not target_dates.notna().all():
        raise ValueError("every target row requires a valid race_date")
    source_dates = parse_dates(frame)
    return frame.loc[source_dates.le(target_dates.max())]


def engineer_training_features(
    frame: Any,
    *,
    full_history_frame: Any | None = None,
) -> Any:
    """Run pre-result feature engineering against eligible historical rows.

    ``frame`` contains the supervised rows selected for this training run.
    ``full_history_frame`` may additionally contain older, quality-approved
    races so rolling features do not lose valid pre-period context.  The
    feature-engineering layer is responsible for using only observations that
    strictly precede each row.
    """

    from keiba_ai.feature_engineering import add_derived_features

    history_frame = frame if full_history_frame is None else full_history_frame
    engineered = add_derived_features(frame, full_history_df=history_frame)
    engineered = engineered.loc[:, ~engineered.columns.duplicated()]
    future_columns = [column for column in FUTURE_FIELDS if column in engineered.columns]
    if future_columns:
        engineered = engineered.drop(columns=future_columns)
    return engineered


def select_training_holdout(
    frame: Any,
    *,
    target: str,
    test_size: float,
    random_state: int = 42,
    min_train_rows: int = 200,
    min_validation_rows: int = 50,
) -> TrainingHoldout:
    """Choose an out-of-time holdout without a random fallback.

    Speed models discard rows without a realizable speed before selecting the
    boundary.  History features are computed chronologically, so a random
    race-group split could still let an early validation result influence a
    later training row.  Insufficient chronology therefore fails closed.
    """

    import numpy as np
    import pandas as pd

    if not 0 < float(test_size) < 1:
        raise ValueError("test_size must be between zero and one")
    positions = np.arange(len(frame), dtype=np.int64)
    eligible = np.ones(len(frame), dtype=bool)
    if target == "speed_deviation":
        from keiba_ai.speed_deviation import raw_speed_mps

        eligible = raw_speed_mps(frame).notna().to_numpy()
    positions = positions[eligible]
    if len(positions) < 2:
        raise ValueError("at least two target-valid rows are required")

    observed_dates = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
    if "race_date" in frame.columns:
        date_text = (
            frame["race_date"]
            .astype("string")
            .str.strip()
            .str.replace("-", "", regex=False)
            .str.replace("/", "", regex=False)
        )
        observed_dates = pd.to_datetime(date_text, format="%Y%m%d", errors="coerce")
    eligible_dates = observed_dates.iloc[positions]
    if not eligible_dates.notna().all():
        raise ValueError("every row requires a valid race_date for holdout")
    cutoff = eligible_dates.quantile(1.0 - float(test_size))
    train_positions = positions[(eligible_dates <= cutoff).to_numpy()]
    validation_positions = positions[(eligible_dates > cutoff).to_numpy()]
    if (
        len(train_positions) < min_train_rows
        or len(validation_positions) < min_validation_rows
    ):
        raise ValueError("out-of-time holdout does not meet minimum rows")
    return TrainingHoldout(
        train_positions=tuple(int(value) for value in train_positions),
        validation_positions=tuple(int(value) for value in validation_positions),
        time_based=True,
    )


def build_training_cv_plan(
    frame: Any,
    *,
    n_splits: int,
) -> TrainingCVPlan:
    """Build date-forward folds inside the outer training partition.

    This function is called only with the outer training partition.  Every
    race/date stays wholly on one side of every fold, preventing runners from
    the same race and later observations from informing an earlier fold.
    """

    import numpy as np
    import pandas as pd
    from sklearn.model_selection import TimeSeriesSplit

    if n_splits < 2:
        raise ValueError("n_splits must be at least two")
    if "race_id" not in frame.columns:
        raise ValueError("race_id is required for race-safe CV")
    if frame.empty:
        raise ValueError("training rows are required for CV")

    positions = np.arange(len(frame), dtype=np.int64)
    race_ids = frame["race_id"].astype("string").fillna("").str.strip()
    if race_ids.eq("").any():
        raise ValueError("race_id must be present for every CV row")

    observed_dates = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
    if "race_date" in frame.columns:
        date_text = (
            frame["race_date"]
            .astype("string")
            .str.strip()
            .str.replace("-", "", regex=False)
            .str.replace("/", "", regex=False)
        )
        observed_dates = pd.to_datetime(
            date_text,
            format="%Y%m%d",
            errors="coerce",
        )

    if not observed_dates.notna().all():
        raise ValueError("every CV row requires a valid race_date")
    unique_dates = observed_dates.drop_duplicates().sort_values()
    if len(unique_dates) < n_splits + 1:
        raise ValueError("not enough race dates for forward-time CV")

    date_values = unique_dates.to_numpy()
    splitter = TimeSeriesSplit(n_splits=n_splits)
    folds: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
    for train_date_positions, validation_date_positions in splitter.split(date_values):
        train_dates = date_values[train_date_positions]
        validation_dates = date_values[validation_date_positions]
        train_rows = positions[observed_dates.isin(train_dates).to_numpy()]
        validation_rows = positions[
            observed_dates.isin(validation_dates).to_numpy()
        ]
        if len(train_rows) == 0 or len(validation_rows) == 0:
            raise ValueError("forward-time CV produced an empty fold")
        folds.append(
            (
                tuple(int(value) for value in train_rows),
                tuple(int(value) for value in validation_rows),
            )
        )
    return TrainingCVPlan(folds=tuple(folds), time_based=True)


def stable_race_order(frame: Any) -> tuple[int, ...]:
    """Return a stable row order that makes each race one contiguous query."""

    import numpy as np

    if "race_id" not in frame.columns:
        raise ValueError("race_id is required for ranking")
    race_ids = frame["race_id"].astype("string").fillna("").str.strip()
    if race_ids.eq("").any():
        raise ValueError("race_id must be present for ranking")
    order = np.argsort(race_ids.to_numpy(), kind="stable")
    return tuple(int(value) for value in order)


def ranking_group_sizes(race_ids: Any) -> tuple[int, ...]:
    """Return LightGBM query sizes and reject non-contiguous race rows."""

    values = [str(value) for value in race_ids]
    if not values:
        raise ValueError("ranking rows are required")
    sizes: list[int] = []
    seen: set[str] = set()
    current = values[0]
    current_size = 0
    for value in values:
        if value != current:
            seen.add(current)
            if value in seen:
                raise ValueError("ranking race rows must be contiguous")
            sizes.append(current_size)
            current = value
            current_size = 0
        current_size += 1
    sizes.append(current_size)
    return tuple(sizes)


def prepare_training_target(
    frame: Any,
    *,
    target: str,
    train_positions: tuple[int, ...],
) -> PreparedTrainingTarget:
    """Build targets without fitting any state on validation outcomes."""

    if not train_positions:
        raise ValueError("training rows are required")
    if target == "speed_deviation":
        from keiba_ai.speed_deviation import (
            apply_speed_deviation_baseline,
            fit_speed_deviation_baseline,
        )

        baseline = fit_speed_deviation_baseline(
            frame.iloc[list(train_positions)]
        )
        return PreparedTrainingTarget(
            values=apply_speed_deviation_baseline(frame, baseline),
            speed_deviation_baseline=baseline,
        )

    # Rank relevance is defined within each race.  Using the maximum finish
    # rank from the entire frame would make training labels depend on the
    # validation period's field size.
    if target == "rank":
        import pandas as pd

        finish_column = next(
            (name for name in ("finish", "finish_position") if name in frame.columns),
            None,
        )
        if finish_column is None or "num_horses" not in frame.columns:
            raise ValueError("rank target requires finish and num_horses")
        finish = pd.to_numeric(frame[finish_column], errors="coerce")
        field_size = pd.to_numeric(frame["num_horses"], errors="coerce")
        return PreparedTrainingTarget(
            values=(field_size - finish + 1).clip(lower=0).fillna(0).astype(int),
            speed_deviation_baseline=None,
        )

    from keiba_ai.train import _make_target

    target_frame = frame
    if "finish" not in target_frame.columns and "finish_position" in target_frame.columns:
        target_frame = target_frame.copy()
        target_frame["finish"] = target_frame["finish_position"]
    return PreparedTrainingTarget(
        values=_make_target(target_frame, target),
        speed_deviation_baseline=None,
    )


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


def prepare_lightgbm_feature_split(
    train_frame: Any,
    validation_frame: Any,
    *,
    target: str,
) -> PreparedLightGBMSplit:
    """Fit preprocessing on train and transform validation without refitting."""

    import numpy as np
    from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate

    train = prepare_lightgbm_feature_frame(train_frame, target=target)
    validation_optimized, _, _ = prepare_for_lightgbm_ultimate(
        validation_frame,
        target_col=target,
        is_training=False,
        optimizer=train.optimizer,
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
    validation_features = validation_optimized.drop(
        [
            column
            for column in validation_optimized.columns
            if column in excluded_columns
        ],
        axis=1,
    )
    string_columns = validation_features.select_dtypes(
        include=["object", "string"],
    ).columns.tolist()
    if string_columns:
        validation_features = validation_features.drop(columns=string_columns)
    # Unknown or holdout-only fields must not change the training contract;
    # absent training fields are represented as ordinary LightGBM missingness.
    validation_features = validation_features.reindex(
        columns=train.features.columns,
        fill_value=np.nan,
    )
    validation = PreparedLightGBMFrame(
        optimized=validation_optimized,
        features=validation_features,
        optimizer=train.optimizer,
        categorical_features=train.categorical_features,
    )
    return PreparedLightGBMSplit(train=train, validation=validation)


def derive_local_feature_schema(
    snapshot_path: Path,
    *,
    target: str,
    training_date_from: str | None,
    training_date_to: str | None,
    test_size: float = 0.2,
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
    loaded_frame = load_ultimate_training_frame(snapshot_path, read_only=True)
    observe("特徴量契約: 期間抽出中", 4)
    frame = filter_training_period(
        loaded_frame,
        training_date_from=training_date_from,
        training_date_to=training_date_to,
    )
    if frame.empty:
        raise LocalRetrainError("local-feature-contract-data-empty")
    observe("特徴量契約: 学習可能データ判定中", 4)
    quality_states = load_recorded_quality_states(snapshot_path)
    try:
        period_selection = select_training_eligible_rows(
            frame,
            target=target,
            training_date_from=training_date_from,
            training_date_to=training_date_to,
            recorded_quality_states=quality_states,
        )
    except TrainingEligibilityError as exc:
        raise LocalRetrainError("local-feature-contract-eligibility-invalid") from exc
    frame = period_selection.frame
    if frame.empty:
        raise LocalRetrainError("local-feature-contract-eligible-data-empty")
    try:
        history_source = filter_history_through_last_target(loaded_frame, frame)
        history_selection = (
            period_selection
            if not training_date_from
            and not training_date_to
            and len(history_source) == len(loaded_frame)
            else select_training_eligible_rows(
                history_source,
                target=target,
                recorded_quality_states=quality_states,
            )
        )
    except (TrainingEligibilityError, ValueError) as exc:
        raise LocalRetrainError("local-feature-contract-history-invalid") from exc
    history_frame = history_selection.frame
    try:
        holdout = select_training_holdout(
            frame,
            target=target,
            test_size=test_size,
        )
    except ValueError as exc:
        raise LocalRetrainError("local-feature-contract-split-invalid") from exc
    observe("特徴量契約: 特徴量生成中", 4)
    frame = engineer_training_features(
        frame,
        full_history_frame=history_frame,
    )
    observe("特徴量契約: スキーマ確定中", 4)
    prepared = prepare_lightgbm_feature_frame(
        frame.iloc[list(holdout.train_positions)].copy(),
        target=target,
    )
    columns = tuple(prepared.features.columns.tolist())
    if not columns:
        raise LocalRetrainError("local-feature-contract-empty")
    if len(set(columns)) != len(columns):
        raise LocalRetrainError("local-feature-contract-duplicate")
    if set(columns).intersection(FUTURE_FIELDS):
        raise LocalRetrainError("local-feature-contract-future-field")
    observe("特徴量契約を確認済み", 5)
    return columns

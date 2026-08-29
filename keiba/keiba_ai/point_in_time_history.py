"""Leakage-safe horse history features.

Every source result must satisfy ``source_race_date < target_race_date``.
Same-day rows and future rows are deliberately invisible, even when their
race IDs sort before the target race ID.
"""

from __future__ import annotations

from collections.abc import Iterable
import numpy as np
import pandas as pd


DEFAULT_HISTORY_WINDOWS = (1, 2, 3, 5, 10)


def _race_dates(values: pd.Series) -> pd.Series:
    text = values.astype("string").str.strip().str.replace("-", "", regex=False)
    text = text.str.replace("/", "", regex=False)
    return pd.to_datetime(text, format="%Y%m%d", errors="coerce")


def _numeric_column(frame: pd.DataFrame, *names: str) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors="coerce")
    return pd.Series(np.nan, index=frame.index, dtype=float)


def history_feature_columns(windows: Iterable[int] = DEFAULT_HISTORY_WINDOWS) -> list[str]:
    columns = [
        "horse_history_total_prior_starts",
        "horse_history_days_since_latest",
        "horse_history_observed_span_days",
        "horse_history_target_date_missing",
    ]
    for window in windows:
        prefix = f"past{int(window)}"
        columns.extend(
            [
                f"{prefix}_count",
                f"{prefix}_coverage",
                f"{prefix}_is_insufficient",
                f"{prefix}_avg_finish",
                f"{prefix}_win_rate",
                f"{prefix}_top3_rate",
                f"{prefix}_avg_last3f_time",
                f"{prefix}_avg_speed_deviation",
            ]
        )
    return columns


def _initialize_features(frame: pd.DataFrame, windows: tuple[int, ...]) -> pd.DataFrame:
    result = frame.copy()
    result["horse_history_total_prior_starts"] = 0
    result["horse_history_days_since_latest"] = np.nan
    result["horse_history_observed_span_days"] = np.nan
    result["horse_history_target_date_missing"] = 1
    for window in windows:
        prefix = f"past{window}"
        result[f"{prefix}_count"] = 0
        result[f"{prefix}_coverage"] = 0.0
        result[f"{prefix}_is_insufficient"] = 1
        for suffix in (
            "avg_finish",
            "win_rate",
            "top3_rate",
            "avg_last3f_time",
            "avg_speed_deviation",
        ):
            result[f"{prefix}_{suffix}"] = np.nan
    return result


def add_point_in_time_horse_history_features(
    targets: pd.DataFrame,
    full_history: pd.DataFrame,
    *,
    windows: Iterable[int] = DEFAULT_HISTORY_WINDOWS,
) -> pd.DataFrame:
    """Add last-N horse features using only strictly earlier result dates.

    The returned frame preserves the target index/order.  Missing history is
    explicit through count, coverage, and insufficient flags.  An audit
    summary is also attached to ``DataFrame.attrs``.
    """
    normalized_windows = tuple(sorted({int(value) for value in windows if int(value) > 0}))
    if not normalized_windows:
        raise ValueError("at least one positive history window is required")

    result = _initialize_features(targets, normalized_windows)
    required = {"horse_id", "race_date"}
    if targets.empty or full_history.empty or not required.issubset(targets.columns):
        result.attrs["point_in_time_history_audit"] = {
            "target_rows": int(len(targets)),
            "matched_rows": 0,
            "future_leakage_rows": 0,
            "invalid_target_date_rows": int(len(targets)),
        }
        return result
    if not required.issubset(full_history.columns):
        result.attrs["point_in_time_history_audit"] = {
            "target_rows": int(len(targets)),
            "matched_rows": 0,
            "future_leakage_rows": 0,
            "invalid_target_date_rows": int(_race_dates(targets["race_date"]).isna().sum()),
            "history_schema_missing": sorted(required - set(full_history.columns)),
        }
        return result

    target = pd.DataFrame(
        {
            "_pit_row": np.arange(len(targets), dtype=np.int64),
            "horse_id": targets["horse_id"].astype("string").fillna("").str.strip().to_numpy(),
            "_target_date": _race_dates(targets["race_date"]).to_numpy(),
        }
    )
    target["horse_id"] = target["horse_id"].astype("string")
    target_valid = target[
        target["_target_date"].notna() & target["horse_id"].ne("")
    ].copy()
    result["horse_history_target_date_missing"] = target["_target_date"].isna().astype(int).to_numpy()

    history = full_history.copy()
    history["horse_id"] = history["horse_id"].astype("string").fillna("").str.strip()
    history["_history_date"] = _race_dates(history["race_date"])
    history["_finish_numeric"] = _numeric_column(history, "finish", "finish_position")
    history = history[
        history["horse_id"].ne("")
        & history["_history_date"].notna()
        & history["_finish_numeric"].gt(0)
    ].copy()
    if "race_id" not in history.columns:
        history["race_id"] = np.arange(len(history)).astype(str)
    history["race_id"] = history["race_id"].astype("string")
    history = history.drop_duplicates(subset=["horse_id", "race_id"], keep="last")

    if target_valid.empty or history.empty:
        result.attrs["point_in_time_history_audit"] = {
            "target_rows": int(len(targets)),
            "matched_rows": 0,
            "future_leakage_rows": 0,
            "invalid_target_date_rows": int(target["_target_date"].isna().sum()),
        }
        return result

    history = history.sort_values(["horse_id", "_history_date", "race_id"], kind="mergesort")
    history["_win"] = history["_finish_numeric"].eq(1).astype(float)
    history["_top3"] = history["_finish_numeric"].le(3).astype(float)
    history["_last3f"] = _numeric_column(history, "last_3f_time", "last_3f")
    history["_speed_deviation"] = _numeric_column(history, "speed_deviation")
    history["_one"] = 1.0

    grouped = history.groupby("horse_id", sort=False)
    history["horse_history_total_prior_starts"] = grouped.cumcount() + 1
    history["_history_first_date"] = grouped["_history_date"].cummin()

    value_columns = {
        "avg_finish": "_finish_numeric",
        "win_rate": "_win",
        "top3_rate": "_top3",
        "avg_last3f_time": "_last3f",
        "avg_speed_deviation": "_speed_deviation",
    }
    generated: list[str] = [
        "horse_history_total_prior_starts",
        "_history_first_date",
    ]
    for window in normalized_windows:
        prefix = f"past{window}"
        count_col = f"{prefix}_count"
        history[count_col] = (
            grouped["_one"].rolling(window, min_periods=1).sum().droplevel(0).reindex(history.index)
        )
        generated.append(count_col)
        for suffix, source in value_columns.items():
            output = f"{prefix}_{suffix}"
            history[output] = (
                grouped[source].rolling(window, min_periods=1).mean().droplevel(0).reindex(history.index)
            )
            generated.append(output)

    right = history[["horse_id", "_history_date"] + generated].copy()
    # merge_asof requires global ordering by the merge timestamp.
    right = right.sort_values(["_history_date", "horse_id"], kind="mergesort")
    left = target_valid.sort_values(["_target_date", "horse_id"], kind="mergesort")
    matched = pd.merge_asof(
        left,
        right,
        left_on="_target_date",
        right_on="_history_date",
        by="horse_id",
        direction="backward",
        allow_exact_matches=False,
    )

    leakage = matched["_history_date"].notna() & (
        matched["_history_date"] >= matched["_target_date"]
    )
    if leakage.any():
        raise RuntimeError("point-in-time history leakage detected")

    matched = matched.set_index("_pit_row").sort_index()
    matched_positions = matched.index.to_numpy(dtype=np.int64)

    def assign_by_position(column: str, values: pd.Series) -> None:
        output = result[column].to_numpy(copy=True)
        output[matched_positions] = values.to_numpy()
        result[column] = output

    prior_count = matched["horse_history_total_prior_starts"].fillna(0).astype(int)
    assign_by_position("horse_history_total_prior_starts", prior_count)
    assign_by_position(
        "horse_history_days_since_latest",
        (matched["_target_date"] - matched["_history_date"]).dt.days,
    )
    assign_by_position(
        "horse_history_observed_span_days",
        (matched["_target_date"] - matched["_history_first_date"]).dt.days,
    )
    for window in normalized_windows:
        prefix = f"past{window}"
        count = matched[f"{prefix}_count"].fillna(0).astype(int)
        assign_by_position(f"{prefix}_count", count)
        assign_by_position(f"{prefix}_coverage", (count / float(window)).clip(upper=1.0))
        assign_by_position(f"{prefix}_is_insufficient", count.lt(window).astype(int))
        for suffix in value_columns:
            assign_by_position(f"{prefix}_{suffix}", matched[f"{prefix}_{suffix}"])

    result.attrs["point_in_time_history_audit"] = {
        "target_rows": int(len(targets)),
        "matched_rows": int(matched["_history_date"].notna().sum()),
        "future_leakage_rows": int(leakage.sum()),
        "invalid_target_date_rows": int(target["_target_date"].isna().sum()),
        "same_day_rows_excluded": True,
        "strict_predicate": "source_race_date < target_race_date",
        "windows": list(normalized_windows),
    }
    return result

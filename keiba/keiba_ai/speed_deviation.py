from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd


BASELINE_SCHEMA = "speed-deviation-baseline-v1"


def _surface_values(frame: pd.DataFrame) -> pd.Series:
    """Return one stable surface label without treating blank text as a category."""

    values = pd.Series("unknown", index=frame.index, dtype="object")
    for column in ("track_type", "surface"):
        if column not in frame.columns:
            continue
        candidate = frame[column].astype("string").str.strip()
        usable = candidate.notna() & ~candidate.isin(["", "None", "nan", "<NA>"])
        values = values.where(~usable, candidate)
    return values.astype(str)


def raw_speed_mps(frame: pd.DataFrame) -> pd.Series:
    """Calculate the realized metres-per-second value used by the target."""

    if "time_seconds" not in frame.columns or "distance" not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    time_seconds = pd.to_numeric(frame["time_seconds"], errors="coerce")
    distance = pd.to_numeric(frame["distance"], errors="coerce")
    valid = time_seconds.gt(0) & distance.gt(0)
    return (distance / time_seconds).where(valid).replace([np.inf, -np.inf], np.nan)


def _group_keys(frame: pd.DataFrame) -> pd.Series:
    if "distance" in frame.columns:
        distance = pd.to_numeric(frame["distance"], errors="coerce").round().astype("Int64")
    else:
        distance = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    return distance.astype("string").fillna("unknown") + "|" + _surface_values(frame)


def fit_speed_deviation_baseline(
    training_frame: pd.DataFrame,
    *,
    min_group_size: int = 30,
) -> dict[str, Any]:
    """Fit target-normalization statistics using training outcomes only."""

    if min_group_size < 2:
        raise ValueError("min_group_size must be at least 2")

    speeds = raw_speed_mps(training_frame)
    valid = speeds.notna()
    if int(valid.sum()) < 2:
        raise ValueError("at least two valid training speeds are required")

    valid_speeds = speeds.loc[valid].astype(float)
    global_std = float(valid_speeds.std(ddof=1))
    if not np.isfinite(global_std) or global_std <= 0:
        raise ValueError("training speed standard deviation must be positive")

    grouped = pd.DataFrame(
        {"key": _group_keys(training_frame).loc[valid], "speed": valid_speeds}
    ).groupby("key", sort=True)["speed"].agg(["count", "mean", "std"])

    groups: dict[str, dict[str, float | int]] = {}
    for key, row in grouped.iterrows():
        count = int(row["count"])
        std = float(row["std"])
        if count < min_group_size or not np.isfinite(std) or std <= 0:
            continue
        groups[str(key)] = {
            "count": count,
            "mean": float(row["mean"]),
            "std": std,
        }

    return {
        "schema": BASELINE_SCHEMA,
        "min_group_size": int(min_group_size),
        "training_sample_count": int(valid.sum()),
        "global_mean": float(valid_speeds.mean()),
        "global_std": global_std,
        "groups": groups,
    }


def apply_speed_deviation_baseline(
    frame: pd.DataFrame,
    baseline: Mapping[str, Any],
) -> pd.Series:
    """Convert realized speed to a deviation using a previously fitted baseline."""

    if baseline.get("schema") != BASELINE_SCHEMA:
        raise ValueError("speed deviation baseline schema is invalid")

    global_mean = float(baseline["global_mean"])
    global_std = float(baseline["global_std"])
    if not np.isfinite(global_std) or global_std <= 0:
        raise ValueError("speed deviation baseline standard deviation is invalid")

    groups = baseline.get("groups")
    if not isinstance(groups, Mapping):
        raise ValueError("speed deviation baseline groups are invalid")

    keys = _group_keys(frame)
    means = keys.map(
        lambda key: float(groups[key]["mean"]) if key in groups else global_mean
    )
    stds = keys.map(
        lambda key: float(groups[key]["std"]) if key in groups else global_std
    )
    speeds = raw_speed_mps(frame)
    return ((speeds - means) / stds).where(speeds.notna()).astype(float)

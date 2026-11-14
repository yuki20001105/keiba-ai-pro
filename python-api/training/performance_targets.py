"""Research labels; current-race clocks never become prediction inputs."""
from __future__ import annotations

import numpy as np
import pandas as pd

from keiba_ai.speed_deviation import raw_speed_mps


_SUMS = ["w", "wx", "wy", "wxx", "wxy", "wyy"]


def _past_sums(frame, keys, days, prefix):
    daily = frame.groupby([*keys, "_day"], sort=True, dropna=False)[_SUMS].sum().reset_index()
    parts = []
    for positions in daily.groupby(keys, sort=False, dropna=False).indices.values():
        block = daily.iloc[positions].sort_values("_day").copy()
        sums = block.set_index("_day")[_SUMS].rolling(
            f"{days}D", closed="left", min_periods=1,
        ).sum().fillna(0).to_numpy()
        block[_SUMS] = sums
        parts.append(block)
    if not parts:
        return pd.DataFrame(columns=[*keys, "_day", *[prefix + name for name in _SUMS]])
    return pd.concat(parts, ignore_index=True).rename(columns={name: prefix + name for name in _SUMS})


def rolling_condition_target(
    frame: pd.DataFrame, *, lookback_days: int = 365, shrinkage_races: float = 20,
    min_parent_races: float = 30, sigma_floor: float = .005, clip: float = 5,
) -> tuple[pd.Series, dict]:
    if (type(lookback_days) is not int or lookback_days < 1
            or not all(np.isfinite(v) and v > 0 for v in
                       (shrinkage_races, min_parent_races, sigma_floor, clip))):
        raise ValueError("invalid-performance-target-parameters")
    required = ["race_id", "race_date", "distance", "surface", "time_seconds"]
    if any(name not in frame for name in required):
        raise ValueError("performance-target-canonical-fields-required")
    data = frame[required].copy().reset_index(drop=True)
    data["_position"] = np.arange(len(data))
    data["_day"] = pd.to_datetime(data.race_date, format="%Y%m%d", errors="raise")
    for name in ["surface", "venue", "field_condition", "weather"]:
        values = frame[name].reset_index(drop=True) if name in frame else pd.Series(pd.NA, index=data.index)
        data[name] = values.astype("string").str.strip().fillna("__missing__").replace("", "__missing__")
    speed = raw_speed_mps(frame).reset_index(drop=True)
    data["distance"] = pd.to_numeric(data.distance, errors="coerce")
    data["_x"] = np.log(data.distance / 1600)
    data["_y"] = np.log(speed)
    valid = np.isfinite(data["_x"]) & np.isfinite(data["_y"]) & data.surface.isin(["turf", "dirt"])
    data["w"] = 1 / data.groupby("race_id").race_id.transform("size")
    data["wx"] = data.w * data["_x"]
    data["wy"] = data.w * data["_y"]
    data["wxx"] = data.wx * data["_x"]
    data["wxy"] = data.wx * data["_y"]
    data["wyy"] = data.wy * data["_y"]
    history = data.loc[valid]
    fine_keys = ["surface", "distance", "venue", "field_condition", "weather"]
    parents = _past_sums(history, ["surface"], lookback_days, "p_")
    children = _past_sums(history, fine_keys, lookback_days, "f_")
    joined = data.merge(parents, on=["surface", "_day"], how="left", validate="many_to_one")
    joined = joined.merge(children, on=[*fine_keys, "_day"], how="left", validate="many_to_one")
    joined = joined.sort_values("_position")
    w = joined.p_w
    mean_x, mean_y = joined.p_wx / w, joined.p_wy / w
    var_x = (joined.p_wxx / w - mean_x ** 2).clip(lower=0)
    cov = joined.p_wxy / w - mean_x * mean_y
    slope = (cov / var_x.where(var_x.gt(1e-12))).fillna(0)
    parent_mean = mean_y + slope * (joined["_x"] - mean_x)
    parent_var = (joined.p_wyy / w - mean_y ** 2 - slope * cov).clip(lower=0)
    fine_w = joined.f_w.fillna(0)
    fine_mean = (joined.f_wy / fine_w.where(fine_w.gt(0))).fillna(parent_mean)
    fine_var = (joined.f_wyy / fine_w.where(fine_w.gt(0)) - fine_mean ** 2).clip(lower=0).fillna(parent_var)
    pooling = fine_w / (fine_w + shrinkage_races)
    mean = pooling * fine_mean + (1 - pooling) * parent_mean
    variance = (
        pooling * fine_var + (1 - pooling) * parent_var
        + pooling * (1 - pooling) * (fine_mean - parent_mean) ** 2
    ).clip(lower=sigma_floor ** 2)
    result = ((joined["_y"] - mean) / np.sqrt(variance)).clip(-clip, clip)
    result = result.where(valid.to_numpy() & w.ge(min_parent_races - 1e-8))
    labels = pd.Series(result.to_numpy(), index=frame.index, name="condition_speed_target")
    return labels, {
        "schema": "past365-race-balanced-condition-log-speed-v1",
        "available_rows": int(labels.notna().sum()), "unavailable_rows": int(labels.isna().sum()),
        "history_rule": "target_date-365days <= history_date < target_date",
        "lookback_days": lookback_days, "shrinkage_races": shrinkage_races,
        "minimum_surface_parent_races": min_parent_races, "sigma_floor": sigma_floor,
        "clip": clip, "race_balanced": True, "class_adjusted": False,
        "cross_surface_global_fallback": False, "same_day_results_used": False,
    }


def race_relative_speed_target(frame: pd.DataFrame) -> pd.Series:
    speed = raw_speed_mps(frame)
    ids = frame.race_id
    mean = speed.groupby(ids).transform("mean")
    std = speed.groupby(ids).transform("std")
    result = ((speed - mean) / std.where(std.gt(0))).where(std.gt(0), 0)
    return result.where(speed.notna()).rename("race_relative_speed_target")

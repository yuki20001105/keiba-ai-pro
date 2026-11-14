"""TRAIN-only speed standardization with coarse, nested condition groups."""
from __future__ import annotations

import numpy as np
import pandas as pd

from keiba_ai.speed_deviation import _group_keys, fit_speed_deviation_baseline, raw_speed_mps


ARMS = ["current_speed", "course", "course_going", "course_going_weather"]


def _keys(frame):
    venue = frame.venue.astype("string").str.strip()
    venue = venue.mask(venue.isin(["", "unknown", "__missing__", "None", "nan"]))
    parts = pd.DataFrame({
        "base": _group_keys(frame), "venue": venue,
        "going": frame.field_condition.astype("string").str.strip().map(
            {"良": "good", "稍重": "wet", "重": "wet", "不良": "wet"}),
        "weather": frame.weather.astype("string").str.strip().map(
            {"晴": "nonprecip", "曇": "nonprecip", "小雨": "precip",
             "雨": "precip", "小雪": "precip", "雪": "precip"}),
    }, index=frame.index)
    result = []
    for columns in [("base", "venue"), ("base", "venue", "going"),
                    ("base", "venue", "going", "weather")]:
        keys = pd.Series(list(zip(*(parts[name].tolist() for name in columns))), index=frame.index)
        result.append(keys.where(parts[list(columns)].notna().all(axis=1), None))
    return result


def fit_group_baseline(frame, arm):
    if arm not in ARMS:
        raise ValueError("unknown-speed-grouping")
    base = fit_speed_deviation_baseline(frame)
    speed = raw_speed_mps(frame)
    levels = []
    for keys in _keys(frame)[:ARMS.index(arm)]:
        statistics = pd.DataFrame({"key": keys, "speed": speed}).groupby(
            "key", sort=True).speed.agg(["count", "mean", "std"])
        groups = {}
        for key, row in statistics.iterrows():
            if row["count"] >= 30 and np.isfinite(row["std"]) and row["std"] > 0:
                groups[key] = {"count": int(row["count"]), "mean": float(row["mean"]),
                               "std": float(row["std"])}
        levels.append(groups)
    return {"schema": "coarse-speed-baseline-v1", "arm": arm, "base": base, "levels": levels}


def normalization(frame, baseline):
    keys = _group_keys(frame)
    base = baseline["base"]
    mean = keys.map(lambda key: base["groups"].get(key, {}).get("mean", base["global_mean"]))
    std = keys.map(lambda key: base["groups"].get(key, {}).get("std", base["global_std"]))
    for keys, groups in zip(_keys(frame), baseline["levels"]):
        child_mean = keys.map(lambda key: groups.get(key, {}).get("mean", np.nan))
        child_std = keys.map(lambda key: groups.get(key, {}).get("std", np.nan))
        mean, std = child_mean.fillna(mean), child_std.fillna(std)
    return mean.astype(float), std.astype(float)


def teacher(frame, baseline):
    mean, std = normalization(frame, baseline)
    return (raw_speed_mps(frame) - mean) / std

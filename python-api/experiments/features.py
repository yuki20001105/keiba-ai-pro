"""Explicit, market-free experimental features from strictly prior DB results.

No scraper ``prev_*`` or current career statistics are consumed. All races on
one date are emitted before that day's results enter any history accumulator.
Counts describe observed eligible history, never proof of complete careers.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
import math

import numpy as np
import pandas as pd


FEATURE_SCHEMA_VERSION = "experiment-safe-features-v1"
BASE_NUMERIC = {
    "safe_distance": "distance", "safe_num_horses": "num_horses",
    "safe_horse_number": "horse_number", "safe_bracket_number": "bracket_number",
    "safe_age": "age", "safe_burden_weight": "burden_weight",
    "safe_horse_weight": "horse_weight", "safe_weight_change": "horse_weight_change",
}
BASE_CATEGORICAL = {
    "safe_surface": "surface", "safe_venue": "venue", "safe_sex": "sex",
    "safe_field_condition": "field_condition", "safe_weather": "weather",
    "safe_race_class": "race_class",
}
HISTORY_COLUMNS = ["safe_prior_starts", "safe_days_since_last"] + [
    f"safe_{prefix}_{field}"
    for prefix in ("prev", "prev2")
    for field in ("finish", "time_seconds", "distance", "speed_mps")
] + [
    f"safe_past{window}_{field}"
    for window in (3, 5, 10)
    for field in ("count", "observed_coverage", "avg_finish", "avg_speed_mps",
                  "avg_last3f_time", "win_rate", "top3_rate")
]
APTITUDE_COLUMNS = [
    f"safe_{group}_{field}"
    for group in ("horse_surface", "horse_distance_band", "horse_course")
    for field in ("starts", "win_rate", "top3_rate", "avg_speed_mps")
] + ["safe_distance_change"]
ENTITY_COLUMNS = [
    f"safe_{entity}_{field}"
    for entity in ("jockey", "trainer", "sire", "damsire")
    for field in ("starts", "win_rate", "top3_rate")
]
FEATURE_GROUPS = {
    "base": list(BASE_NUMERIC) + ["safe_month", "safe_race_number"] + list(BASE_CATEGORICAL),
    "history": HISTORY_COLUMNS,
    "distance_course": APTITUDE_COLUMNS,
    "entities": ENTITY_COLUMNS,
}
ALLOWED_FEATURES = frozenset(column for values in FEATURE_GROUPS.values() for column in values)


def _identity(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return None if text.lower() in {"", "none", "nan", "<na>", "unknown", "unknown_local",
                                   "null", "n/a", "-", "--", "不明"} else text


def _number(value) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else math.nan
    except (TypeError, ValueError):
        return math.nan


def _mean(values) -> float:
    present = [value for value in values if math.isfinite(value)]
    return sum(present) / len(present) if present else math.nan


def _band(distance: float) -> str:
    return "short" if distance < 1400 else "mile" if distance < 1900 else "middle" if distance < 2300 else "long"


def engineer_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Generate only named safe features; unknown input columns never qualify.

    Input must already be a quality-selected, complete-runner cohort. The
    returned frame keeps original columns for evaluator use, but only names in
    ``feature_sets`` may be supplied to any model.
    """
    required = {"race_id", "race_date", "horse_id", "horse_number", "finish",
                "time_seconds", "distance", "surface", "venue", "num_horses"}
    if not required.issubset(frame.columns) or frame.empty:
        raise ValueError("experiment-feature-input-incomplete")
    source = frame.sort_values(["race_date", "race_id", "horse_number"], kind="mergesort").reset_index(drop=True)
    dates = pd.to_datetime(source["race_date"], format="%Y%m%d", errors="coerce")
    if dates.isna().any():
        raise ValueError("experiment-feature-date-invalid")
    ordinals = np.array([value.toordinal() for value in dates.dt.date], dtype=np.int32)
    size = len(source)
    output = {}
    for name, original in BASE_NUMERIC.items():
        values = source[original] if original in source else pd.Series(np.nan, index=source.index)
        output[name] = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).astype("float32")
    output["safe_month"] = dates.dt.month.astype("float32")
    output["safe_race_number"] = pd.to_numeric(source["race_id"].astype(str).str[-2:], errors="coerce").astype("float32")
    for name, original in BASE_CATEGORICAL.items():
        values = source[original] if original in source else pd.Series(None, index=source.index)
        # Encoding belongs to the training partition, not this dataset builder.
        output[name] = pd.Series([_identity(value) for value in values], dtype="string")
    numeric = {name: np.full(size, np.nan, dtype=np.float32)
               for name in HISTORY_COLUMNS + APTITUDE_COLUMNS + ENTITY_COLUMNS}
    columns = ["horse_id", "finish", "time_seconds", "distance", "surface", "venue",
               "last_3f_time", "jockey_id", "trainer_id", "sire", "damsire"]
    arrays = {name: source[name].to_numpy() if name in source else np.full(size, None)
              for name in columns}
    histories: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))
    horse_counts: dict[str, int] = defaultdict(int)
    # count, wins, top3, speed sum, speed count, latest source day
    aggregates: dict[tuple, list] = {}
    referenced_rows = 0
    max_source_date = None
    boundaries = np.r_[0, np.flatnonzero(np.diff(ordinals)) + 1, size]
    for day_start, day_end in zip(boundaries[:-1], boundaries[1:]):
        day = int(ordinals[day_start])
        pending = []
        for position in range(int(day_start), int(day_end)):
            horse = _identity(arrays["horse_id"][position])
            finish = _number(arrays["finish"][position])
            seconds = _number(arrays["time_seconds"][position])
            distance = _number(arrays["distance"][position])
            if not (finish >= 1 and seconds > 0 and distance > 0):
                raise ValueError("experiment-feature-result-invalid")
            speed = distance / seconds
            surface, venue = str(arrays["surface"][position]), str(arrays["venue"][position])
            history = histories.get(horse, ()) if horse else ()
            numeric["safe_prior_starts"][position] = horse_counts.get(horse, 0) if horse else 0
            if history:
                if history[-1][0] >= day:
                    raise ValueError("experiment-history-future-reference")
                referenced_rows += 1
                max_source_date = max(max_source_date or history[-1][0], history[-1][0])
                numeric["safe_days_since_last"][position] = day - history[-1][0]
                numeric["safe_distance_change"][position] = distance - history[-1][3]
                for offset, prefix in ((1, "prev"), (2, "prev2")):
                    if len(history) >= offset:
                        previous = history[-offset]
                        for field, value in zip(("finish", "time_seconds", "distance", "speed_mps"), previous[1:5]):
                            numeric[f"safe_{prefix}_{field}"][position] = value
            for window in (3, 5, 10):
                previous = list(history)[-window:]
                count = len(previous)
                prefix = f"safe_past{window}_"
                numeric[prefix + "count"][position] = count
                numeric[prefix + "observed_coverage"][position] = count / window
                if count:
                    numeric[prefix + "avg_finish"][position] = _mean(value[1] for value in previous)
                    numeric[prefix + "avg_speed_mps"][position] = _mean(value[4] for value in previous)
                    numeric[prefix + "avg_last3f_time"][position] = _mean(value[5] for value in previous)
                    numeric[prefix + "win_rate"][position] = sum(value[1] == 1 for value in previous) / count
                    numeric[prefix + "top3_rate"][position] = sum(value[1] <= 3 for value in previous) / count
            keys = []
            if horse:
                keys += [("horse_surface", horse, surface),
                         ("horse_distance_band", horse, _band(distance)),
                         ("horse_course", horse, venue, surface)]
            for entity, original in (("jockey", "jockey_id"), ("trainer", "trainer_id"),
                                     ("sire", "sire"), ("damsire", "damsire")):
                identifier = _identity(arrays[original][position])
                if identifier:
                    keys.append((entity, identifier))
            for key in keys:
                state = aggregates.get(key)
                group = key[0]
                numeric[f"safe_{group}_starts"][position] = state[0] if state else 0
                if state:
                    if state[5] >= day:
                        raise ValueError("experiment-entity-future-reference")
                    numeric[f"safe_{group}_win_rate"][position] = state[1] / state[0]
                    numeric[f"safe_{group}_top3_rate"][position] = state[2] / state[0]
                    if group.startswith("horse_"):
                        numeric[f"safe_{group}_avg_speed_mps"][position] = state[3] / state[4]
            pending.append((horse, (day, finish, seconds, distance, speed,
                                    _number(arrays["last_3f_time"][position])), keys))
        # No result from this date was visible while constructing its features.
        for horse, observation, keys in pending:
            if horse:
                histories[horse].append(observation)
                horse_counts[horse] += 1
            for key in keys:
                state = aggregates.setdefault(key, [0, 0, 0, 0.0, 0, day])
                state[0] += 1
                state[1] += observation[1] == 1
                state[2] += observation[1] <= 3
                state[3] += observation[4]
                state[4] += 1
                state[5] = day
    output.update(numeric)
    features = pd.DataFrame(output, index=source.index)
    if set(features.columns) != ALLOWED_FEATURES:
        raise ValueError("experiment-feature-allowlist-mismatch")
    # Overwrite a malicious/legacy input named safe_* with generated values.
    source = source.drop(columns=[column for column in source if column in ALLOWED_FEATURES])
    result = pd.concat([source, features], axis=1)
    base = list(FEATURE_GROUPS["base"])
    history = base + list(HISTORY_COLUMNS)
    extended = history + list(APTITUDE_COLUMNS) + list(ENTITY_COLUMNS)
    return result, {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_sets": {"M01": base, "M02": history, "M03": extended, "M05": extended.copy()},
        "feature_groups": {key: list(values) for key, values in FEATURE_GROUPS.items()},
        "categorical_features": list(BASE_CATEGORICAL),
        "point_in_time_audit": {
            "strict_predicate": "source_race_date < target_race_date",
            "same_day_future_references": 0, "future_leakage_rows": 0,
            "rows_with_prior_horse_history": referenced_rows,
            "input_history_rows": size, "same_day_results_excluded": True,
            "latest_referenced_horse_date": datetime.fromordinal(max_source_date).strftime("%Y%m%d") if max_source_date else None,
            "coverage_semantics": "observed eligible rows, not proof of complete career or field coverage",
        },
    }

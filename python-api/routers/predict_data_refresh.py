"""Safe pre-race data refresh helpers for race analysis."""

from __future__ import annotations

from typing import Any

import pandas as pd


RACE_REFRESH_FIELDS = (
    "race_name",
    "venue",
    "date",
    "post_time",
    "race_class",
    "kai",
    "day",
    "course_direction",
    "distance",
    "track_type",
    "weather",
    "field_condition",
    "num_horses",
)

HORSE_REFRESH_FIELDS = (
    "bracket_number",
    "horse_number",
    "horse_name",
    "horse_id",
    "horse_url",
    "sex_age",
    "sex",
    "age",
    "jockey_weight",
    "jockey_name",
    "jockey_id",
    "jockey_url",
    "trainer_name",
    "trainer_id",
    "trainer_url",
    "weight_kg",
    "weight_diff",
    "weight_change",
    "horse_weight",
    "horse_weight_change",
    "odds",
    "popularity",
)


def _usable(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    try:
        return not bool(pd.isna(value))
    except (TypeError, ValueError):
        return True


def race_metadata_invalid(df: pd.DataFrame) -> bool:
    """Return True when the race cannot safely pass the distance gate."""
    if "distance" not in df.columns:
        return True
    distances = pd.to_numeric(df["distance"], errors="coerce")
    return bool(distances.isna().all() or (distances.fillna(0) <= 0).any())


def merge_fresh_pre_race_data(
    df: pd.DataFrame,
    race_info: dict[str, Any],
    fresh: dict[str, Any],
) -> bool:
    """Merge a newly parsed race without copying post-race outcome fields.

    The function mutates ``df`` and ``race_info``.  Only explicitly listed
    pre-race fields are accepted, which prevents result-page finish data from
    leaking into model input.
    """
    fresh_info = fresh.get("race_info") or {}
    changed = False

    for field in RACE_REFRESH_FIELDS:
        value = fresh_info.get(field)
        if not _usable(value):
            continue
        if field == "distance":
            try:
                if float(value) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
        df[field] = value
        race_info[field] = value
        changed = True

    if "track_type" in fresh_info and _usable(fresh_info.get("track_type")):
        df["surface"] = fresh_info["track_type"]

    if "horse_number" not in df.columns:
        return changed

    target_numbers = pd.to_numeric(df["horse_number"], errors="coerce")
    for horse in fresh.get("horses") or []:
        horse_number = horse.get("horse_number")
        try:
            normalized_number = int(float(horse_number))
        except (TypeError, ValueError):
            continue
        mask = target_numbers == normalized_number
        if not bool(mask.any()):
            continue
        for field in HORSE_REFRESH_FIELDS:
            value = horse.get(field)
            if not _usable(value):
                continue
            df.loc[mask, field] = value
            changed = True

        # Keep current weight aliases synchronized from a single fresh scrape.
        # The shared feature contract rejects conflicting aliases, so leaving a
        # stale alias beside a refreshed value would turn a recoverable parse
        # gap into a prediction failure (or, worse, ambiguous input).
        for aliases, source_aliases in (
            (("horse_weight", "weight_kg"), ("weight_kg", "horse_weight", "weight")),
            (("horse_weight_change", "weight_change", "weight_diff"),
             ("weight_change", "horse_weight_change", "weight_diff")),
        ):
            values = []
            for key in source_aliases:
                value = horse.get(key)
                if not _usable(value):
                    continue
                try:
                    numeric = float(value)
                except (TypeError, ValueError, OverflowError):
                    values = []
                    break
                if not pd.notna(numeric):
                    values = []
                    break
                values.append(numeric)
            if values and len(set(values)) == 1:
                for key in aliases:
                    df.loc[mask, key] = values[0]
                changed = True

    return changed

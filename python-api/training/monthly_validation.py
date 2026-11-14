"""Reproducible, race-group monthly studies, separate from model serving."""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd


def _races(target):
    required = {"race_id", "race_date", "horse_number", "num_horses"}
    if target.empty or not required.issubset(target) or target.columns.duplicated().any():
        raise ValueError("monthly-validation-complete-races-required")
    groups = target.groupby("race_id", sort=True)
    if (groups.race_date.nunique(dropna=False).ne(1).any()
            or target.duplicated(["race_id", "horse_number"]).any()
            or not pd.to_numeric(target.num_horses).eq(groups.race_id.transform("size")).all()):
        raise ValueError("monthly-validation-inconsistent-race")
    races = groups.race_date.first().astype(str).reset_index()
    if not races.race_date.str.fullmatch(r"\d{8}").all():
        raise ValueError("monthly-validation-date-required")
    pd.to_datetime(races.race_date, format="%Y%m%d", errors="raise")
    return races.sort_values(["race_date", "race_id"], kind="stable").reset_index(drop=True)


def stratified_partitions(target, *, seed=42):
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("monthly-validation-fixed-integer-seed-required")
    races = _races(target)
    roles = {}
    for month, part in races.groupby(races.race_date.str[:6], sort=True):
        if len(part) < 5:
            raise ValueError("monthly-validation-needs-five-races-per-month")
        ids = np.random.default_rng(np.random.SeedSequence([seed, int(month)])).permutation(
            sorted(part.race_id))
        test = max(1, int(len(ids) * .2))
        cal = max(1, int((len(ids) - test) * .2))
        roles.update({rid: "TEST" if i < test else "CAL" if i < test + cal else "TRAIN"
                      for i, rid in enumerate(ids)})
    return races.assign(partition=races.race_id.map(roles))


def forward_partitions(target, *, year):
    if type(year) is not int or not 2017 <= year <= 2200:
        raise ValueError("monthly-validation-year-invalid")
    races = _races(target)
    start, calibration = f"{year}0101", f"{year-1}0701"
    roles = np.where(races.race_date.lt(calibration), "TRAIN",
                     np.where(races.race_date.lt(start), "CAL",
                              np.where(races.race_date.str[:4].eq(str(year)), "TEST", "UNUSED")))
    return races.assign(partition=roles)


def build_holdout_features(target, history, withheld_races):
    """Withheld outcomes cannot indirectly enter a later training horse's history."""
    from experiments import features as frozen
    from forward_adapters import feature_state as state_builder
    from training import db_shared_features as shared

    current = state_builder._cohort(target, historical=False)
    observed = state_builder._cohort(history.loc[~history.race_id.isin(set(withheld_races))], historical=True)
    if current.empty or (not observed.empty and observed.race_date.lt("20130101").any()):
        raise ValueError("monthly-validation-history-scope")
    envelope = state_builder.build_history_state(observed.iloc[:0], target_date=str(current.race_date.min()))
    state = envelope["state"]
    parts = []
    current_groups = current.groupby("race_date", sort=True).indices
    observed_groups = observed.groupby("race_date", sort=True).indices
    days = sorted(set(current_groups) | set(observed_groups))
    consumed = hashlib.sha256()
    for day in days:
        if day > str(current.race_date.max()):
            break
        if day in current_groups:
            state["target_date"] = day
            day_card = current.iloc[current_groups[day]]
            horses = {frozen._identity(horse) for horse in day_card.horse_id}
            # Hash the read-only horse slice, retaining the full state for later days.
            daily_state = {**state, **{name: {horse: state[name][horse] for horse in horses if horse in state[name]}
                                      for name in ("histories", "horse_counts")}}
            day_features = state_builder.build_current_features(
                {"state": daily_state, "state_sha256": state_builder._hash(daily_state)}, day_card)
            parts.append(day_features[["race_id", "horse_number", *shared.FEATURE_COLUMNS]])
        if day not in observed_groups:
            continue
        ordinal = datetime.strptime(day, "%Y%m%d").date().toordinal()
        group = observed.iloc[observed_groups[day]]
        for row in group.to_dict("records"):
            horse = frozen._identity(row["horse_id"])
            seconds, finish = frozen._number(row["time_seconds"]), frozen._number(row["finish"])
            distance, last3f = frozen._number(row["distance"]), frozen._number(row.get("last_3f_time"))
            consumed.update(state_builder._json({name: state_builder._value(row.get(name)) for name in
                                                 state_builder._IDENTITY + state_builder._META + state_builder._HISTORY}) + b"\n")
            if horse:
                observation = [ordinal, finish, seconds, distance, distance / seconds,
                               last3f if np.isfinite(last3f) else None]
                state["histories"][horse] = (state["histories"].get(horse, []) + [observation])[-10:]
                state["horse_counts"][horse] = state["horse_counts"].get(horse, 0) + 1
        state["history_min_source_date"] = state["history_min_source_date"] or day
        state["history_max_source_date"] = day
        state["history_row_count"] += len(group)
        state["history_race_count"] += int(group.race_id.nunique())
        state["source_rows_sha256"] = consumed.hexdigest()
    keys = ["race_id", "horse_number"]
    result = pd.concat(parts, ignore_index=True).set_index(keys).loc[
        pd.MultiIndex.from_frame(target[keys]), shared.FEATURE_COLUMNS].copy()
    result.index = target.index.copy()
    result.attrs.update(current_outcomes_consumed=False, heldout_outcomes_consumed=False,
                        same_day_or_future_history_used=False,
                        withheld_races=sorted(set(withheld_races)),
                        feature_source_hashes={**shared.source_hashes(),
                            "training.monthly_validation": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    return result

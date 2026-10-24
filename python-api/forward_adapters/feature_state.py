"""Reusable previous-day history state and outcome-free current features.

No database, provider parsing, model fit, or live-readiness assertion. Callers
must supply canonical, quality-selected complete historical races and a proven
complete current card. Current outcomes and market columns are never consumed.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

from experiments import features as legacy
from experiments import search_features as search
from experiments import dataset as canonical
from experiments.dataset import FLAT_DISTANCE_CONTRACT, JRA_VENUES


SCHEMA = "previous-day-canonical-feature-state-v1"
_IDENTITY = ["race_id", "race_date", "horse_id", "horse_number"]
_META = ["distance", "surface", "venue", "num_horses"]
_HISTORY = ["finish", "time_seconds", "last_3f_time", "jockey_id", "trainer_id", "sire", "damsire"]
_ENTITIES = ("jockey", "trainer", "sire", "damsire")


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError("canonical-YYYYMMDD-required")
    return datetime.strptime(value, "%Y%m%d").date()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _modules():
    return {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (legacy, search, canonical)} | {"feature_state": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def _value(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, np.generic):
        value = value.item()
    return value


def _key(parts):
    return _json(list(parts)).decode()


def _entity_keys(horse, row):
    keys = []
    if horse:
        keys += [("horse_surface", horse, str(row["surface"])),
                 ("horse_distance_band", horse, legacy._band(float(row["distance"]))),
                 ("horse_course", horse, str(row["venue"]), str(row["surface"]))]
    for entity in _ENTITIES:
        identifier = legacy._identity(row.get(entity + "_id" if entity in ("jockey", "trainer") else entity))
        if identifier:
            keys.append((entity, identifier))
    return keys


def _cohort(frame, *, historical):
    required = set(_IDENTITY + _META + (["finish", "time_seconds"] if historical else []))
    if not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any() or not required.issubset(frame):
        raise ValueError("canonical-cohort-columns-required")
    if frame.empty:
        return frame.copy()
    value = frame.copy()
    days = value.race_date.astype("string")
    if days.isna().any() or not days.str.fullmatch(r"\d{8}").all():
        raise ValueError("canonical-cohort-date-required")
    pd.to_datetime(days, format="%Y%m%d", errors="raise")
    ids = value.race_id.astype("string")
    if ids.isna().any() or not ids.str.fullmatch(r"\d{12}").all() or not ids.str[:4].eq(days.str[:4]).all():
        raise ValueError("canonical-race-identity-invalid")
    if not ids.str[4:6].isin(JRA_VENUES).all() or not value.venue.eq(ids.str[4:6].map(JRA_VENUES)).all():
        raise ValueError("canonical-jra-venue-invalid")
    for surface, group in value.groupby("surface", dropna=False):
        if surface not in FLAT_DISTANCE_CONTRACT or not pd.to_numeric(group.distance, errors="coerce").isin(FLAT_DISTANCE_CONTRACT[surface]).all():
            raise ValueError("canonical-jra-flat-scope-required")
    number = pd.to_numeric(value.horse_number, errors="coerce")
    if not np.isfinite(number).all() or not number.between(1, 18).all() or not number.eq(np.floor(number)).all():
        raise ValueError("verified-runner-number-required")
    value["horse_number"] = number.astype(int)
    value["race_id"], value["race_date"] = ids.astype(str), days.astype(str)
    value["horse_id"] = value.horse_id.map(legacy._identity).fillna("")
    if value.duplicated(["race_id", "horse_number"]).any() or value.loc[value.horse_id.ne("")].duplicated(["race_id", "horse_id"]).any():
        raise ValueError("duplicate-current-or-history-runner")
    groups = value.groupby("race_id", sort=False)
    counts = groups.race_id.transform("size")
    if not counts.between(2, 18).all() or not pd.to_numeric(value.num_horses, errors="coerce").eq(counts).all():
        raise ValueError("partial-current-or-history-race")
    if groups[["race_date", *_META]].nunique(dropna=False).ne(1).any().any():
        raise ValueError("conflicting-current-or-history-race-metadata")
    if historical:
        finish, seconds = pd.to_numeric(value.finish, errors="coerce"), pd.to_numeric(value.time_seconds, errors="coerce")
        if (not np.isfinite(finish).all() or not finish.between(1, counts).all() or not finish.eq(np.floor(finish)).all()
                or not np.isfinite(seconds).all() or not seconds.gt(0).all() or not finish.eq(1).groupby(value.race_id).any().all()):
            raise ValueError("history-needs-confirmed-complete-results")
    return value.sort_values(["race_date", "race_id", "horse_number"], kind="mergesort").reset_index(drop=True)


def build_history_state(history: pd.DataFrame, *, target_date: str) -> dict:
    """Prepare once, from complete canonical history strictly before target day.

    Historical quality selection belongs upstream; the same 2013-start scope
    and complete-result cohort as the fixed experimental model are required.
    The returned JSON-safe envelope includes its own integrity hash. Empty
    history is explicit (max date is None), never relabelled as fresh coverage.
    """
    _date(target_date)
    if isinstance(history, pd.DataFrame) and history.empty:
        history = history.reindex(columns=list(dict.fromkeys([*history.columns, *_IDENTITY, *_META, *_HISTORY])))
    frame = _cohort(history, historical=True)
    if not frame.empty and (frame.race_date.ge(target_date).any() or frame.race_date.lt("20130101").any()):
        raise ValueError("history-must-be-2013-or-later-and-strictly-before-target")
    histories, counts, aggregates = {}, {}, {}
    consumed = hashlib.sha256()
    input_columns = _IDENTITY + _META + _HISTORY
    for row in frame.reindex(columns=input_columns).to_dict("records"):
        consumed.update(_json({name: _value(row[name]) for name in input_columns}) + b"\n")
        horse = legacy._identity(row["horse_id"])
        day = _date(row["race_date"]).toordinal()
        finish, seconds, distance = (legacy._number(row[name]) for name in ("finish", "time_seconds", "distance"))
        speed, last3f = distance / seconds, legacy._number(row["last_3f_time"])
        observation = [day, finish, seconds, distance, speed, last3f if np.isfinite(last3f) else None]
        if horse:
            histories[horse] = (histories.get(horse, []) + [observation])[-10:]
            counts[horse] = counts.get(horse, 0) + 1
        for key in _entity_keys(horse, row):
            state = aggregates.setdefault(_key(key), [0, 0, 0, 0.0, 0, day])
            state[0] += 1
            state[1] += finish == 1
            state[2] += finish <= 3
            state[3] += speed
            state[4] += 1
            state[5] = day
    state = {"schema": SCHEMA, "target_date": target_date, "history_max_source_date": str(frame.race_date.max()) if not frame.empty else None,
             "history_min_source_date": str(frame.race_date.min()) if not frame.empty else None,
             "history_row_count": len(frame), "history_race_count": int(frame.race_id.nunique()),
             "source_rows_sha256": consumed.hexdigest(), "source_modules": _modules(),
             "histories": histories, "horse_counts": counts, "aggregates": aggregates,
             "history_policy": "source_race_date < target_date; observed eligible history, not complete career coverage",
             "live_card_parser_verified": False}
    return {"state": state, "state_sha256": _hash(state)}


def build_current_features(envelope: dict, current: pd.DataFrame) -> pd.DataFrame:
    """Return canonical identity/metadata plus all 101 safe features, no labels.

    State is read-only and reusable for every complete race on its target day.
    This does not assert that caller-supplied raw card fields were observed at
    a valid live cutoff. It never invokes a training eligibility target filter.
    """
    state = envelope["state"]
    if state.get("schema") != SCHEMA or envelope.get("state_sha256") != _hash(state) or state.get("source_modules") != _modules():
        raise ValueError("history-state-or-feature-source-integrity-mismatch")
    _date(state["target_date"])
    source = _cohort(current, historical=False)
    if source.empty or not source.race_date.eq(state["target_date"]).all():
        raise ValueError("current-races-must-match-prepared-target-day")
    maximum = state["history_max_source_date"]
    if maximum is not None and _date(maximum) >= _date(state["target_date"]):
        raise ValueError("history-state-has-same-day-or-future-source")
    output = {}
    for name, original in legacy.BASE_NUMERIC.items():
        values = source[original] if original in source else pd.Series(np.nan, index=source.index)
        output[name] = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).astype("float32")
    output["safe_month"] = pd.to_datetime(source.race_date, format="%Y%m%d").dt.month.astype("float32")
    output["safe_race_number"] = pd.to_numeric(source.race_id.str[-2:]).astype("float32")
    for name, original in legacy.BASE_CATEGORICAL.items():
        values = source[original] if original in source else pd.Series(None, index=source.index)
        output[name] = pd.Series([legacy._identity(value) for value in values], dtype="string")
    numeric = {name: np.full(len(source), np.nan, dtype=np.float32)
               for name in legacy.HISTORY_COLUMNS + legacy.APTITUDE_COLUMNS + legacy.ENTITY_COLUMNS}
    for position, row in enumerate(source.to_dict("records")):
        horse, day = legacy._identity(row["horse_id"]), _date(row["race_date"]).toordinal()
        history = state["histories"].get(horse, []) if horse else []
        numeric["safe_prior_starts"][position] = state["horse_counts"].get(horse, 0) if horse else 0
        if history:
            if any(observation[0] >= day for observation in history):
                raise ValueError("future-horse-history-state")
            numeric["safe_days_since_last"][position] = day - history[-1][0]
            numeric["safe_distance_change"][position] = float(row["distance"]) - history[-1][3]
            for offset, prefix in ((1, "prev"), (2, "prev2")):
                if len(history) >= offset:
                    for field, value in zip(("finish", "time_seconds", "distance", "speed_mps"), history[-offset][1:5]):
                        numeric[f"safe_{prefix}_{field}"][position] = value
        for window in (3, 5, 10):
            previous = history[-window:]
            count, prefix = len(previous), f"safe_past{window}_"
            numeric[prefix + "count"][position] = count
            numeric[prefix + "observed_coverage"][position] = count / window
            if count:
                numeric[prefix + "avg_finish"][position] = legacy._mean(value[1] for value in previous)
                numeric[prefix + "avg_speed_mps"][position] = legacy._mean(value[4] for value in previous)
                numeric[prefix + "avg_last3f_time"][position] = legacy._mean(legacy._number(value[5]) for value in previous)
                numeric[prefix + "win_rate"][position] = sum(value[1] == 1 for value in previous) / count
                numeric[prefix + "top3_rate"][position] = sum(value[1] <= 3 for value in previous) / count
        for key in _entity_keys(horse, row):
            aggregate = state["aggregates"].get(_key(key))
            group = key[0]
            numeric[f"safe_{group}_starts"][position] = aggregate[0] if aggregate else 0
            if aggregate:
                if aggregate[5] >= day:
                    raise ValueError("future-entity-history-state")
                numeric[f"safe_{group}_win_rate"][position] = aggregate[1] / aggregate[0]
                numeric[f"safe_{group}_top3_rate"][position] = aggregate[2] / aggregate[0]
                if group.startswith("horse_"):
                    numeric[f"safe_{group}_avg_speed_mps"][position] = aggregate[3] / aggregate[4]
    output.update(numeric)
    features = pd.DataFrame(output, index=source.index)
    if set(features) != legacy.ALLOWED_FEATURES:
        raise ValueError("base-feature-schema-mismatch")
    result = pd.concat([source[_IDENTITY + _META], features], axis=1)
    derived = search.derive_search_features(result)
    result = pd.concat([result, derived], axis=1)
    result.attrs.update(state_sha256=envelope["state_sha256"], history_max_source_date=maximum,
                        target_date=state["target_date"], input_outcomes_consumed=False,
                        live_card_parser_verified=False, feature_count=len(features.columns) + len(derived.columns))
    return result

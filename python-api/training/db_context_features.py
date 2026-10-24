"""Isolated fixed context profile, built from strictly earlier saved results.

This does not modify the frozen 46-feature formula, activate a model, fetch a
page, infer turf damage from the official going, or diagnose health from weight.
Race pressure and gate statistics are historical proxies, not causal effects.
Static ancestry names require consistent *earlier* observations of this horse;
neither current profile totals nor future identity corrections are consumed.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict, deque
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from threading import RLock
from types import MappingProxyType

import numpy as np
import pandas as pd

from experiments import dataset as canonical
from experiments import features as frozen
from forward_adapters import feature_state
from training import db_shared_features as shared
from training import db_shared_optimized as optimized


FEATURE_PIPELINE = "local-db-context-v1"
FEATURE_VERSION = FEATURE_PIPELINE
FEATURE_GROUPS = {
    "baseline46": list(optimized.FEATURE_COLUMNS),
    "condition_aptitude": ["ctx_condition_starts", "ctx_condition_rank_mean", "ctx_condition_speed_delta_mean",
                           "ctx_course_starts", "ctx_course_rank_mean", "ctx_course_speed_delta_mean",
                           "ctx_surface_changed", "ctx_distance_change_ratio"],
    "weight": ["ctx_weight_history_count5", "ctx_weight_delta_last", "ctx_weight_delta_mean5",
               "ctx_weight_std5", "ctx_weight_change_pct", "ctx_weight_trend5"],
    "pedigree": ["ctx_sire_surface_starts", "ctx_sire_surface_rank_mean",
                 "ctx_sire_condition_starts", "ctx_sire_condition_rank_mean",
                 "ctx_damsire_surface_starts", "ctx_damsire_surface_rank_mean",
                 "ctx_pedigree_verified_prior"],
    "gate": ["ctx_gate_fraction", "ctx_gate_prior_count", "ctx_gate_rank_mean", "ctx_gate_rank_delta"],
    "style_pace": ["ctx_style_count5", "ctx_early_position_mean5", "ctx_early_position_std5",
                   "ctx_front_rate5", "ctx_late_gain_mean5", "ctx_field_front_pressure",
                   "ctx_other_front_pressure", "ctx_field_style_coverage", "ctx_style_pressure_interaction"],
}
FEATURE_COLUMNS = tuple(name for group in FEATURE_GROUPS.values() for name in group)
CATEGORICAL_COLUMNS = tuple(optimized.CATEGORICAL_COLUMNS)
EXTRA_COLUMNS = tuple(name for name in FEATURE_COLUMNS if name not in optimized.FEATURE_COLUMNS)
_SHRINKAGE = 20.0
_KEYS = ["race_id", "horse_number"]
_CORNER_COLUMN = "_ctx_history_corners"
FEATURE_LABELS = {
    "ctx_condition_starts": "同じ芝/ダート・馬場区分の過去走数",
    "ctx_condition_rank_mean": "同じ馬場区分の過去相対成績",
    "ctx_condition_speed_delta_mean": "同じ馬場区分の過去レース内速度差",
    "ctx_course_starts": "同じ競馬場・芝/ダートの過去走数",
    "ctx_course_rank_mean": "同じ競馬場の過去相対成績",
    "ctx_course_speed_delta_mean": "同じ競馬場の過去レース内速度差",
    "ctx_surface_changed": "前走から芝/ダートの変更",
    "ctx_distance_change_ratio": "前走から距離の変更割合",
    "ctx_weight_history_count5": "直近5走の馬体重記録数",
    "ctx_weight_delta_last": "前走の記録体重との差",
    "ctx_weight_delta_mean5": "直近5走の記録体重平均との差",
    "ctx_weight_std5": "直近5走の記録体重のばらつき",
    "ctx_weight_change_pct": "公開馬体重増減の割合",
    "ctx_weight_trend5": "直近5走の記録体重の傾向",
    "ctx_sire_surface_starts": "父の同じ芝/ダートの過去産駒記録数",
    "ctx_sire_surface_rank_mean": "父の同じ芝/ダートの過去産駒相対成績",
    "ctx_sire_condition_starts": "父の同じ馬場区分の過去産駒記録数",
    "ctx_sire_condition_rank_mean": "父の同じ馬場区分の過去産駒相対成績",
    "ctx_damsire_surface_starts": "母父の同じ芝/ダートの過去産駒記録数",
    "ctx_damsire_surface_rank_mean": "母父の同じ芝/ダートの過去産駒相対成績",
    "ctx_pedigree_verified_prior": "対象馬の血統表記の過去一貫性",
    "ctx_gate_fraction": "出走頭数に対する馬番の相対位置",
    "ctx_gate_prior_count": "同じ競馬場・条件・馬番帯の過去記録数",
    "ctx_gate_rank_mean": "同じ条件の馬番帯の過去相対成績",
    "ctx_gate_rank_delta": "同じ条件全体に対する馬番帯の過去成績差",
    "ctx_style_count5": "直近5走の通過順位記録数",
    "ctx_early_position_mean5": "過去の先頭記録通過位置の平均",
    "ctx_early_position_std5": "過去の先頭記録通過位置のばらつき",
    "ctx_front_rate5": "過去の前方通過割合",
    "ctx_late_gain_mean5": "過去の先頭から最終記録通過位置の改善",
    "ctx_field_front_pressure": "記録がある出走馬の前方通過傾向",
    "ctx_other_front_pressure": "自分を除く出走馬の前方通過傾向",
    "ctx_field_style_coverage": "今回の出走馬の過去通過順位充足率",
    "ctx_style_pressure_interaction": "自分と他馬の前方通過傾向の組み合わせ",
}
_OWN_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_SERVING_STATES = OrderedDict()
_SERVING_LOCK = RLock()
_MAX_SERVING_DAYS = 1


def feature_source_hashes() -> dict[str, str]:
    own = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if own != _OWN_HASH:
        raise ValueError("context-feature-source-changed-since-import-restart-required")
    return {**optimized.feature_source_hashes(), "training.db_context_features": own}


source_hashes = feature_source_hashes


def _number(value) -> float:
    return frozen._number(value)


def _identity(value):
    return frozen._identity(value)


def _condition(value):
    # The saved parser's official Japanese going categories only. Unknown,
    # masked and prose descriptions must not become invented going classes.
    text = _identity(value)
    return text if text in {"良", "稍重", "重", "不良"} else None


def _corners(value, field_size) -> tuple[int, ...] | None:
    """Ordered recorded positions; the first slot is NOT always the first turn.

    Straight races and masked/invalid observations remain missing. The legacy
    classifier's default eight-runner field and zero-variance fill are not used.
    """
    size = _number(field_size)
    if not (size >= 2 and size == int(size)):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = json.loads(text) if text.startswith("[") else text.split("-")
        except (ValueError, TypeError):
            return None
    if (not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 4
            or any(isinstance(item, (bool, np.bool_)) for item in value)):
        return None
    numbers = [_number(item) for item in value]
    if not all(math.isfinite(item) and item == int(item) and 1 <= item <= size for item in numbers):
        return None
    return tuple(int(item) for item in numbers)


def _attach_history_corners(db_path, history: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Read narrow result-only extras, keyed by race + runner + horse identity."""
    result = history.copy()
    values = [None] * len(result)
    locations = {(str(rid), int(number)): (position, _identity(horse), count)
                 for position, (rid, number, horse, count) in enumerate(zip(
                     result.race_id, result.horse_number, result.horse_id, result.num_horses))}
    seen, invalid, conflicts = set(), 0, 0
    path = Path(db_path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        ids = sorted(set(result.race_id.astype(str)))
        for offset in range(0, len(ids), 400):
            batch = ids[offset:offset + 400]
            marks = ",".join("?" for _ in batch)
            for rid, horse, number, recorded, text in connection.execute(
                "SELECT race_id,json_extract(data,'$.horse_id'),json_extract(data,'$.horse_number'),"
                "json_extract(data,'$.corner_positions_list'),json_extract(data,'$.corner_positions') "
                f"FROM race_results_ultimate WHERE json_valid(data) AND race_id IN ({marks})", batch,
            ):
                numeric = _number(number)
                if not math.isfinite(numeric) or numeric != int(numeric):
                    continue
                key = (str(rid), int(numeric))
                location = locations.get(key)
                if location is None:
                    continue
                position, expected_horse, count = location
                if key in seen or _identity(horse) != expected_horse:
                    conflicts += 1
                    values[position] = None
                    # A repeated identity can never regain authority later.
                    seen.add(key)
                    continue
                seen.add(key)
                array = _corners(recorded, count)
                fallback = _corners(text, count)
                if array is not None and fallback is not None and array != fallback:
                    conflicts += 1
                else:
                    values[position] = array if array is not None else fallback
                    invalid += values[position] is None
    finally:
        connection.close()
    result[_CORNER_COLUMN] = pd.Series(values, index=result.index, dtype=object)
    return result, {"rows": len(result), "available": sum(value is not None for value in values),
                    "missing_or_invalid": invalid, "identity_or_alias_conflicts": conflicts,
                    "meaning": "ordered recorded passage positions, not actual first-turn number"}


def load_db_frames(db_path, date_from=None, date_to=None):
    """Full eligible population is required for bloodline and gate denominators.

    New extras are read in a second RO transaction. Normal training supplies an
    immutable online-backup snapshot; serving checks its DB revision upstream.
    """
    lower = shared._period_date(date_from, end=False)
    upper = shared._period_date(date_to, end=True)
    if lower < "20130101" or lower > upper:
        raise ValueError("context-period-outside-contract")
    all_targets, history, coverage = shared.load_db_frames(db_path, "2013-01", upper)
    history, corner_audit = _attach_history_corners(db_path, history)
    targets = all_targets.loc[all_targets.race_date.between(lower, upper)].reset_index(drop=True)
    return targets, history, {**coverage, "feature_version": FEATURE_PIPELINE,
        "date_from": lower, "date_to": upper, "target": {"eligible_rows": len(targets),
        "eligible_races": int(targets.race_id.nunique())}, "context_corner_audit": corner_audit,
        "context_population": "all complete eligible JRA flat races from 2013, not target-horse-selected progeny",
        "source_hashes": feature_source_hashes(), "capture_timing_verified": False,
        "context_caveats": _caveats()}


def _caveats():
    return {"going_is_not_turf_damage": True, "gate_bias_is_not_causal": True,
            "weight_is_not_health_diagnosis": True, "pace_is_historical_style_proxy_not_lap_forecast": True,
            "pedigree_fact_capture_time_verified": False,
            "pedigree_policy": "one unique prior pair for this horse and exact current pair agreement",
            "complete_career_coverage_verified": False, "shrinkage_prior_count": _SHRINKAGE}


def _state_mean(state, component):
    return state[component] / state[0] if state and state[0] else math.nan


def _shrunk(state, parent, component):
    if not state or not state[0]:
        return math.nan
    parent_mean = _state_mean(parent, component)
    return ((state[component] + _SHRINKAGE * parent_mean) / (state[0] + _SHRINKAGE)
            if math.isfinite(parent_mean) else _state_mean(state, component))


def _extra_features(target, history, *, _prepared=None, _state_only=False):
    size = len(target)
    output = {name: np.full(size, np.nan, dtype=np.float32) for name in EXTRA_COLUMNS}
    # All computation views are allowlisted. Target results, odds, comments and
    # corners never enter this view, even when the caller supplies those fields.
    current_names = ["race_id", "race_date", "horse_id", "horse_number", "num_horses", "surface", "venue",
                     "distance", "field_condition", "horse_weight", "horse_weight_change", "sire", "damsire"]
    current = target.reindex(columns=current_names).reset_index(drop=True)
    current["_position"] = np.arange(size)
    current = current.sort_values(["race_date", "race_id", "horse_number"], kind="stable")
    earlier = (history.reindex(columns=[*canonical.SOURCE_COLUMNS, _CORNER_COLUMN]).copy()
               if _prepared is None else pd.DataFrame(columns=[*canonical.SOURCE_COLUMNS, _CORNER_COLUMN]))
    earlier = earlier.sort_values(["race_date", "race_id", "horse_number"], kind="stable")
    # These are already completed historical race results. They become visible
    # only after the whole source date has been emitted, never inside that day.
    speeds = pd.to_numeric(earlier.distance) / pd.to_numeric(earlier.time_seconds)
    earlier["_speed_delta"] = speeds - speeds.groupby(earlier.race_id, sort=False).transform("mean")
    earlier["_rank"] = 1.0 - (pd.to_numeric(earlier.finish) - 1) / (pd.to_numeric(earlier.num_horses) - 1)
    states = {} if _prepared is None else _prepared["states"]
    recent = defaultdict(lambda: deque(maxlen=5)) if _prepared is None else _prepared["recent"]
    ancestry = {} if _prepared is None else _prepared["ancestry"]
    current_days = {str(day): group for day, group in current.groupby("race_date", sort=False)}
    history_days = iter(earlier.groupby("race_date", sort=False))
    pending_day = next(history_days, None)
    latest_used = None if _prepared is None else _prepared["latest_source_date"]
    pedigree_unverified = 0

    def add(day_rows, day):
        nonlocal latest_used
        fields = ["horse_id", "distance", "surface", "venue", "field_condition", "horse_number", "num_horses",
                  "horse_weight", "sire", "damsire", "_rank", "_speed_delta", _CORNER_COLUMN]
        for values in day_rows[fields].itertuples(index=False, name=None):
            horse, distance, surface, venue, condition, number, count, weight, sire, damsire, rank, delta, corners = values
            horse, sire, damsire = map(_identity, (horse, sire, damsire))
            condition = _condition(condition)
            distance, count, number, weight = map(_number, (distance, count, number, weight))
            pair = (sire, damsire) if sire and damsire else None
            if horse:
                if pair:
                    if horse not in ancestry:
                        ancestry[horse] = pair
                    elif ancestry[horse] != pair:
                        ancestry[horse] = False
                passage = _corners(corners, count)
                first = (passage[0] - 1) / (count - 1) if passage else math.nan
                gain = (passage[0] - passage[-1]) / (count - 1) if passage else math.nan
                recent[horse].append((day, distance, str(surface), weight, first, gain))
            keys = [("population",), ("venue", str(venue), str(surface), frozen._band(distance))]
            fraction = (number - 1) / (count - 1)
            gate = min(2, int(fraction * 3))
            if condition:
                keys.append(("gate", str(venue), str(surface), frozen._band(distance), condition, gate))
            if horse:
                keys += [("horse", horse), ("course", horse, str(venue), str(surface))]
                if condition:
                    keys.append(("condition", horse, str(surface), condition))
            # Do not let a known-conflicting horse ancestry contribute to new
            # population statistics. Previously observed source rows stay fixed.
            verified_pair = horse and pair and ancestry.get(horse) == pair
            if verified_pair:
                keys += [("sire", sire), ("sire_surface", sire, str(surface)),
                         ("damsire", damsire), ("damsire_surface", damsire, str(surface))]
                if condition:
                    keys.append(("sire_condition", sire, str(surface), condition))
            for key in keys:
                state = states.setdefault(key, [0, 0.0, 0.0])
                state[0] += 1
                state[1] += float(rank)
                state[2] += float(delta)
        latest_used = day

    for day, rows in current_days.items():
        while pending_day is not None and str(pending_day[0]) < day:
            add(pending_day[1], str(pending_day[0]))
            pending_day = next(history_days, None)
        if _state_only:
            if len(current_days) != 1:
                raise ValueError("context-serving-state-single-target-date-required")
            return {"states": MappingProxyType({key: tuple(value) for key, value in states.items()}),
                    "recent": MappingProxyType({key: tuple(value) for key, value in recent.items()}),
                    "ancestry": MappingProxyType(dict(ancestry)), "target_date": day,
                    "latest_source_date": latest_used, "history_rows": len(history)}
        for raw in rows.itertuples(index=False, name=None):
            # Explicit tuple ordering avoids pandas rewriting underscored names.
            mapping = dict(zip(current.columns, raw))
            position = int(mapping["_position"])
            horse, sire, damsire = map(_identity, (
                mapping["horse_id"], mapping["sire"], mapping["damsire"]))
            condition = _condition(mapping["field_condition"])
            surface, venue = str(mapping["surface"]), str(mapping["venue"])
            distance, count, number, weight, change = map(_number, (
                mapping["distance"], mapping["num_horses"], mapping["horse_number"],
                mapping["horse_weight"], mapping["horse_weight_change"]))
            past = list(recent.get(horse, ())) if horse else []
            parent = states.get(("horse", horse)) if horse else None
            for prefix, key in (("condition", ("condition", horse, surface, condition)),
                                ("course", ("course", horse, venue, surface))):
                state = states.get(key) if horse and (prefix != "condition" or condition) else None
                output[f"ctx_{prefix}_starts"][position] = (
                    state[0] if state else (0 if prefix != "condition" or condition else math.nan))
                output[f"ctx_{prefix}_rank_mean"][position] = _shrunk(state, parent, 1)
                output[f"ctx_{prefix}_speed_delta_mean"][position] = _shrunk(state, parent, 2)
            if past:
                output["ctx_surface_changed"][position] = float(past[-1][2] != surface)
                if past[-1][1] > 0:
                    output["ctx_distance_change_ratio"][position] = (distance - past[-1][1]) / past[-1][1]
            weights = [(index, item[3]) for index, item in enumerate(past) if math.isfinite(item[3]) and item[3] > 0]
            output["ctx_weight_history_count5"][position] = len(weights)
            if math.isfinite(weight) and weight > 0 and weights:
                output["ctx_weight_delta_mean5"][position] = weight - float(np.mean([value for _, value in weights]))
                if math.isfinite(past[-1][3]) and past[-1][3] > 0:
                    output["ctx_weight_delta_last"][position] = weight - past[-1][3]
            if len(weights) >= 2:
                x, y = np.array(weights, dtype=float).T
                output["ctx_weight_std5"][position] = float(np.std(y, ddof=1))
                output["ctx_weight_trend5"][position] = float(np.sum((x-x.mean())*(y-y.mean())) / np.sum((x-x.mean())**2))
            if math.isfinite(weight) and weight > 0 and math.isfinite(change):
                output["ctx_weight_change_pct"][position] = change / weight
            pair = (sire, damsire) if sire and damsire else None
            verified = bool(horse and pair and ancestry.get(horse) == pair)
            output["ctx_pedigree_verified_prior"][position] = float(verified)
            if verified:
                for prefix, key, parent_key in (
                    ("sire_surface", ("sire_surface", sire, surface), ("sire", sire)),
                    ("sire_condition", ("sire_condition", sire, surface, condition), ("sire_surface", sire, surface)),
                    ("damsire_surface", ("damsire_surface", damsire, surface), ("damsire", damsire))):
                    state = states.get(key) if prefix != "sire_condition" or condition else None
                    output[f"ctx_{prefix}_starts"][position] = (
                        state[0] if state else (0 if prefix != "sire_condition" or condition else math.nan))
                    output[f"ctx_{prefix}_rank_mean"][position] = _shrunk(state, states.get(parent_key), 1)
            else:
                pedigree_unverified += 1
            fraction = (number - 1) / (count - 1)
            output["ctx_gate_fraction"][position] = fraction
            venue_parent = states.get(("venue", venue, surface, frozen._band(distance)))
            state = states.get(("gate", venue, surface, frozen._band(distance), condition,
                                min(2, int(fraction * 3)))) if condition else None
            output["ctx_gate_prior_count"][position] = state[0] if state else (0 if condition else math.nan)
            gate_mean = _shrunk(state, venue_parent, 1)
            output["ctx_gate_rank_mean"][position] = gate_mean
            output["ctx_gate_rank_delta"][position] = gate_mean - _state_mean(venue_parent, 1)
            styles = [item for item in past if math.isfinite(item[4])]
            output["ctx_style_count5"][position] = len(styles)
            if styles:
                early = np.array([item[4] for item in styles], dtype=float)
                output["ctx_early_position_mean5"][position] = float(early.mean())
                output["ctx_front_rate5"][position] = float(np.mean(early <= 1/3))
                output["ctx_late_gain_mean5"][position] = float(np.mean([item[5] for item in styles]))
                if len(styles) >= 2:
                    output["ctx_early_position_std5"][position] = float(early.std(ddof=1))
    # Current-field aggregation is vectorized and uses only prior style features.
    race = target.race_id.reset_index(drop=True)
    front = pd.Series(output["ctx_front_rate5"])
    grouped = front.groupby(race, sort=False)
    known = grouped.transform("count")
    total = grouped.transform("sum", min_count=1)
    output["ctx_field_front_pressure"] = (total / known.where(known.gt(0))).to_numpy(dtype=np.float32)
    denominator = known - front.notna().astype(int)
    others = (total - front.fillna(0)) / denominator.where(denominator.gt(0))
    output["ctx_other_front_pressure"] = others.to_numpy(dtype=np.float32)
    output["ctx_field_style_coverage"] = (known / race.groupby(race, sort=False).transform("size")).to_numpy(dtype=np.float32)
    output["ctx_style_pressure_interaction"] = (front * others).to_numpy(dtype=np.float32)
    result = pd.DataFrame(output, index=target.index).reindex(columns=EXTRA_COLUMNS)
    result.attrs["context_audit"] = {"historical_population_rows": len(history) if _prepared is None else _prepared["history_rows"],
        "latest_context_source_date": latest_used, "pedigree_unverified_target_rows": pedigree_unverified,
        "strict_predicate": "history_race_date < target_race_date", "same_day_results_used": False,
        "target_corners_or_outcomes_consumed": False, **_caveats()}
    return result


def _quality_revision(db_path) -> str:
    """Quality exclusions also affect history, unlike unrelated prediction logs."""
    path = Path(db_path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    digest = hashlib.sha256()
    try:
        connection.execute("PRAGMA query_only=ON")
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scrape_race_acquisition_state'").fetchone()
        if exists:
            for row in connection.execute("SELECT race_id,lifecycle,quality_status,excluded_from_standard "
                                          "FROM scrape_race_acquisition_state ORDER BY race_id"):
                digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
    finally:
        connection.close()
    return digest.hexdigest()


def _source_revision(db_path):
    from services.prediction_source_revision import read_source_revision
    return {**read_source_revision(Path(db_path).resolve(strict=True)),
            "quality_revision": _quality_revision(db_path)}


def _baseline_state(history, day):
    """Only horse history is necessary for the unchanged 46 baseline columns.

    Entity and aptitude aggregates in the forward adapter are excluded from
    the baseline46. A small per-card integrity envelope is made at inference;
    a large global state is neither re-hashed nor replayed for every race.
    """
    histories, counts = defaultdict(lambda: deque(maxlen=10)), defaultdict(int)
    ordinals = {str(value): datetime.strptime(str(value), "%Y%m%d").toordinal()
                for value in history.race_date.unique()}
    names = ["horse_id", "race_date", "finish", "time_seconds", "distance", "last_3f_time"]
    for horse, date_value, finish, seconds, distance, last3f in history.reindex(columns=names).itertuples(index=False, name=None):
        horse = _identity(horse)
        if not horse:
            continue
        finish, seconds, distance, last3f = map(_number, (finish, seconds, distance, last3f))
        histories[horse].append((ordinals[str(date_value)], finish, seconds, distance, distance/seconds,
                                last3f if math.isfinite(last3f) else None))
        counts[horse] += 1
    return {"histories": MappingProxyType({key: tuple(value) for key, value in histories.items()}),
            "horse_counts": MappingProxyType(dict(counts)), "target_date": day,
            "history_max_source_date": str(history.race_date.max()) if not history.empty else None,
            "source_modules": feature_state._modules()}


def _current_baseline(prepared, current):
    baseline = prepared["baseline"]
    horses = {_identity(value) for value in current.horse_id if _identity(value)}
    state = {"schema": feature_state.SCHEMA, "target_date": baseline["target_date"],
             "history_max_source_date": baseline["history_max_source_date"],
             "source_modules": baseline["source_modules"],
             "histories": {horse: baseline["histories"].get(horse, ()) for horse in horses},
             "horse_counts": {horse: baseline["horse_counts"].get(horse, 0) for horse in horses},
             "aggregates": {}}
    # The caller receives only the fixed46, not the deliberately omitted
    # nonbaseline entity features produced by the frozen forward helper.
    envelope = {"state": state, "state_sha256": feature_state._hash(state)}
    generated = feature_state.build_current_features(envelope, current)
    keys = pd.MultiIndex.from_frame(current[_KEYS])
    result = generated.set_index(_KEYS).loc[keys, list(optimized.FEATURE_COLUMNS)].copy()
    result.index = current.index.copy()
    return result


def _serving_state(db_path, current):
    """Single-flight, immutable, revision-bound previous-day state (one day)."""
    path = Path(db_path).resolve(strict=True)
    day = str(current.race_date.iloc[0])
    sources = feature_source_hashes()
    source_digest = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    with _SERVING_LOCK:
        before = _source_revision(path)
        key = (str(path), before["data_revision"], before["quality_revision"], day, source_digest)
        cached = _SERVING_STATES.get(key)
        if cached is not None:
            _SERVING_STATES.move_to_end(key)
            return cached, True, before
        # Old revision/day state cannot remain authoritative. Removing only
        # this in-process cache does not change DB, paper records or model files.
        _SERVING_STATES.clear()
        _, history, coverage = load_db_frames(path, "2013-01", day)
        history = history.loc[history.race_date.lt(day)].reset_index(drop=True)
        checked = feature_state._cohort(history, historical=True)
        if not checked.empty and checked.race_date.ge(day).any():
            raise ValueError("context-serving-history-not-strictly-before-target")
        prepared = MappingProxyType({"context": _extra_features(current, history, _state_only=True),
                    "baseline": _baseline_state(checked, day), "coverage": coverage,
                    "history_rows": len(history),
                    "history_max_source_date": str(history.race_date.max()) if not history.empty else None,
                    "source_revision": before, "feature_source_hashes": sources})
        if _source_revision(path) != before or feature_source_hashes() != sources:
            raise ValueError("context-source-changed-during-preparation-retry")
        _SERVING_STATES[key] = prepared
        while len(_SERVING_STATES) > _MAX_SERVING_DAYS:
            _SERVING_STATES.popitem(last=False)
        return prepared, False, before


def build_training_features(target: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(target, pd.DataFrame) or target.columns.duplicated().any():
        raise ValueError("context-target-frame-required")
    if target.empty:
        return pd.DataFrame(index=target.index, columns=FEATURE_COLUMNS)
    # Existing raw47 validation supplies identity, complete cohort, strict date,
    # numeric result and no-intermediate-missing-history checks unchanged.
    raw47 = shared.build_training_features(target, history)
    baseline = optimized.select_features(raw47)
    extra = _extra_features(target, history)
    result = pd.concat([baseline, extra], axis=1).loc[:, list(FEATURE_COLUMNS)]
    result.attrs.update(raw47.attrs)
    result.attrs.update(feature_version=FEATURE_PIPELINE, source_hashes=feature_source_hashes(),
                        context_audit=extra.attrs["context_audit"], context_caveats=_caveats())
    return result


def build_serving_features(db_path, race_info, horses):
    current = shared._current_card(race_info, horses)
    prepared, cache_hit, revision = _serving_state(db_path, current)
    baseline = _current_baseline(prepared, current)
    # Only a prepared previous-day read-only state is used: no global history
    # loop, DB rows or current results are replayed for another race that day.
    extra = _extra_features(current, pd.DataFrame(columns=canonical.SOURCE_COLUMNS), _prepared=prepared["context"])
    result = pd.concat([baseline, extra], axis=1).loc[:, list(FEATURE_COLUMNS)]
    result.attrs.update(feature_version=FEATURE_PIPELINE, source_hashes=feature_source_hashes(),
        context_audit=extra.attrs["context_audit"], context_caveats=_caveats(),
        current_outcomes_consumed=False, same_day_or_future_history_used=False,
        complete_career_coverage_verified=False)
    if _source_revision(db_path) != revision:
        raise ValueError("context-source-changed-during-prediction-retry")
    audit = {"schema": "prediction-input-contract-v1", "scope": FEATURE_PIPELINE,
        "feature_version": FEATURE_PIPELINE, "status": "unverified",
        "issue_codes": ["card_capture_provenance_unverified"], "semantic_parity_verified": True,
        "source_hashes": feature_source_hashes(), "source_hash_matches": None,
        "live_capture_hash_bound": False, "card_capture_time_verified": False,
        "current_outcomes_consumed": False, "expected_count": len(FEATURE_COLUMNS),
        "actual_count": len(result.columns), "missing_columns": [], "restored_columns": [], "blocked_columns": [],
        "all_missing_columns": [name for name in result if result[name].isna().all()],
        "missing_value_rate": float(result.isna().to_numpy().mean()), "uncomputed_columns": [],
        "no_history_rows": int(result.safe_prior_starts.eq(0).sum()),
        "missing_horse_id_rows": int(current.horse_id.eq("").sum()),
        "same_day_or_future_history_used": False,
        "history_max_source_date": prepared["history_max_source_date"],
        "history_rows": prepared["history_rows"], "history_audit": deepcopy(prepared["coverage"]),
        "history_state_cache_hit": cache_hit, "history_state_cache_max_days": _MAX_SERVING_DAYS,
        "history_state_data_revision": revision["data_revision"],
        "history_state_revision_kind": revision["revision_kind"],
        "history_state_source_tracking": revision["source_tracking"],
        "context_audit": result.attrs["context_audit"], "context_caveats": _caveats(),
        "coverage_semantics": "observed eligible global population; not complete career proof",
        "calibration_verified": False, "profitability_verified": False}
    return result, audit

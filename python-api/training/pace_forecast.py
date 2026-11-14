"""Prior-date supervised forecasts; unknown evidence is never a pace label."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import lightgbm as lgb
import numpy as np
import pandas as pd

from training import db_context_features as context
from training import db_shared_fit as shared

SCHEMA = "chronological-pace-position-heads-v1"
BASE_COLUMNS = tuple(context.FEATURE_COLUMNS)
FORECAST_COLUMNS = (
    "pace_slow_probability", "pace_middle_probability", "pace_fast_probability", "pace_model_available",
    "position_front_probability", "position_middle_probability", "position_back_probability",
    "position_model_available", "position_expected_order", "position_entropy",
)
MINIMUM_RACES = 200
MINIMUM_DATES = 20
_IMPORTED_SELF = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def source_sha256():
    current = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if current != _IMPORTED_SELF:
        raise ValueError("pace-source-changed-restart-required")
    return current


def source_hashes():
    return {**context.feature_source_hashes(), **shared.source_hashes(),
            "training.pace_forecast": source_sha256()}


def _check(target, features):
    if (list(features) != list(BASE_COLUMNS) or not features.index.equals(target.index)
            or target.empty or target.columns.duplicated().any()
            or not {"race_id", "race_date", "horse_number", "num_horses"}.issubset(target)):
        raise ValueError("pace-fixed-context-aligned-input-required")
    dates = target.race_date.astype(str)
    horses = pd.to_numeric(target.horse_number, errors="coerce")
    sizes = pd.to_numeric(target.num_horses, errors="coerce")
    if (not dates.str.fullmatch(r"\d{8}").all()
            or pd.to_datetime(dates, format="%Y%m%d", errors="coerce").isna().any()
            or not np.isfinite(horses).all() or not np.isfinite(sizes).all()
            or not horses.eq(np.floor(horses)).all() or not sizes.eq(np.floor(sizes)).all()
            or not sizes.ge(2).all() or not horses.between(1, 18).all()
            or target.duplicated(["race_id", "horse_number"]).any()
            or target.groupby("race_id").race_date.nunique().ne(1).any()
            or not sizes.eq(target.groupby("race_id").race_id.transform("size")).all()):
        raise ValueError("pace-whole-date-complete-field-required")


def _fit(target, features, labels, semantics):
    valid = labels.notna()
    evidence = target.loc[valid]
    counts = labels.loc[valid].value_counts()
    result = {"status": "insufficient_labels", "label_semantics": semantics,
              "race_count": int(evidence.race_id.nunique()), "date_count": int(evidence.race_date.nunique()),
              "runner_count": int(valid.sum()), "class_counts": {str(k): int(v) for k, v in counts.items()},
              "model": None, "encoder": None}
    if (result["race_count"] < MINIMUM_RACES or result["date_count"] < MINIMUM_DATES
            or set(counts.index) != {0., 1., 2.} or counts.min() < 20):
        return result
    encoder = shared.FoldEncoder(list(BASE_COLUMNS), list(context.CATEGORICAL_COLUMNS))
    try:
        encoder.fit_transform(features.loc[valid])
    except ValueError as error:
        if str(error) != "fold has no variable, observed training features":
            raise
        return {**result, "status": "insufficient_features"}
    encoder.feature_columns = list(BASE_COLUMNS)
    encoded = encoder.transform(features.loc[valid])
    dataset = lgb.Dataset(encoded, label=labels.loc[valid].astype(int).to_numpy(),
                          categorical_feature=list(context.CATEGORICAL_COLUMNS), free_raw_data=True)
    model = lgb.train({"objective": "multiclass", "num_class": 3, "metric": "multi_logloss",
                       "learning_rate": .05, "num_leaves": 15, "min_data_in_leaf": 30,
                       "lambda_l2": 1., "max_bin": 63, "seed": 42, "num_threads": 2,
                       "deterministic": True, "force_col_wise": True, "verbosity": -1},
                      dataset, num_boost_round=80)
    return {**result, "status": "trained", "encoder": encoder, "model": model}


def _position_labels(target):
    labels = []
    for recorded, size in zip(target.get("_ctx_history_corners", pd.Series(None, index=target.index, dtype=object)),
                              target.num_horses):
        corners = context._corners(recorded, size)
        relative = (corners[0] - 1) / (float(size) - 1) if corners is not None else np.nan
        labels.append(0. if relative < 1 / 3 else 1. if relative < 2 / 3 else 2. if np.isfinite(relative) else np.nan)
    return pd.Series(labels, index=target.index)


def _condition_key(condition):
    return json.dumps([None if pd.isna(value) else str(value) for value in condition], ensure_ascii=False)


def _pace_labels(target, supplied, *, thresholds=None):
    first = target.drop_duplicates("race_id")
    empty = pd.Series(np.nan, index=target.index)
    if supplied is None or supplied.empty:
        return empty, {}
    required = {"race_id", "first600_seconds", "validated", "source_sha256"}
    if (not required.issubset(supplied) or supplied.race_id.duplicated().any()
            or not set(supplied.race_id).issubset(set(target.race_id))):
        raise ValueError("pace-lap-label-identity-required")
    trusted = supplied.loc[supplied.validated.map(lambda v: isinstance(v, (bool, np.bool_)) and bool(v))].copy()
    if trusted.empty:
        return empty, {}
    if not trusted.source_sha256.map(lambda v: isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) is not None).all():
        raise ValueError("pace-lap-label-source-required")
    seconds = pd.to_numeric(trusted.first600_seconds, errors="coerce")
    if not (np.isfinite(seconds).all() and seconds.between(18., 60.).all()):
        raise ValueError("pace-lap-label-time-invalid")
    by_race = seconds.set_axis(trusted.race_id)
    observed = first.loc[first.race_id.isin(by_race.index)].copy()
    observed["seconds"] = observed.race_id.map(by_race)
    if pd.to_numeric(observed.distance).lt(600).any():
        raise ValueError("pace-lap-label-distance-invalid")
    fitted = thresholds is None
    thresholds = {} if fitted else dict(thresholds)
    global_bounds = (observed.seconds.quantile([1 / 3, 2 / 3]).to_numpy() if fitted
                     else np.asarray(thresholds["__global__"], dtype=float))
    thresholds["__global__"] = [float(v) for v in global_bounds]
    labels = {}
    columns = ["surface", "distance", "venue", "field_condition"]
    for condition, group in observed.groupby(columns, dropna=False, sort=False):
        key = _condition_key(condition)
        bounds = ((group.seconds.quantile([1 / 3, 2 / 3]).to_numpy() if len(group) >= 30 else global_bounds)
                  if fitted else np.asarray(thresholds.get(key, global_bounds), dtype=float))
        if fitted:
            thresholds[key] = [float(v) for v in bounds]
        for race, value in zip(group.race_id, group.seconds):
            labels[race] = 2. if value < bounds[0] else 0. if value > bounds[1] else 1.
    return target.race_id.map(labels).astype(float), thresholds


def fit_heads(target: pd.DataFrame, features: pd.DataFrame, *, pace_labels=None) -> dict:
    _check(target, features)
    pace, thresholds = _pace_labels(target, pace_labels)
    # A race has one pace observation; never replicate its label per horse.
    race_rows = ~target.race_id.duplicated()
    race_features = _race_features(target, features)
    pace_head = _fit(target.loc[race_rows], race_features.loc[race_rows], pace.loc[race_rows],
                     "condition_relative_first600_terciles")
    pace_head["first600_model"] = None
    if pace_head["status"] == "trained":
        selected = race_rows & pace.notna()
        seconds = pd.to_numeric(pace_labels.set_index("race_id").first600_seconds, errors="coerce")
        encoded = pace_head["encoder"].transform(race_features.loc[selected])
        dataset = lgb.Dataset(encoded, label=target.loc[selected, "race_id"].map(seconds).to_numpy(),
                              categorical_feature=list(context.CATEGORICAL_COLUMNS), free_raw_data=True)
        pace_head["first600_model"] = lgb.train(
            {"objective": "regression", "metric": "l2", "learning_rate": .05, "num_leaves": 15,
             "min_data_in_leaf": 30, "lambda_l2": 1., "max_bin": 63, "seed": 42, "num_threads": 2,
             "deterministic": True, "force_col_wise": True, "verbosity": -1}, dataset, num_boost_round=80)
    return {"schema": SCHEMA, "source_sha256": source_sha256(),
            "source_hashes": source_hashes(),
            "training_date_to": str(target.race_date.max()), "base_columns": list(BASE_COLUMNS),
            "pace": {**pace_head, "thresholds": thresholds, "probability_calibrated": False},
            "position": _fit(target, features, _position_labels(target),
                             "first_recorded_passage_not_first_corner")}


def _race_features(target, features):
    frame = features.copy()
    numeric = [name for name in BASE_COLUMNS if name not in context.CATEGORICAL_COLUMNS]
    frame[numeric] = frame[numeric].groupby(target.race_id, sort=False).transform("mean")
    for name in context.CATEGORICAL_COLUMNS:
        values = frame[name].astype("string").fillna("__missing__")
        count = values.groupby(target.race_id, sort=False).transform("nunique")
        frame[name] = values.where(count.eq(1), "__mixed__")
    return frame


def predict_heads(heads: dict, target: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    _check(target, features)
    if (heads.get("schema") != SCHEMA or heads.get("source_sha256") != source_sha256()
            or heads.get("source_hashes") != source_hashes()
            or heads.get("base_columns") != list(BASE_COLUMNS)):
        raise ValueError("pace-head-source-or-schema-mismatch")
    if str(heads["training_date_to"]) >= str(target.race_date.min()):
        raise ValueError("pace-head-training-must-be-strictly-before-target")
    result = pd.DataFrame(np.nan, index=target.index, columns=FORECAST_COLUMNS)
    for name in ("pace", "position"):
        head = heads[name]
        result[name + "_model_available"] = float(head["status"] == "trained")
        if head["status"] != "trained":
            continue
        x = _race_features(target, features) if name == "pace" else features
        probabilities = np.asarray(head["model"].predict(head["encoder"].transform(x), num_threads=2))
        if (probabilities.shape != (len(target), 3) or not np.isfinite(probabilities).all()
                or (probabilities < 0).any() or not np.allclose(probabilities.sum(axis=1), 1., atol=1e-6)):
            raise ValueError("pace-head-probability-invalid")
        columns = FORECAST_COLUMNS[:3] if name == "pace" else FORECAST_COLUMNS[4:7]
        result[list(columns)] = probabilities
        if name == "position":
            expected = pd.Series(probabilities @ np.array([1 / 6, .5, 5 / 6]), index=target.index)
            group = expected.groupby(target.race_id, sort=False)
            result["position_expected_order"] = (group.rank(method="average") - 1) / (group.transform("size") - 1)
            result["position_entropy"] = -np.sum(probabilities * np.log(np.maximum(probabilities, 1e-15)), axis=1)
    return result.astype("float32")


def forward_features(target: pd.DataFrame, features: pd.DataFrame, *, pace_labels=None):
    _check(target, features)
    output = pd.DataFrame(np.nan, index=target.index, columns=FORECAST_COLUMNS)
    output[["pace_model_available", "position_model_available"]] = 0.
    audit = []
    for year in sorted(target.race_date.astype(str).str[:4].unique()):
        predicted = target.race_date.astype(str).str.startswith(year)
        first = str(target.loc[predicted, "race_date"].min())
        previous = target.race_date.lt(first)
        record = {"prediction_date_from": first, "training_date_to": None}
        if previous.any():
            labels = (pace_labels.loc[pace_labels.race_id.isin(target.loc[previous, "race_id"])]
                      if pace_labels is not None else None)
            heads = fit_heads(target.loc[previous], features.loc[previous], pace_labels=labels)
            output.loc[predicted] = predict_heads(heads, target.loc[predicted], features.loc[predicted])
            record.update(training_date_to=heads["training_date_to"],
                          pace_status=heads["pace"]["status"], position_status=heads["position"]["status"])
        audit.append(record)
    return output.astype("float32"), audit


def evaluate_heads(heads, target, features, *, pace_labels=None):
    """Only score saved observations; thresholds and models remain TRAIN-only."""
    predictions = predict_heads(heads, target, features)
    labels = {"position": _position_labels(target)}
    labels["pace"], _ = (_pace_labels(target, pace_labels, thresholds=heads["pace"]["thresholds"])
                         if heads["pace"]["status"] == "trained" else (pd.Series(np.nan, index=target.index), {}))
    result = {}
    for name in ("pace", "position"):
        eligible = labels[name].notna()
        if name == "pace":
            eligible &= ~target.race_id.duplicated()
        record = {"status": heads[name]["status"], "race_count": int(target.loc[eligible].race_id.nunique()),
                  "runner_count": int(eligible.sum()), "probability_calibrated": False}
        if heads[name]["status"] == "trained" and eligible.any():
            columns = FORECAST_COLUMNS[:3] if name == "pace" else FORECAST_COLUMNS[4:7]
            p = predictions.loc[eligible, list(columns)].to_numpy()
            observed = labels[name].loc[eligible].astype(int).to_numpy()
            truth = np.eye(3)[observed]
            record.update(nll=float(-np.log(np.maximum(p[np.arange(len(p)), observed], 1e-15)).mean()),
                          brier=float(np.sum((p - truth) ** 2, axis=1).mean()),
                          accuracy=float((p.argmax(axis=1) == observed).mean()),
                          observed_class_counts={str(k): int(v) for k, v in pd.Series(observed).value_counts().items()})
            if name == "pace":
                x = heads[name]["encoder"].transform(_race_features(target, features).loc[eligible])
                seconds = np.asarray(heads[name]["first600_model"].predict(x, num_threads=2))
                actual = target.loc[eligible, "race_id"].map(pace_labels.set_index("race_id").first600_seconds).to_numpy(float)
                record.update(first600_mae_seconds=float(np.mean(np.abs(seconds - actual))),
                              first600_rmse_seconds=float(np.sqrt(np.mean((seconds - actual) ** 2))))
            else:
                relative = [(context._corners(row["_ctx_history_corners"], row["num_horses"])[0] - 1)
                            / (row["num_horses"] - 1) for _, row in target.loc[eligible].iterrows()]
                record["projected_position_mae"] = float(np.mean(np.abs(
                    predictions.loc[eligible, "position_expected_order"].to_numpy() - relative)))
        result[name] = record
    return result

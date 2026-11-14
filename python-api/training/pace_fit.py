"""Isolated supervised development stacking; legacy contracts remain unchanged."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from types import FunctionType

import numpy as np
import pandas as pd

from training import db_context_fit as base
from training import pace_forecast as forecast

FEATURE_PIPELINE = "local-pace-stack-v1"
SCHEMA = "local-pace-stack-model-v1"
FEATURE_COLUMNS = forecast.BASE_COLUMNS + forecast.FORECAST_COLUMNS
_IMPORTED_SELF = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def source_hashes():
    own = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if own != _IMPORTED_SELF:
        raise ValueError("pace-fit-source-changed-restart-required")
    return {**base.source_hashes(), **forecast.source_hashes(), "training.pace_fit": own}


def feature_source_hashes():
    return {**base.feature_source_hashes(), "training.pace_forecast": forecast.source_sha256(),
            "training.pace_fit": source_hashes()["training.pace_fit"]}


def _bound(function, replacements):
    namespace = {**function.__globals__, **replacements}
    copied = FunctionType(function.__code__, namespace, function.__name__, function.__defaults__, function.__closure__)
    copied.__kwdefaults__ = function.__kwdefaults__
    return copied


def _labels_for(labels, target):
    return labels.loc[labels.race_id.isin(target.race_id)].copy() if labels is not None else None


def fit_on_partitions(target, features, splits, *, pace_labels=None, progress=None):
    original = (base.legacy._frame_digest(target), base.legacy._frame_digest(features))
    target, features = base.prepare(target, features)
    if original != (base.legacy._frame_digest(target), base.legacy._frame_digest(features)):
        raise ValueError("pace-partitions-require-canonical-position-order")
    _validate_partitions(target, splits)
    train_index = np.asarray(splits["TRAIN"])
    train = target.iloc[train_index]
    train_x = features.iloc[train_index]
    dates = np.sort(train.race_date.unique())
    boundary = max(2, min(len(dates) - 1, int(math.floor(.8 * len(dates)))))
    inner = train.race_date.isin(dates[:boundary])
    inner_target, inner_x = train.loc[inner], train_x.loc[inner]
    train_forecast = pd.DataFrame(np.nan, index=train.index, columns=forecast.FORECAST_COLUMNS)
    initial, audit = forecast.forward_features(inner_target, inner_x, pace_labels=_labels_for(pace_labels, inner_target))
    train_forecast.loc[inner] = initial
    inner_heads = forecast.fit_heads(inner_target, inner_x, pace_labels=_labels_for(pace_labels, inner_target))
    train_forecast.loc[~inner] = forecast.predict_heads(inner_heads, train.loc[~inner], train_x.loc[~inner])
    heads = forecast.fit_heads(train, train_x, pace_labels=_labels_for(pace_labels, train))
    stacked = features.copy()
    for name in forecast.FORECAST_COLUMNS:
        stacked[name] = np.nan
    stacked.loc[train_index, list(forecast.FORECAST_COLUMNS)] = train_forecast
    for name in ("CAL", "TEST"):
        positions = np.asarray(splits[name])
        future = forecast.predict_heads(heads, target.iloc[positions], features.iloc[positions])
        stacked.loc[positions, list(forecast.FORECAST_COLUMNS)] = future
    changes = {"FEATURE_COLUMNS": FEATURE_COLUMNS, "FEATURE_PIPELINE": FEATURE_PIPELINE, "SCHEMA": SCHEMA,
               "source_hashes": source_hashes, "feature_source_hashes": feature_source_hashes, "__file__": __file__}
    changes["prepare"] = _bound(base.prepare, changes)
    fitter = _bound(base.fit_on_partitions, changes)
    bundle, result, predictions = fitter(target, stacked, splits, n_threads=2, preset_parallel=False, progress=progress)
    blockers = []
    if heads["pace"]["status"] != "trained":
        blockers.append("pace_labels_insufficient")
    if heads["position"]["status"] != "trained":
        blockers.append("position_labels_insufficient")
    # Matched controls and serving validation must be checked by publication.
    blockers.extend(("matched_comparison_required", "serving_parity_required", "publication_required"))
    bundle.update(heads=heads, production_eligible=False, activation_blockers=blockers)
    result.update(schema="pace-stack-fit-result-v1", activation_blockers=blockers,
                  forecast_audit={"training_forward_folds": audit,
                                  "inner_validation_heads_frozen_at": inner_heads["training_date_to"],
                                  "cal_test_heads_frozen_at": heads["training_date_to"],
                                  "cal_or_test_outcomes_used_to_fit_heads": False},
                  forecast_coverage={name: {column: float(stacked.iloc[positions][column].mean())
                                           for column in ("pace_model_available", "position_model_available")}
                                     for name, positions in splits.items()},
                  head_evidence={name: {k: v for k, v in heads[name].items() if k not in ("model", "encoder", "first600_model")}
                                 for name in ("pace", "position")},
                  head_evaluation={name: forecast.evaluate_heads(
                      heads, target.iloc[positions], features.iloc[positions], pace_labels=_labels_for(pace_labels, target.iloc[positions]))
                                   for name, positions in splits.items() if name in ("CAL", "TEST")})
    return bundle, result, predictions


def _validate_partitions(target, splits):
    if not isinstance(splits, dict) or set(splits) != {"TRAIN", "CAL", "TEST"}:
        raise ValueError("pace-three-explicit-partitions-required")
    occupied = set()
    counts = target.groupby("race_date").size()
    for positions in splits.values():
        index = np.asarray(positions)
        if (index.ndim != 1 or index.dtype.kind not in "iu" or not len(index)
                or len(np.unique(index)) != len(index) or index.min() < 0 or index.max() >= len(target)
                or occupied.intersection(index.tolist())):
            raise ValueError("pace-partition-position-invalid-or-overlapping")
        occupied.update(index.tolist())
        part_counts = target.iloc[index].groupby("race_date").size()
        if not part_counts.eq(counts.reindex(part_counts.index)).all():
            raise ValueError("pace-partition-whole-dates-required")
    parts = {name: target.iloc[index] for name, index in splits.items()}
    if not (parts["TRAIN"].race_date.max() < parts["CAL"].race_date.min()
            and parts["CAL"].race_date.max() < parts["TEST"].race_date.min()):
        raise ValueError("pace-chronological-partition-overlap")
    if parts["TRAIN"].race_date.nunique() < 4:
        raise ValueError("pace-insufficient-training-dates")


def predict_scores(bundle, target, features):
    """Research prediction also requires the identical frozen serving contract."""
    changes = {"FEATURE_COLUMNS": FEATURE_COLUMNS, "FEATURE_PIPELINE": FEATURE_PIPELINE, "SCHEMA": SCHEMA,
               "source_hashes": source_hashes, "feature_source_hashes": feature_source_hashes, "__file__": __file__}
    validate = _bound(base._validate_prediction_bundle, changes)
    validate(bundle)
    if bundle.get("heads", {}).get("training_date_to") != bundle.get("training_date_to"):
        raise ValueError("pace-stack-head-training-cutoff-mismatch")
    forecasts = forecast.predict_heads(bundle["heads"], target, features)
    stacked = pd.concat([features, forecasts], axis=1)
    encoded = bundle["encoder"].transform(stacked)
    score = base.legacy._finite_predictions(bundle["model"], encoded, 2)
    return score, forecasts

"""Isolated race-NLL research; historical outcomes are teachers, never inputs."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import time
from types import FunctionType, SimpleNamespace

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

from training import db_context_features as context
from training import db_shared_fit as shared
from training import pace_forecast as legacy

SCHEMA = "pace-research-v2"
FIELD_COLUMNS = ("field_front_candidates", "field_front_max", "field_front_std",
                 "field_front_gap", "field_inner_front_share", "field_style_missing")
EXTRA_FORECAST = ("first600_prediction",)
INTERACTIONS = ("fast_front_interaction", "slow_back_interaction",
                "position_style_residual", "pace_distance_interaction")
ALLOWED_COLUMNS = set(context.FEATURE_COLUMNS + legacy.FORECAST_COLUMNS
                      + FIELD_COLUMNS + EXTRA_FORECAST + INTERACTIONS)
_OWN_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def source_hashes():
    current = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if current != _OWN_HASH:
        raise ValueError("research-source-changed-restart-required")
    return {**legacy.source_hashes(), "training.pace_research": current}


@dataclass
class FitBudget:
    maximum: int
    deadline: float
    clock: object = time.time
    path: Path | None = None
    records: list = field(default_factory=list)

    def __post_init__(self):
        if self.path is not None and self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if saved["maximum"] != self.maximum or saved["deadline"] != self.deadline:
                raise ValueError("fit-budget-contract-changed")
            self.records = saved["records"]

    def reserve(self, name):
        if self.clock() >= self.deadline or len(self.records) >= self.maximum:
            raise TimeoutError("research-fit-or-time-budget-exhausted")
        self.records.append({"number": len(self.records) + 1, "name": name, "started_at": self.clock()})
        if self.path is not None:
            from experiments.common import write_json
            write_json(self.path, {"maximum": self.maximum, "deadline": self.deadline, "records": self.records})

    def train(self, *args, **kwargs):
        self.reserve("lightgbm")
        return lgb.train(*args, **kwargs)


def hierarchical_labels(target, supplied, *, thresholds=None):
    # Reuse the strict provenance/identity validation, not its global fallback.
    legacy._pace_labels(target, supplied)
    if supplied is None or supplied.empty:
        return pd.Series(np.nan, index=target.index), {}
    trusted = supplied.loc[supplied.validated.eq(True)].set_index("race_id")
    first = target.drop_duplicates("race_id").copy()
    observed = first.loc[first.race_id.isin(trusted.index)].copy()
    observed["seconds"] = pd.to_numeric(observed.race_id.map(trusted.first600_seconds))
    coarse = ["surface", "distance"]
    fine = coarse + ["venue", "field_condition"]
    if thresholds is None:
        parents, centers, supports = {}, {}, {}
        for key, group in observed.groupby(coarse, dropna=False):
            encoded = legacy._condition_key(key)
            if len(group) >= 20:
                parents[encoded] = float(group.seconds.median())
        for key, group in observed.groupby(fine, dropna=False):
            encoded = legacy._condition_key(key)
            parent = parents.get(legacy._condition_key(key[:2]))
            if parent is not None:
                weight = len(group) / (len(group) + 20.)
                centers[encoded] = float(parent + weight * (group.seconds.median() - parent))
                supports[encoded] = int(len(group))
        residuals = []
        for row in observed.itertuples():
            key = legacy._condition_key([getattr(row, column) for column in fine])
            if key in centers:
                residuals.append(row.seconds - centers[key])
        bounds = np.quantile(residuals, [1 / 3, 2 / 3]).tolist() if residuals else None
        thresholds = {"schema": "hierarchical-condition-residual-v2", "parents": parents,
                      "centers": centers, "supports": supports, "residual_bounds": bounds,
                      "global_absolute_seconds_fallback": False}
    if thresholds.get("schema") != "hierarchical-condition-residual-v2":
        raise ValueError("hierarchical-threshold-schema-required")
    labels = {}
    bounds = thresholds["residual_bounds"]
    if bounds is not None:
        for row in observed.itertuples():
            coarse_key = legacy._condition_key([getattr(row, column) for column in coarse])
            fine_key = legacy._condition_key([getattr(row, column) for column in fine])
            center = thresholds["centers"].get(fine_key, thresholds["parents"].get(coarse_key))
            if center is not None:
                residual = row.seconds - center
                labels[row.race_id] = 2. if residual < bounds[0] else 0. if residual > bounds[1] else 1.
    return target.race_id.map(labels).astype(float), thresholds


def field_features(target, features):
    if not target.index.equals(features.index):
        raise ValueError("field-feature-alignment-required")
    front = pd.to_numeric(features.ctx_front_rate5, errors="coerce")
    known = front.notna() & pd.to_numeric(features.ctx_style_count5, errors="coerce").gt(0)
    # Explicit nonmissing rates are useful in synthetic and historical records;
    # style-count zero still remains a separate missing-evidence signal.
    grouped = front.groupby(target.race_id, sort=False)
    result = pd.DataFrame(index=features.index)
    result["field_front_candidates"] = front.ge(.5).where(front.notna()).groupby(target.race_id).transform("sum")
    result.loc[grouped.transform("count").eq(0), "field_front_candidates"] = np.nan
    result["field_front_max"] = grouped.transform("max")
    result["field_front_std"] = grouped.transform("std")
    ordered = pd.DataFrame({"race": target.race_id, "front": front}).sort_values(
        ["race", "front"], ascending=[True, False], kind="stable")
    gaps = ordered.groupby("race").front.apply(
        lambda values: float(values.iloc[0] - values.iloc[1]) if values.notna().sum() >= 2 else np.nan)
    result["field_front_gap"] = target.race_id.map(gaps)
    gate = pd.to_numeric(features.ctx_gate_fraction, errors="coerce")
    inside = front.where(gate.le(1 / 3), 0.).groupby(target.race_id).transform("sum")
    total = grouped.transform("sum").where(grouped.transform("count").gt(0))
    result["field_inner_front_share"] = inside / total.where(total.gt(0))
    result["field_style_missing"] = 1. - known.astype(float).groupby(target.race_id).transform("mean")
    return result.loc[:, FIELD_COLUMNS].astype("float32")


def head_inputs(target, features, mode):
    if mode == "legacy":
        return features.loc[:, context.FEATURE_COLUMNS]
    enriched = features.copy()
    if mode == "hier_field":
        enriched = pd.concat([enriched, field_features(target, features)], axis=1)
    if mode == "condition":
        conditions = {"safe_distance", "safe_surface", "safe_venue", "safe_field_condition",
                      "safe_race_class", "safe_num_horses", "safe_weather", "safe_month"}
        enriched = enriched.copy()
        for name in enriched:
            if name not in conditions:
                enriched[name] = np.nan
    return enriched


def head_api(mode, budget=None):
    if mode not in {"legacy", "hier", "hier_field", "condition"}:
        raise ValueError("unknown-head-mode")
    columns = context.FEATURE_COLUMNS + (FIELD_COLUMNS if mode == "hier_field" else ())
    namespace = dict(legacy.__dict__)
    namespace.update(BASE_COLUMNS=columns, SCHEMA=SCHEMA + "-" + mode,
                     source_hashes=source_hashes, source_sha256=lambda: _OWN_HASH)
    for name, value in legacy.__dict__.items():
        if isinstance(value, FunctionType) and value.__module__ == legacy.__name__:
            namespace[name] = FunctionType(value.__code__, namespace, name,
                                          value.__defaults__, value.__closure__)
            namespace[name].__kwdefaults__ = value.__kwdefaults__
    namespace.update(source_hashes=source_hashes, source_sha256=lambda: _OWN_HASH)
    if mode != "legacy":
        namespace["_pace_labels"] = hierarchical_labels
    if budget is not None:
        namespace["lgb"] = SimpleNamespace(Dataset=lgb.Dataset, train=budget.train)
    return SimpleNamespace(**namespace)


def predict_head_features(heads, target, features, mode):
    api = head_api(mode)
    inputs = head_inputs(target, features, mode)
    forecast = api.predict_heads(heads, target, inputs)
    forecast["first600_prediction"] = np.nan
    if heads["pace"]["status"] == "trained":
        encoded = heads["pace"]["encoder"].transform(api._race_features(target, inputs))
        forecast["first600_prediction"] = heads["pace"]["first600_model"].predict(encoded, num_threads=2)
        if mode != "legacy":
            parents = heads["pace"]["thresholds"]["parents"]
            supported = pd.Series(
                [legacy._condition_key(values) in parents for values in
                 target[["surface", "distance"]].itertuples(index=False, name=None)], index=target.index)
            forecast.loc[~supported, list(legacy.FORECAST_COLUMNS[:3]) + ["first600_prediction"]] = np.nan
            forecast.loc[~supported, "pace_model_available"] = 0.
    return forecast


def assemble(features, forecast, *, groups, head="none", fields=None, interactions=False, time_feature=False):
    columns = list(dict.fromkeys(name for group in groups for name in context.FEATURE_GROUPS[group]))
    result = features.loc[:, columns].copy()
    if head in {"pace", "both"}:
        pace_columns = list(legacy.FORECAST_COLUMNS[:4]) + (["first600_prediction"] if time_feature else [])
        result[pace_columns] = forecast[pace_columns]
    if head in {"position", "both"}:
        result[list(legacy.FORECAST_COLUMNS[4:])] = forecast[list(legacy.FORECAST_COLUMNS[4:])]
    if fields is not None:
        result = pd.concat([result, fields], axis=1)
    if interactions:
        if head != "both":
            raise ValueError("interactions-require-both-heads")
        front = pd.to_numeric(features.ctx_front_rate5, errors="coerce")
        result["fast_front_interaction"] = forecast.pace_fast_probability * front
        result["slow_back_interaction"] = forecast.pace_slow_probability * (1. - front)
        result["position_style_residual"] = forecast.position_front_probability - front
        result["pace_distance_interaction"] = forecast.first600_prediction * pd.to_numeric(
            features.ctx_distance_change_ratio, errors="coerce")
    return result


def race_probabilities(raw, race_ids, temperature=1.):
    values = np.asarray(raw, dtype=float) / float(temperature)
    codes, unique = pd.factorize(race_ids, sort=False)
    if (values.shape != codes.shape or not len(values) or not np.isfinite(values).all()
            or temperature <= 0 or np.any(codes < 0)):
        raise ValueError("research-invalid-softmax-input")
    maxima = np.full(len(unique), -np.inf)
    np.maximum.at(maxima, codes, values)
    exponential = np.exp(values - maxima[codes])
    total = np.bincount(codes, weights=exponential)
    return exponential / total[codes]


def race_objective(raw, dataset):
    sizes = np.asarray(dataset.get_group(), dtype=int)
    ids = np.repeat(np.arange(len(sizes)), sizes)
    probability = race_probabilities(raw, ids)
    return probability - dataset.get_label(), np.maximum(2. * probability * (1. - probability), 1e-8)


def calibrate(target, scores, budget=None):
    eligible = shared._single_winner_mask(target)
    cohort = target.loc[eligible]
    scores = np.asarray(scores)[eligible]
    if cohort.race_id.nunique() < 2:
        raise ValueError("research-too-few-calibration-races")
    winners = cohort.finish.eq(1).to_numpy()
    if budget is not None:
        budget.reserve("temperature-calibration")
    def loss(log_temperature):
        p = race_probabilities(scores, cohort.race_id, np.exp(log_temperature))
        return float(-np.log(np.maximum(p[winners], 1e-15)).mean())
    bounds = np.log([.02, 10.])
    optimum = minimize_scalar(loss, bounds=tuple(bounds), method="bounded",
                              options={"xatol": 1e-6, "maxiter": 100})
    if not optimum.success or not np.isfinite(optimum.fun):
        raise ValueError("research-calibration-failed")
    audit = {"temperature": float(np.exp(optimum.x)), "fit_uses_test": False,
             "date_from": str(cohort.race_date.min()), "date_to": str(cohort.race_date.max()),
             "eligible_races": int(cohort.race_id.nunique()),
             "boundary_reached": bool(min(optimum.x - bounds[0], bounds[1] - optimum.x) <= .01)}
    return audit


def race_records(target, probabilities):
    frame = target[["race_id", "race_date", "horse_number", "finish", "surface", "distance", "venue"]].copy()
    frame["probability"] = np.asarray(probabilities)
    sums = frame.probability.groupby(frame.race_id).sum()
    if (not np.isfinite(frame.probability).all() or not frame.probability.between(0, 1).all()
            or not np.allclose(sums, 1., atol=1e-10, rtol=0)):
        raise ValueError("research-probabilities-not-normalized")
    frame = frame.loc[shared._single_winner_mask(target)].copy()
    winner = frame.finish.eq(1)
    frame["squared_error"] = (frame.probability - winner.astype(float)) ** 2
    picked = frame.sort_values(["race_id", "probability", "horse_number"],
                              ascending=[True, False, True], kind="stable").drop_duplicates("race_id")
    grouped = frame.groupby("race_id", sort=True)
    records = grouped[["race_date", "surface", "distance", "venue"]].first()
    records["nll"] = -np.log(frame.loc[winner].set_index("race_id").probability.clip(lower=1e-15))
    records["brier"] = grouped.squared_error.sum()
    records["top1"] = picked.set_index("race_id").finish.eq(1).astype(float)
    records["top_probability"] = picked.set_index("race_id").probability
    records["num_horses"] = grouped.size()
    return records.reset_index()


def summary_metrics(records):
    confidence = records.top_probability.to_numpy()
    correct = records.top1.to_numpy()
    ece = 0.
    for index in range(10):
        selected = (confidence >= index / 10) & (confidence < (index + 1) / 10 if index < 9 else confidence <= 1.)
        if selected.any():
            ece += selected.mean() * abs(confidence[selected].mean() - correct[selected].mean())
    return {"races": len(records), "nll": float(records.nll.mean()), "brier": float(records.brier.mean()),
            "top1": float(records.top1.mean()), "ece": float(ece)}


def fit_final(train, train_x, cal, cal_x, valid, valid_x, *, objective="win", params=None, budget=None):
    if objective not in {"win", "speed"}:
        raise ValueError("research-objective-not-whitelisted")
    columns = list(train_x)
    if (not columns or len(set(columns)) != len(columns) or not set(columns).issubset(ALLOWED_COLUMNS)
            or list(cal_x) != columns or list(valid_x) != columns):
        raise ValueError("research-feature-whitelist-or-schema-mismatch")
    for target, x in ((train, train_x), (cal, cal_x), (valid, valid_x)):
        legacy._check(target, pd.DataFrame(index=target.index, columns=context.FEATURE_COLUMNS))
        if not target.index.equals(x.index):
            raise ValueError("research-feature-alignment-required")
    if not (str(train.race_date.max()) < str(cal.race_date.min())
            and str(cal.race_date.max()) < str(valid.race_date.min())):
        raise ValueError("research-train-cal-validation-date-overlap")
    options = {"num_boost_round": 200, "num_leaves": 31, "min_data_in_leaf": 30, "lambda_l2": 1.}
    if params:
        if not set(params).issubset(options):
            raise ValueError("research-parameter-not-whitelisted")
        options.update(params)
    rounds = options.pop("num_boost_round")
    fit_mask = shared._single_winner_mask(train) if objective == "win" else np.ones(len(train), dtype=bool)
    training = train.loc[fit_mask]
    categories = [name for name in context.CATEGORICAL_COLUMNS if name in columns]
    encoder = shared.FoldEncoder(columns, categories)
    encoder.fit_transform(train_x.loc[fit_mask])
    encoder.feature_columns = columns
    encoded = encoder.transform(train_x.loc[fit_mask])
    baseline = None
    if objective == "speed":
        if budget is not None:
            budget.reserve("speed-normalization")
        baseline = shared.fit_speed_deviation_baseline(training)
        labels = shared.apply_speed_deviation_baseline(training, baseline).to_numpy()
        group = None
    else:
        labels = training.finish.eq(1).to_numpy(dtype=float)
        group = training.groupby("race_id", sort=False).size().to_numpy()
        if training.race_id.ne(training.race_id.shift()).fillna(True).sum() != len(group):
            raise ValueError("research-training-races-must-be-contiguous")
    dataset = lgb.Dataset(encoded, label=labels, group=group, categorical_feature=categories)
    parameters = {"objective": race_objective if objective == "win" else "regression",
                  "metric": "None", "learning_rate": .05, "max_bin": 63, "seed": 42,
                  "num_threads": 2, "deterministic": True, "force_col_wise": True,
                  "verbosity": -1, **options}
    train_fn = budget.train if budget is not None else lgb.train
    model = train_fn(parameters, dataset, num_boost_round=rounds)
    cal_scores = np.asarray(model.predict(encoder.transform(cal_x), num_threads=2))
    scores = np.asarray(model.predict(encoder.transform(valid_x), num_threads=2))
    calibration = calibrate(cal, cal_scores, budget)
    probability = race_probabilities(scores, valid.race_id, calibration["temperature"])
    records = race_records(valid, probability)
    return {"model": model, "encoder": encoder, "baseline": baseline, "objective": objective,
            "feature_columns": columns, "calibration": calibration, "scores": scores,
            "cal_scores": cal_scores, "probabilities": probability,
            "metrics": summary_metrics(records), "race_records": records,
            "reference_only": True, "production_eligible": False}

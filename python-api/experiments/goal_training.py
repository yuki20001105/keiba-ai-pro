"""Fixed-parameter, development-only annual training for the isolated goal loop.

No tuning, promotion, live data, or financial-evidence approval occurs here.
Win scores are race-normalized sigmoid outputs, not separately calibrated win
probabilities. All preprocessing and diagnostic contributions use training rows.
"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import re
from typing import Any, Callable

import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from keiba_ai.speed_deviation import (
    apply_speed_deviation_baseline, fit_speed_deviation_baseline, raw_speed_mps,
)

from .common import file_sha256, read_json, write_json
from .dataset import EVALUATION_COLUMNS
from .evaluation import COHORT, evaluate_race_records, race_evaluation_records
from .training import FoldEncoder, _features, _inverse_speed, _params, _ranking_groups, _validate_frame


SCHEMA = "isolated-goal-trial-v1"
EVALUATION_LABEL = "development_known_exposed"
_TRIAL_FIELDS = {
    "id", "target", "validation_years", "train_start_year", "feature_columns", "params",
    "seed", "num_threads", "device", "phase", "bootstrap_samples",
    "experiment_contract_sha256", "proposal_id",
}
_INTEGER_BOUNDS = {
    "num_boost_round": (1, 2000), "num_leaves": (2, 1024),
    "min_data_in_leaf": (1, 1_000_000), "bagging_freq": (0, 1000),
    "max_bin": (2, 65535), "max_cat_threshold": (1, 1024),
}
_FLOAT_BOUNDS = {
    "learning_rate": (0.00001, 1), "min_sum_hessian_in_leaf": (0, 1_000_000),
    "feature_fraction": (0, 1), "bagging_fraction": (0, 1),
    "lambda_l1": (0, 1_000_000), "lambda_l2": (0, 1_000_000),
    "reg_alpha": (0, 1_000_000), "reg_lambda": (0, 1_000_000),
    "min_gain_to_split": (0, 1_000_000), "path_smooth": (0, 1_000_000),
    "cat_l2": (0, 1_000_000), "cat_smooth": (0, 1_000_000),
}


def _integer(value: Any, low: int, high: int, name: str) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")


def _trial_contract(trial: dict) -> tuple[dict, dict, int]:
    if not isinstance(trial, dict):
        raise ValueError("trial must be an object")
    required = {"id", "target", "validation_years", "train_start_year", "feature_columns"}
    if set(trial) - _TRIAL_FIELDS or required - set(trial):
        raise ValueError("unknown or missing trial fields")
    trial = deepcopy(trial)
    if not isinstance(trial["id"], str) or not trial["id"].strip():
        raise ValueError("id must be a nonempty string")
    if trial["target"] not in ("win", "speed_deviation", "rank"):
        raise ValueError("target must be win, speed_deviation, or rank")
    years = trial["validation_years"]
    if not isinstance(years, list) or not years:
        raise ValueError("validation_years must be nonempty")
    for year in years:
        _integer(year, 2017, 2100, "validation year")
    if years != sorted(set(years)):
        raise ValueError("validation_years must be sorted and unique")
    _integer(trial["train_start_year"], 2016, min(years) - 1, "train_start_year")
    columns = trial["feature_columns"]
    if not isinstance(columns, list) or not columns or not all(isinstance(c, str) for c in columns):
        raise ValueError("feature_columns must be a nonempty string list")
    trial.setdefault("phase", "development")
    trial.setdefault("seed", 42)
    trial.setdefault("num_threads", 1)
    trial.setdefault("device", "cpu")
    trial.setdefault("bootstrap_samples", 100)
    trial.setdefault("params", {})
    if trial["phase"] != "development" or trial["device"] != "cpu":
        raise ValueError("only development phase and CPU training are allowed")
    _integer(trial["seed"], 0, 2**31 - 1, "seed")
    _integer(trial["num_threads"], 1, 4, "num_threads")
    _integer(trial["bootstrap_samples"], 0, 1000, "bootstrap_samples")
    if "experiment_contract_sha256" in trial and (
        not isinstance(trial["experiment_contract_sha256"], str)
        or not re.fullmatch(r"[0-9a-fA-F]{64}", trial["experiment_contract_sha256"])
    ):
        raise ValueError("experiment_contract_sha256 must be a SHA256 digest")
    if "proposal_id" in trial and (not isinstance(trial["proposal_id"], str) or not trial["proposal_id"].strip()):
        raise ValueError("proposal_id must be a nonempty string")
    if not isinstance(trial["params"], dict):
        raise ValueError("params must be an object")
    # The shared allowlist rejects objective/device/thread/seed overrides first.
    params, rounds = _params(trial, ranking=trial["target"] == "rank")
    for key, value in trial["params"].items():
        if key in _INTEGER_BOUNDS:
            _integer(value, *_INTEGER_BOUNDS[key], key)
        elif key == "max_depth":
            if value != -1 or type(value) is not int:
                _integer(value, 1, 32, key)
        elif key == "extra_trees":
            if type(value) is not bool:
                raise ValueError("extra_trees must be boolean")
        elif key in _FLOAT_BOUNDS:
            low, high = _FLOAT_BOUNDS[key]
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not low <= value <= high
                    or (key in {"feature_fraction", "bagging_fraction"} and value == 0)):
                raise ValueError(f"{key} must be finite within ({low}, {high})")
        else:
            raise ValueError(f"parameter has no safe bounds: {key}")
    if trial["target"] == "win":
        params.update(objective="binary", metric="binary_logloss")
    return trial, params, rounds


def _probability_metrics(frame: pd.DataFrame) -> dict:
    label = pd.to_numeric(frame["finish"]).eq(1).to_numpy(dtype=int)
    probability = frame["win_probability"].to_numpy(dtype=float)
    bins = np.minimum((probability * 10).astype(int), 9)
    ece = sum(
        float(mask.mean()) * abs(float(probability[mask].mean() - label[mask].mean()))
        for index in range(10) if (mask := bins == index).any()
    )
    return {
        "auc": float(roc_auc_score(label, probability)) if len(np.unique(label)) == 2 else None,
        "log_loss": float(log_loss(label, probability, labels=[0, 1])),
        "brier": float(brier_score_loss(label, probability)), "ece": float(ece),
        "ece_bins": 10, "probability_source": "race_normalized_sigmoid",
        "calibration_status": "not_evaluated", "separate_calibration_applied": False,
    }


def _goal_evaluation(records: pd.DataFrame, **kwargs: Any) -> dict:
    evaluation = evaluate_race_records(records, **kwargs)
    evaluation["definitions"]["rank_correlation"] = (
        "macro mean of per-race Spearman(prediction, -finish); "
        "constant predictions contribute 0.0 and are not excluded"
    )
    return evaluation


def run_goal_trial(
    dataset_dir: Path, output_dir: Path, trial: dict,
    progress: Callable[[str, int], None] | None = None,
) -> dict:
    """Persist deterministic prior-year folds; callback exceptions interrupt work.

    ``result.json`` is the completion marker. Interrupted runs may retain partial
    isolated artifacts and must be restarted in a new, empty output directory.
    """
    import lightgbm as lgb

    dataset_dir, output_dir = Path(dataset_dir).resolve(), Path(output_dir).resolve()
    protected = Path(__file__).resolve().parents[1] / "models"
    if (output_dir == dataset_dir or output_dir.is_relative_to(dataset_dir)
            or dataset_dir.is_relative_to(output_dir)
            or output_dir == protected or output_dir.is_relative_to(protected)
            or protected.is_relative_to(output_dir)):
        raise ValueError("output must be separate from dataset and active models")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError("trial output directory must be empty")
    trial, params, rounds = _trial_contract(trial)
    notify = progress if progress is not None else lambda stage, pct: None
    manifest_path, dataset_path = dataset_dir / "dataset_manifest.json", dataset_dir / "dataset.parquet"
    manifest_hash, dataset_hash = file_sha256(manifest_path), file_sha256(dataset_path)
    manifest = read_json(manifest_path)
    if manifest.get("dataset_sha256") != dataset_hash:
        raise ValueError("dataset hash mismatch")
    columns, categorical = _features(manifest, {**trial, "candidate_id": "M03"})
    notify("validating isolated goal dataset", 0)
    available = set(pq.ParquetFile(dataset_path).schema_arrow.names)
    if not set(columns).issubset(available):
        raise ValueError("manifest feature columns not present in dataset")
    projected = list(dict.fromkeys(
        [c for c in EVALUATION_COLUMNS + ["expected_runner_count"] if c in available] + columns
    ))
    years = trial["validation_years"]
    start_date, end_date = f"{trial['train_start_year']}0101", f"{max(years)}1231"
    frame = _validate_frame(pd.read_parquet(
        dataset_path, columns=projected,
        filters=[("race_date", ">=", start_date), ("race_date", "<=", end_date)],
    ))
    for year in years:
        if (not frame["race_date"].lt(f"{year}0101").any()
                or not frame["race_date"].between(f"{year}0101", f"{year}1231").any()):
            raise ValueError(f"missing training or validation period for {year}")
    source_contract = {key: manifest.get(key) for key in (
        "snapshot_sha256", "eligibility_sha256", "dataset_schema_version", "feature_schema_version",
        "source_contract", "point_in_time_audit", "cohort_policy", "payout_scope", "payout_unit",
    )}
    source_contract["training_module_sha256"] = file_sha256(Path(__file__))
    source_contract["shared_training_module_sha256"] = file_sha256(Path(__file__).with_name("training.py"))
    source_contract["evaluation_module_sha256"] = file_sha256(Path(__file__).with_name("evaluation.py"))
    target = trial["target"]
    task = "speed_regression" if target == "speed_deviation" else "ranking"
    probability_transform = "race_normalized_sigmoid" if target == "win" else "none"
    evidence = {
        "evaluation_label": EVALUATION_LABEL, "calibration_status": "not_evaluated",
        "separate_calibration_applied": False, "promotion_approved": False,
        "phase3n_financial_evidence_approved": False, "observed_timestamp_assurance": False,
        "financial_evaluation_label": "fixed_top1_100_yen_retrospective_not_approved_Phase3N_financial_evidence",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    folds, predictions, records, contributions = [], [], [], []
    for fold_index, year in enumerate(years):
        train = frame.loc[frame["race_date"].lt(f"{year}0101")].reset_index(drop=True)
        valid = frame.loc[frame["race_date"].between(f"{year}0101", f"{year}1231")].reset_index(drop=True)
        notify(f"{year}: fit train-only target and encoder", 5 + int(75 * fold_index / len(years)))
        encoder = FoldEncoder(columns, categorical)
        x_train, baseline = encoder.fit_transform(train), None
        x_valid = encoder.transform(valid)
        fold_params = dict(params)
        if target == "speed_deviation":
            baseline = fit_speed_deviation_baseline(train)
            y_train = apply_speed_deviation_baseline(train, baseline)
        elif target == "rank":
            y_train = (train["expected_runner_count"] - pd.to_numeric(train["finish"]) + 1).astype(int)
            fold_params["label_gain"] = list(range(int(y_train.max()) + 1))
        else:
            y_train = pd.to_numeric(train["finish"]).eq(1).astype(int)
            if y_train.nunique() != 2:
                raise ValueError("win training labels require both classes")
        fit_categories = [c for c in categorical if c in encoder.feature_columns]
        train_data = lgb.Dataset(
            x_train, label=y_train, categorical_feature=fit_categories,
            group=_ranking_groups(train) if target == "rank" else None,
        )

        def training_progress(env: Any) -> None:
            if env.iteration % 20 == 0:
                notify(f"{year}: training round {env.iteration + 1}/{rounds}",
                       5 + int(75 * (fold_index + (env.iteration + 1) / rounds) / len(years)))

        model = lgb.train(fold_params, train_data, num_boost_round=rounds, callbacks=[training_progress])
        predicted = np.asarray(model.predict(x_valid, num_threads=trial["num_threads"]), dtype=float)
        if predicted.shape != (len(valid),) or not np.isfinite(predicted).all():
            raise ValueError("model did not produce finite predictions for every runner")
        evaluated = valid[[c for c in EVALUATION_COLUMNS + ["expected_runner_count"] if c in valid]].copy()
        evaluated["fold_year"] = year
        evaluated["evaluation_label"] = EVALUATION_LABEL
        evaluated["prediction"] = predicted
        if target == "win":
            if not ((predicted >= 0) & (predicted <= 1)).all():
                raise ValueError("binary sigmoid scores must be within [0, 1]")
            evaluated["sigmoid_score"] = predicted
            totals = evaluated.groupby("race_id")["sigmoid_score"].transform("sum")
            if not np.isfinite(totals).all() or totals.le(0).any():
                raise ValueError("cannot normalize nonpositive race scores")
            evaluated["win_probability"] = predicted / totals
            evaluated["prediction"] = evaluated["win_probability"]
        elif target == "speed_deviation":
            evaluated["actual"] = apply_speed_deviation_baseline(valid, baseline).to_numpy()
            evaluated["actual_speed_mps"] = raw_speed_mps(valid).to_numpy()
            evaluated["prediction_speed_mps"] = _inverse_speed(predicted, valid, baseline)
        race_records = race_evaluation_records(evaluated, task=task)
        constant_races = evaluated.groupby("race_id")["prediction"].nunique().eq(1)
        constant_mask = race_records["race_id"].isin(constant_races.index[constant_races])
        race_records.loc[constant_mask & race_records["rank_correlation"].isna(), "rank_correlation"] = 0.0
        race_records["evaluation_label"] = EVALUATION_LABEL
        evaluation = _goal_evaluation(race_records, seed=trial["seed"], bootstrap_samples=0)
        evaluation.update(evidence)
        if target == "win":
            evaluation["probability_metrics"] = _probability_metrics(evaluated)
        contract = {
            "schema": SCHEMA, "trial": trial, "target": target, "task": task, **evidence,
            "dataset_sha256": dataset_hash, "manifest_sha256": manifest_hash,
            "source_contract": source_contract, "train_period": [start_date, f"{year - 1}1231"],
            "evaluation_year": year, "train_rows": len(train),
            "train_observed_period": [str(train["race_date"].min()), str(train["race_date"].max())],
            "requested_features": columns, "feature_columns": encoder.feature_columns,
            "dropped_columns": encoder.dropped_columns, "categorical_features": fit_categories,
            "validation_used_for_fit_or_early_stopping": False, "market_features": False,
            "params": fold_params, "num_boost_round": rounds,
            "probability_transform": probability_transform,
        }
        sample = x_train.sample(n=min(128, len(x_train)), random_state=trial["seed"])
        values = np.asarray(model.predict(sample, pred_contrib=True, num_threads=trial["num_threads"]), dtype=float)
        if values.shape != (len(sample), len(encoder.feature_columns) + 1) or not np.isfinite(values).all():
            raise ValueError("nonfinite or malformed training contributions")
        importance = np.abs(values[:, :-1]).mean(axis=0)
        contributions.extend({
            "fold_year": year, "feature": column, "mean_abs_contribution": float(value),
            "sample_rows": len(sample), "sample_period": [str(train.loc[sample.index, "race_date"].min()),
                                                         str(train.loc[sample.index, "race_date"].max())],
        } for column, value in zip(encoder.feature_columns, importance))
        artifact = output_dir / f"fold_{year}.joblib"
        joblib.dump({
            "model": model, "encoder": encoder, "feature_columns": encoder.feature_columns,
            "baseline": baseline, "target": target, "probability_transform": probability_transform,
            "contract": contract,
        }, artifact)
        folds.append({
            "year": year, "contract": contract, "artifact": str(artifact), "sha256": file_sha256(artifact),
            "train_rows": len(train), "validation_rows": len(valid),
            "train_races": int(train["race_id"].nunique()), "validation_races": len(race_records),
            "evaluation": evaluation,
        })
        predictions.append(evaluated)
        records.append(race_records)
        notify(f"{year}: isolated fold saved", 5 + int(75 * (fold_index + 1) / len(years)))
    combined, combined_records = pd.concat(predictions, ignore_index=True), pd.concat(records, ignore_index=True)
    notify("aggregate development evaluation", 85)
    evaluation = _goal_evaluation(
        combined_records, seed=trial["seed"], bootstrap_samples=trial["bootstrap_samples"],
        progress=lambda replicate: notify(f"date-cluster bootstrap {replicate}", 90),
    )
    evaluation.update(evidence)
    if target == "win":
        evaluation["probability_metrics"] = {
            **_probability_metrics(combined),
            "by_year": {str(year): _probability_metrics(group) for year, group in combined.groupby("fold_year")},
        }
    paths = {
        "predictions": output_dir / "predictions.parquet",
        "race_records": output_dir / "race_records.parquet",
        "contributions": output_dir / "training_contributions.json",
    }
    combined.to_parquet(paths["predictions"], index=False)
    combined_records.to_parquet(paths["race_records"], index=False)
    write_json(paths["contributions"], {
        "diagnostic_only": True, "selection_evidence": False, "training_only": True,
        "max_rows_per_fold": 128, "rows": contributions,
    })
    result = {
        "schema": SCHEMA, "status": "completed", "trial_id": trial["id"],
        "phase": "development", "target": target, "task": task,
        "feature_columns": columns, "validation_years": years,
        "cohort": COHORT, "real_world_all_races": False, **evidence,
        "probability_transform": probability_transform,
        "dataset_sha256": dataset_hash, "manifest_sha256": manifest_hash,
        "source_contract": source_contract, "folds": folds, "evaluation": evaluation,
        "artifacts": {key: {"path": str(path), "sha256": file_sha256(path)} for key, path in paths.items()},
        "warnings": [
            "All evaluations are development_known_exposed; no holdout or promotion claim.",
            "No separate calibration applied; full win probability calibration is not evaluated.",
            "Ranking scores are not probabilities.",
            "Fixed top1 100-yen retrospective returns are not approved Phase3N financial evidence.",
            "No observed-timestamp assurance; popularity/odds are retrospective, not verified pre-cutoff.",
            "Complete runner cohorts only; place and other bet types are not evaluated.",
        ],
    }
    notify("isolated goal trial complete", 100)
    if file_sha256(dataset_path) != dataset_hash or file_sha256(manifest_path) != manifest_hash:
        raise ValueError("dataset or manifest changed during trial")
    write_json(output_dir / "result.json", result)
    return result

"""Configurable isolated search worker; never changes legacy trial semantics.

Search years 2021/22, known-reused confirmation years 2023/24/25 and the
known-reused retrospective year 2026 cannot be mixed in a trial. Only the search
band permits primary-only screening and intentionally unsaved model artifacts.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Callable

import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from keiba_ai.speed_deviation import apply_speed_deviation_baseline, fit_speed_deviation_baseline, raw_speed_mps

from .evaluation import COHORT, evaluate_race_records, race_evaluation_records
from .search_metrics import evaluate_primary_records, primary_race_records
from .training import FoldEncoder, _features, _hash, _inverse_speed, _params, _ranking_groups, _validate_frame


SCHEMA = "isolated-feature-search-trial-v1"
_BANDS = {"search": {2021, 2022}, "confirmation": {2023, 2024, 2025}, "retrospective": {2026}}
_LABELS = {"search": "search_forward_time", "confirmation": "known_reused_confirmation",
           "retrospective": "known_reused_retrospective"}
_REQUIRED_DATA = ["race_date", "race_id", "horse_id", "horse_number", "finish", "time_seconds", "distance",
                  "surface", "venue", "odds", "popularity", "tansho_payout"]


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def _trial_contract(trial: dict) -> dict:
    if not isinstance(trial, dict):
        raise ValueError("trial must be an object")
    required = {"id", "target", "validation_years", "train_start_year", "feature_columns", "params"}
    if not required.issubset(trial) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", str(trial["id"])):
        raise ValueError("invalid search trial contract")
    target = trial["target"]
    if target not in {"regression", "ranking", "rank_regression"}:
        raise ValueError("target must be regression, ranking, or rank_regression")
    years = trial["validation_years"]
    if (not isinstance(years, list) or not years or any(type(year) is not int for year in years)
            or years != sorted(set(years))):
        raise ValueError("validation_years must be an ascending unique integer list")
    phase = next((name for name, allowed in _BANDS.items() if set(years).issubset(allowed)), None)
    if phase is None:
        raise ValueError("search, confirmation and retrospective years must not mix")
    if "phase" in trial and trial["phase"] != phase:
        raise ValueError("phase conflicts with validation years")
    start = trial["train_start_year"]
    if type(start) is not int or start not in {2016, 2018, 2020} or start >= min(years):
        raise ValueError("train_start_year must be 2016, 2018, or 2020 and precede validation")
    features = trial["feature_columns"]
    if not isinstance(features, list) or not features or not all(isinstance(value, str) and value for value in features) or len(features) != len(set(features)):
        raise ValueError("feature_columns must be a nonempty unique string list")
    if not isinstance(trial["params"], dict):
        raise ValueError("params must be an object")
    bootstrap = _integer(trial.get("bootstrap_samples", 300), "bootstrap_samples", 0, 2000)
    contributions = _integer(trial.get("contribution_sample", 128), "contribution_sample", 0, 512)
    save_model = trial.get("save_model", True)
    if type(save_model) is not bool:
        raise ValueError("save_model must be boolean")
    if phase != "search" and (not bootstrap or not contributions or not save_model):
        raise ValueError("confirmation and retrospective verification require full artifacts and evaluation")
    seed = _integer(trial.get("seed", 42), "seed", 0, 2147483647)
    threads = _integer(trial.get("num_threads", 4), "num_threads", 1, 4)
    regression_loss = trial.get("regression_loss", "mse")
    ranking_loss = trial.get("ranking_loss", "lambdarank")
    if regression_loss not in {"mse", "mae", "huber"} or ranking_loss not in {"lambdarank", "rank_xendcg"}:
        raise ValueError("unsupported bounded loss function")
    if target == "ranking" and "regression_loss" in trial:
        raise ValueError("ranking cannot declare a regression loss")
    if target != "ranking" and "ranking_loss" in trial:
        raise ValueError("regression cannot declare a ranking loss")
    normalized = {**trial, "phase": phase, "seed": seed, "num_threads": threads,
                  "device": trial.get("device", "cpu"), "bootstrap_samples": bootstrap,
                  "contribution_sample": contributions, "save_model": save_model}
    params = dict(trial["params"])
    if "num_boost_round" in trial:
        if "num_boost_round" in params and trial["num_boost_round"] != params["num_boost_round"]:
            raise ValueError("conflicting num_boost_round declarations")
        params["num_boost_round"] = trial["num_boost_round"]
    alpha = params.pop("huber_alpha", None)
    if alpha is not None and (target == "ranking" or regression_loss != "huber" or isinstance(alpha, bool)
                              or not isinstance(alpha, (int, float)) or not np.isfinite(alpha) or not 0 < alpha < 1):
        raise ValueError("huber_alpha requires Huber regression and must be between 0 and 1")
    normalized["params"] = {**params, **({"huber_alpha": alpha} if alpha is not None else {})}
    model_params, rounds = _params({**normalized, "params": params}, ranking=target == "ranking")
    _integer(rounds, "num_boost_round", 1, 1000)
    if target == "ranking":
        model_params["objective"] = ranking_loss
    else:
        model_params["objective"] = {"mse": "regression", "mae": "regression_l1", "huber": "huber"}[regression_loss]
        if regression_loss == "huber":
            model_params["alpha"] = float(alpha if alpha is not None else 0.9)
    # Bind normalized semantic settings as well as the actual LightGBM settings.
    normalized["num_boost_round"] = rounds
    normalized["loss"] = ranking_loss if target == "ranking" else regression_loss
    return {"trial": normalized, "params": model_params, "rounds": rounds,
            "phase": phase, "task": "speed_regression" if target == "regression" else "ranking"}


def _search_features(manifest: dict, columns: list[str]) -> tuple[list[str], list[str]]:
    base = manifest.get("feature_sets", {}).get("M03", [])
    expanded = manifest.get("feature_allowlist", [])
    if any(not isinstance(values, list) or not all(isinstance(value, str) and value for value in values)
           for values in (base, expanded)):
        raise ValueError("manifest feature allowlists must be string lists")
    allowed = list(dict.fromkeys(base + expanded))
    if not allowed:
        raise ValueError("manifest has no safe search feature allowlist")
    # Reuse the existing forbidden-result/market/unsafe-prev guard, not an
    # arbitrary selection of numeric parquet columns.
    return _features({**manifest, "feature_sets": {"M03": allowed}},
                     {"candidate_id": "M03", "feature_columns": columns})


def run_search_trial(dataset_dir: Path, output_dir: Path, trial: dict,
                     progress: Callable[[str, int], None] | None = None) -> dict:
    """Run a fixed search payload sequentially; caller owns search/selection."""
    import lightgbm as lgb

    settings = _trial_contract(trial)
    request, params = settings["trial"], settings["params"]
    years, start = request["validation_years"], request["train_start_year"]
    bootstrap, contribution_sample, save_model = request["bootstrap_samples"], request["contribution_sample"], request["save_model"]
    screening = bootstrap == 0
    task, target = settings["task"], request["target"]
    notify = progress if progress is not None else lambda stage, pct: None
    dataset_dir, output_dir = Path(dataset_dir).resolve(), Path(output_dir).resolve()
    protected = (Path(__file__).resolve().parents[1] / "models").resolve()
    if output_dir == dataset_dir or output_dir.is_relative_to(dataset_dir) or output_dir == protected or output_dir.is_relative_to(protected):
        raise ValueError("search output must be separate from dataset and active models")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError("search output must be empty; never overwrite a trial")
    notify("validate fixed search payload and dataset", 0)
    manifest_path, dataset_path = dataset_dir / "dataset_manifest.json", dataset_dir / "dataset.parquet"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    import hashlib
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    dataset_hash = _hash(dataset_path)
    if dataset_hash != manifest.get("dataset_sha256"):
        raise ValueError("dataset hash mismatch")
    for key, actual in (("dataset_sha256", dataset_hash), ("manifest_sha256", manifest_hash)):
        if key in request and request[key] != actual:
            raise ValueError(f"fixed trial {key} mismatch")
    columns, categorical = _search_features(manifest, request["feature_columns"])
    available = set(pq.read_schema(dataset_path).names)
    declared = [column for column in ("num_horses", "expected_runner_count") if column in available]
    required = list(dict.fromkeys(_REQUIRED_DATA + declared + columns))
    if not declared or not set(required).issubset(available):
        raise ValueError("required features or complete-runner declarations missing")
    # Screening never materializes confirmation/2026 rows. History features are
    # already strict-as-of in the immutable, explicitly allowlisted dataset.
    frame = _validate_frame(pd.read_parquet(dataset_path, columns=required,
                            filters=[("race_date", ">=", f"{start}0101"), ("race_date", "<=", f"{max(years)}1231")]))
    for year in years:
        if not frame["race_date"].between(f"{start}0101", f"{year - 1}1231").any() or not frame["race_date"].between(f"{year}0101", f"{year}1231").any():
            raise ValueError(f"missing training or evaluation period for {year}")
    source_contract = {key: manifest.get(key) for key in ("snapshot_sha256", "eligibility_sha256", "dataset_schema_version",
                      "feature_schema_version", "scope", "source_contract", "point_in_time_audit", "payout_scope", "payout_unit",
                      "search_feature_contract", "source_dataset_schema_version", "source_feature_schema_version")}
    source_contract["modules"] = {name: _hash(Path(__file__).with_name(name))
                                  for name in ("search_training.py", "search_metrics.py", "training.py", "evaluation.py")}
    artifact_policy = {"save_model": save_model, "bootstrap_samples": bootstrap, "contribution_sample": contribution_sample,
                       "screening_primary_only": screening, "predictions_saved": True, "full_verification": not screening and bool(contribution_sample) and save_model}
    output_dir.mkdir(parents=True, exist_ok=True)
    folds, prediction_parts, record_parts, contributions = [], [], [], []
    for index, year in enumerate(years):
        train = frame.loc[frame["race_date"].between(f"{start}0101", f"{year - 1}1231")].reset_index(drop=True)
        validation = frame.loc[frame["race_date"].between(f"{year}0101", f"{year}1231")].reset_index(drop=True)
        fold_pct = 5 + int(index / len(years) * 76)
        notify(f"{year}: fit fold-local encoder and target", fold_pct)
        encoder = FoldEncoder(columns, categorical)
        x_train, x_validation = encoder.fit_transform(train), None
        x_validation = encoder.transform(validation)
        baseline = None
        fold_params = dict(params)
        if target == "regression":
            baseline = fit_speed_deviation_baseline(train)
            y_train, y_validation = apply_speed_deviation_baseline(train, baseline), apply_speed_deviation_baseline(validation, baseline)
        elif target == "ranking":
            y_train = (train["expected_runner_count"] - pd.to_numeric(train["finish"]) + 1).astype(int)
            y_validation = (validation["expected_runner_count"] - pd.to_numeric(validation["finish"]) + 1).astype(int)
            fold_params["label_gain"] = list(range(int(y_train.max()) + 1))
        else:
            y_train = (train["expected_runner_count"] - pd.to_numeric(train["finish"])) / (train["expected_runner_count"] - 1)
            y_validation = (validation["expected_runner_count"] - pd.to_numeric(validation["finish"])) / (validation["expected_runner_count"] - 1)
        fitted_categories = [column for column in categorical if column in encoder.feature_columns]
        data = lgb.Dataset(x_train, label=y_train, categorical_feature=fitted_categories,
                           group=_ranking_groups(train) if target == "ranking" else None)
        def training_progress(env: Any) -> None:
            if env.iteration % 20 == 0:
                notify(f"{year}: search training round {env.iteration + 1}/{settings['rounds']}",
                       min(80, fold_pct + int(60 / len(years) * (env.iteration + 1) / settings["rounds"])))
        model = lgb.train(fold_params, data, num_boost_round=settings["rounds"], callbacks=[training_progress])
        notify(f"{year}: predict complete evaluation cohort", min(81, fold_pct + int(62 / len(years))))
        predicted = np.asarray(model.predict(x_validation, num_threads=request["num_threads"]), dtype=float)
        if predicted.shape != (len(validation),) or not np.isfinite(predicted).all():
            raise ValueError("every evaluation runner requires a finite prediction")
        evaluated = validation[["race_id", "race_date", "horse_id", "horse_number", "surface", "finish", "popularity", "odds", "tansho_payout", "expected_runner_count"]].copy()
        evaluated["prediction"], evaluated["actual"], evaluated["fold_year"] = predicted, y_validation.to_numpy(), year
        if target == "regression":
            evaluated["actual_speed_mps"] = raw_speed_mps(validation).to_numpy()
            evaluated["prediction_speed_mps"] = _inverse_speed(predicted, validation, baseline)
        if screening:
            records = primary_race_records(evaluated, task=task)
            fold_evaluation = evaluate_primary_records(records)
        else:
            records = race_evaluation_records(evaluated, task=task,
                       progress=lambda count: notify(f"{year}: verify complete races {count}", min(82, fold_pct + int(65 / len(years)))))
            fold_evaluation = evaluate_race_records(records, seed=request["seed"], bootstrap_samples=0)
        contract = {"schema": SCHEMA, "trial": request, "dataset_sha256": dataset_hash, "manifest_sha256": manifest_hash,
                    "source_contract": source_contract, "train_period": [f"{start}0101", f"{year - 1}1231"],
                    "evaluation_year": year, "evaluation_label": _LABELS[settings["phase"]],
                    "target": target, "task": task, "loss": request["loss"], "params": fold_params,
                    "num_boost_round": settings["rounds"], "target_baseline": baseline,
                    "requested_features": columns, "feature_columns": encoder.feature_columns,
                    "dropped_columns": encoder.dropped_columns, "categorical_features": fitted_categories,
                    "validation_used_for_fit_or_early_stopping": False, "market_features": False,
                    "history_policy": "source_race_date < target_race_date; train_start limits supervised fitting, not prior feature history",
                    "artifact_policy": artifact_policy, "cohort": COHORT, "real_world_all_races": False,
                    "score_is_probability": False}
        artifact = output_dir / f"fold_{year}.joblib" if save_model else None
        if artifact is not None:
            joblib.dump({"model": model, "encoder": encoder, "baseline": baseline,
                         "feature_columns": encoder.feature_columns, "contract": contract}, artifact)
        folds.append({"year": year, "train_rows": len(train), "validation_rows": len(validation),
                      "train_races": int(train["race_id"].nunique()), "validation_races": int(validation["race_id"].nunique()),
                      "feature_count": len(encoder.feature_columns), "contract": contract, "evaluation": fold_evaluation,
                      "artifact": str(artifact) if artifact is not None else None, "sha256": _hash(artifact) if artifact is not None else None})
        prediction_parts.append(evaluated)
        record_parts.append(records)
        if contribution_sample:
            notify(f"{year}: bounded training-only contribution diagnostic", min(82, fold_pct + int(70 / len(years))))
            sample = x_train.sample(n=min(contribution_sample, len(x_train)), random_state=request["seed"])
            try:
                values = np.asarray(model.predict(sample, pred_contrib=True, num_threads=request["num_threads"]))
                importance = np.abs(values[:, :-1]).mean(axis=0)
                contributions.extend({"fold_year": year, "feature": name, "mean_abs_contribution": float(value), "sample_rows": len(sample)}
                                     for name, value in zip(encoder.feature_columns, importance))
            except (lgb.basic.LightGBMError, ValueError, TypeError) as exc:
                contributions.append({"fold_year": year, "diagnostic_unavailable": type(exc).__name__})
        notify(f"{year}: search fold complete", 5 + int((index + 1) / len(years) * 76))
    notify("aggregate fixed-year search metrics", 84)
    all_records = pd.concat(record_parts, ignore_index=True)
    if screening:
        evaluation = evaluate_primary_records(all_records)
    else:
        evaluation = evaluate_race_records(all_records, seed=request["seed"], bootstrap_samples=bootstrap,
                     progress=lambda replicate: notify(f"date bootstrap {replicate}/{bootstrap}", 84 + int(replicate / bootstrap * 8)))
        evaluation["evaluation_mode"] = "full_verification"
    prediction_path = output_dir / "predictions.parquet"
    pd.concat(prediction_parts, ignore_index=True).to_parquet(prediction_path, index=False)
    contribution_path = output_dir / "training_contributions.json"
    contribution_path.write_text(json.dumps({"enabled": bool(contribution_sample), "sample_limit_per_fold": contribution_sample,
                                           "diagnostic_only": True, "training_only": True, "selection_evidence": False,
                                           "rows": contributions}, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    if _hash(dataset_path) != dataset_hash or _hash(manifest_path) != manifest_hash:
        raise ValueError("dataset or manifest changed during search trial")
    result = {"schema": SCHEMA, "status": "completed", "trial_id": str(request["id"]),
              "candidate_id": str(request.get("candidate_id", request["id"])), "phase": settings["phase"],
              "target": target, "task": task, "loss": request["loss"], "validation_years": years, "train_start_year": start,
              "feature_columns": columns,
              "evaluation_label": _LABELS[settings["phase"]], "cohort": COHORT, "real_world_all_races": False,
              "dataset_sha256": dataset_hash, "manifest_sha256": manifest_hash, "source_contract": source_contract,
              "artifact_policy": artifact_policy, "folds": folds, "evaluation": evaluation,
              "artifacts": {"predictions": {"path": str(prediction_path), "sha256": _hash(prediction_path)},
                            "training_contributions": {"path": str(contribution_path), "sha256": _hash(contribution_path)}},
              "warnings": ["Existing confirmation/2026 data is not an unseen holdout.",
                           "Complete-runner selected cohort; not all real-world races. Scores are not probabilities.",
                           "Search screening omits betting-rule evaluation; do not infer ROI from primary metrics."]
                          + (["Model artifacts intentionally not saved; this is not a deployable model."] if not save_model else [])}
    (output_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    notify("isolated search trial complete", 100)
    return result

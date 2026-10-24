"""Sequential, isolated LightGBM trials with fold-local preprocessing.

This module does not read a live database, activate models, fetch data, tune on
validation labels, or start additional processes. The final 2026 period is
explicitly a known-reused retrospective comparison, not an unseen holdout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np
import pandas as pd

from keiba_ai.constants import FUTURE_FIELDS
from keiba_ai.speed_deviation import apply_speed_deviation_baseline, fit_speed_deviation_baseline, raw_speed_mps

from .evaluation import COHORT, evaluate_race_records, race_evaluation_records


SCHEMA = "isolated-comparison-trial-v1"
DEVELOPMENT_YEARS = (2023, 2024, 2025)
MAX_THREADS = 8
_PROHIBITED_FEATURES = set(FUTURE_FIELDS) | {
    "race_id", "horse_id", "race_date", "finish", "finish_position", "time_seconds", "time",
    "speed_deviation", "odds", "popularity", "tansho_payout", "fukusho_payout",
    "implied_prob", "implied_prob_norm", "market_entropy", "odds_rank_in_race",
}
_ALLOWED_PARAMS = {
    "num_boost_round", "learning_rate", "num_leaves", "max_depth", "min_data_in_leaf",
    "min_sum_hessian_in_leaf", "feature_fraction", "bagging_fraction", "bagging_freq",
    "lambda_l1", "lambda_l2", "reg_alpha", "reg_lambda", "max_bin", "min_gain_to_split",
    "path_smooth", "extra_trees", "max_cat_threshold", "cat_l2", "cat_smooth",
}


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class FoldEncoder:
    """Train-only categories/column selection; retain numeric unknowns as NaN."""
    requested_columns: list[str]
    categorical_columns: list[str]
    categories: dict[str, list[str]] = field(default_factory=dict)
    feature_columns: list[str] = field(default_factory=list)
    dropped_columns: dict[str, str] = field(default_factory=dict)

    def fit_transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        self.categories = {}
        self.feature_columns = []
        self.dropped_columns = {}
        for column in self.categorical_columns:
            values = frame[column].astype("string").str.strip().replace("", pd.NA)
            self.categories[column] = sorted(values.dropna().unique().tolist())
        encoded = self._encode(frame)
        for column in self.requested_columns:
            observed = encoded[column].dropna()
            if observed.empty:
                self.dropped_columns[column] = "all_missing_in_training"
            elif observed.nunique() <= 1 and len(observed) == len(encoded):
                self.dropped_columns[column] = "constant_in_training"
            else:
                self.feature_columns.append(column)
        if not self.feature_columns:
            raise ValueError("fold has no variable, observed training features")
        return encoded.loc[:, self.feature_columns]

    def _encode(self, frame: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=frame.index)
        for column in self.requested_columns:
            if column not in frame:
                raise ValueError(f"missing feature column: {column}")
            if column in self.categories:
                mapping = {value: index for index, value in enumerate(self.categories[column])}
                result[column] = frame[column].astype("string").str.strip().map(mapping).astype(float)
            else:
                result[column] = pd.to_numeric(frame[column], errors="coerce").astype(float).replace([np.inf, -np.inf], np.nan)
        return result

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        if not self.feature_columns:
            raise ValueError("encoder is not fitted")
        return self._encode(frame).loc[:, self.feature_columns]


def _features(manifest: dict, trial: dict) -> tuple[list[str], list[str]]:
    candidate = trial["candidate_id"]
    base = "M03" if candidate == "M04" else candidate
    feature_sets = manifest.get("feature_sets", {})
    allowed = feature_sets.get(base)
    if not isinstance(allowed, list) or not allowed or not all(isinstance(item, str) for item in allowed):
        raise ValueError(f"missing feature set: {base}")
    columns = trial.get("feature_columns", allowed)
    if not isinstance(columns, list) or not columns or len(set(columns)) != len(columns) or not set(columns).issubset(allowed):
        raise ValueError("feature_columns must be a nonempty unique subset of the manifest feature set")
    if candidate == "M04" and "feature_columns" not in trial:
        raise ValueError("M04 requires a locked feature_columns selection")
    unsafe = [column for column in columns if column in _PROHIBITED_FEATURES or column.startswith(("prev_", "prev2_", "prev_race", "prev2_race")) or any(token in column.lower() for token in ("odds", "popularity", "payout", "implied_prob", "market_"))]
    if unsafe:
        raise ValueError(f"unsafe or market-aware feature columns: {unsafe}")
    categorical = [column for column in manifest.get("categorical_features", []) if column in columns]
    return list(columns), categorical


def _validate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"race_date", "race_id", "horse_id", "horse_number", "finish", "time_seconds", "distance", "surface", "venue", "odds", "popularity", "tansho_payout"}
    if not required.issubset(frame):
        raise ValueError(f"dataset missing columns: {sorted(required - set(frame.columns))}")
    frame = frame.copy()
    frame["race_date"] = frame["race_date"].astype("string")
    dates = pd.to_datetime(frame["race_date"], format="%Y%m%d", errors="coerce")
    if frame.empty or not frame["race_date"].str.fullmatch(r"\d{8}").fillna(False).all() or dates.isna().any():
        raise ValueError("valid YYYYMMDD dates required")
    frame["race_id"] = frame["race_id"].astype("string").str.strip()
    if frame["race_id"].isna().any() or frame["race_id"].eq("").any():
        raise ValueError("missing race_id")
    # Identity for evaluating runners is race + verified horse number. Missing
    # provider horse IDs remain missing; never invent IDs or join their history.
    frame["horse_id"] = frame["horse_id"].astype("string").fillna("").str.strip()
    number = pd.to_numeric(frame["horse_number"], errors="coerce")
    if not (np.isfinite(number).all() and number.between(1, 18).all() and number.eq(np.floor(number)).all()):
        raise ValueError("horse_number must be a verified JRA runner number")
    frame["horse_number"] = number.astype(int)
    identified = frame.loc[frame["horse_id"].ne("")]
    if identified.duplicated(["race_id", "horse_id"]).any() or frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("duplicate runner rows")
    sizes = frame.groupby("race_id")["race_id"].transform("size")
    if sizes.lt(2).any():
        raise ValueError("each race requires at least two runners")
    if not {"num_horses", "expected_runner_count"}.intersection(frame.columns):
        raise ValueError("declared runner count is required to prove complete cohorts")
    for column in ("num_horses", "expected_runner_count"):
        if column in frame and not pd.to_numeric(frame[column], errors="coerce").eq(sizes).all():
            raise ValueError("dataset contains a partial runner cohort")
    frame["expected_runner_count"] = sizes
    for column in ("race_date", "surface", "distance"):
        if frame.groupby("race_id")[column].nunique(dropna=False).ne(1).any():
            raise ValueError(f"inconsistent race {column}")
    finish = pd.to_numeric(frame["finish"], errors="coerce")
    if not (np.isfinite(finish).all() and finish.gt(0).all() and finish.eq(np.floor(finish)).all() and finish.le(sizes).all()):
        raise ValueError("all runner targets must be settled numeric finishes")
    if not frame.assign(_winner=finish.eq(1)).groupby("race_id")["_winner"].any().all():
        raise ValueError("every race requires a confirmed winner")
    if not np.isfinite(raw_speed_mps(frame)).all():
        raise ValueError("all runner speeds must be verified; do not drop individual runners")
    return frame.sort_values(["race_date", "race_id", "horse_number", "horse_id"], kind="stable").reset_index(drop=True)


def _ranking_groups(frame: pd.DataFrame) -> list[int]:
    ids = frame["race_id"].astype(str).tolist()
    groups: list[int] = []
    seen: set[str] = set()
    current = None
    for race_id in ids:
        if race_id != current:
            if race_id in seen:
                raise ValueError("ranking queries must be contiguous")
            seen.add(race_id)
            groups.append(0)
            current = race_id
        groups[-1] += 1
    return groups


def _inverse_speed(predicted: np.ndarray, frame: pd.DataFrame, baseline: dict) -> np.ndarray:
    # Reuse the baseline's own surface precedence and rounded-distance key.
    from keiba_ai.speed_deviation import _group_keys
    keys = _group_keys(frame)
    means = keys.map(lambda key: baseline["groups"].get(key, {}).get("mean", baseline["global_mean"]))
    stds = keys.map(lambda key: baseline["groups"].get(key, {}).get("std", baseline["global_std"]))
    return np.asarray(predicted) * stds.to_numpy() + means.to_numpy()


def _params(trial: dict, *, ranking: bool) -> tuple[dict, int]:
    requested = dict(trial.get("params", {}))
    unknown = set(requested) - _ALLOWED_PARAMS
    if unknown:
        raise ValueError(f"unsupported experiment parameters: {sorted(unknown)}")
    rounds = requested.pop("num_boost_round", 200)
    if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= 2000:
        raise ValueError("num_boost_round must be an integer from 1 to 2000")
    threads = trial.get("num_threads", 4)
    if isinstance(threads, bool) or not isinstance(threads, int) or not 1 <= threads <= MAX_THREADS:
        raise ValueError(f"num_threads must be between 1 and {MAX_THREADS}")
    device = trial.get("device", "cpu")
    if device not in {"cpu", "gpu"}:
        raise ValueError("device must be cpu or gpu")
    seed = int(trial.get("seed", 42))
    params = {
        "objective": "lambdarank" if ranking else "regression", "metric": "ndcg" if ranking else "rmse",
        "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 30,
        "feature_fraction": 0.9, "bagging_fraction": 1.0, "verbosity": -1,
        "seed": seed, "feature_fraction_seed": seed, "bagging_seed": seed, "data_random_seed": seed,
        "num_threads": threads, "device_type": device, **requested,
    }
    if device == "cpu":
        params.update(deterministic=True, force_col_wise=True)
    else:
        for key in ("gpu_platform_id", "gpu_device_id"):
            if key in trial:
                params[key] = int(trial[key])
    if ranking:
        params["ndcg_eval_at"] = [1, 3, 5]
    return params, rounds


def run_trial(dataset_dir: Path, output_dir: Path, trial: dict, progress: Callable[[str, int], None] | None = None) -> dict:
    """Train fixed sequential annual folds and save only isolated artifacts."""
    import lightgbm as lgb

    dataset_dir, output_dir = Path(dataset_dir).resolve(), Path(output_dir).resolve()
    protected = Path(__file__).resolve().parents[1] / "models"
    if output_dir == dataset_dir or output_dir.is_relative_to(dataset_dir) or output_dir == protected or output_dir.is_relative_to(protected):
        raise ValueError("output must be separate from dataset and active models")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("trial output directory must be empty; never overwrite an experiment")
    required_trial = {"id", "candidate_id", "phase", "device", "num_threads", "seed", "params"}
    if not required_trial.issubset(trial) or trial["candidate_id"] not in {"M01", "M02", "M03", "M04", "M05"}:
        raise ValueError("invalid trial contract")
    if trial["phase"] not in {"development", "final"}:
        raise ValueError("invalid phase")
    notify = progress if progress is not None else lambda stage, pct: None
    notify("validating isolated dataset", 0)
    manifest_path = dataset_dir / "dataset_manifest.json"
    dataset_path = dataset_dir / "dataset.parquet"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_hash = _hash(manifest_path)
    dataset_hash = _hash(dataset_path)
    if manifest.get("dataset_sha256") != dataset_hash:
        raise ValueError("dataset hash mismatch")
    source_contract = {key: manifest.get(key) for key in (
        "snapshot_sha256", "eligibility_sha256", "dataset_schema_version", "feature_schema_version",
        "scope", "source_audit", "point_in_time_audit", "payout_scope", "payout_unit", "cohort_policy",
    )}
    source_contract["dataset_source_contract"] = manifest.get("source_contract")
    source_contract["training_module_sha256"] = _hash(Path(__file__))
    source_contract["evaluation_module_sha256"] = _hash(Path(__file__).with_name("evaluation.py"))
    columns, categorical = _features(manifest, trial)
    frame = _validate_frame(pd.read_parquet(dataset_path))
    if manifest.get("row_count", len(frame)) != len(frame) or manifest.get("race_count", frame["race_id"].nunique()) != frame["race_id"].nunique():
        raise ValueError("dataset cohort counts differ from manifest")
    if not set(columns).issubset(frame):
        raise ValueError("manifest feature columns not present in dataset")
    ranking = trial["candidate_id"] == "M05"
    task = "ranking" if ranking else "speed_regression"
    params, rounds = _params(trial, ranking=ranking)
    years = DEVELOPMENT_YEARS if trial["phase"] == "development" else (2026,)
    for year in years:
        if not frame["race_date"].between("20160101", f"{year - 1}1231").any() or not frame["race_date"].between(f"{year}0101", f"{year}1231").any():
            raise ValueError(f"missing training or evaluation period for {year}")
    output_dir.mkdir(parents=True, exist_ok=True)
    folds, predictions, contribution_rows, evaluated_races = [], [], [], []
    for fold_number, year in enumerate(years):
        train = frame.loc[frame["race_date"].between("20160101", f"{year - 1}1231")].reset_index(drop=True)
        validation = frame.loc[frame["race_date"].between(f"{year}0101", f"{year}1231")].reset_index(drop=True)
        start_pct = 5 + int(fold_number / len(years) * 78)
        notify(f"{year}: fit train-only target and encoder", start_pct)
        encoder = FoldEncoder(columns, categorical)
        x_train = encoder.fit_transform(train)
        x_validation = encoder.transform(validation)
        baseline = None
        if ranking:
            y_train = (train["expected_runner_count"] - pd.to_numeric(train["finish"]) + 1).astype(int)
            y_validation = (validation["expected_runner_count"] - pd.to_numeric(validation["finish"]) + 1).astype(int)
            fold_params = {**params, "label_gain": list(range(int(y_train.max()) + 1))}
        else:
            baseline = fit_speed_deviation_baseline(train)
            y_train = apply_speed_deviation_baseline(train, baseline)
            y_validation = apply_speed_deviation_baseline(validation, baseline)
            fold_params = dict(params)
        fit_categories = [column for column in categorical if column in encoder.feature_columns]
        train_data = lgb.Dataset(x_train, label=y_train, categorical_feature=fit_categories,
                                 group=_ranking_groups(train) if ranking else None)

        def training_progress(env: Any, *, fold_year: int = year, start: int = start_pct) -> None:
            if env.iteration % 20 == 0:
                notify(f"{fold_year}: training round {env.iteration + 1}/{rounds}", min(82, start + int(65 / len(years) * (env.iteration + 1) / rounds)))

        model = lgb.train(fold_params, train_data, num_boost_round=rounds, callbacks=[training_progress])
        predicted = np.asarray(model.predict(x_validation, num_threads=params["num_threads"]), dtype=float)
        if predicted.shape != (len(validation),) or not np.isfinite(predicted).all():
            raise ValueError("model did not predict every runner")
        evaluation_frame = validation[[column for column in ("race_id", "race_date", "horse_id", "horse_number", "surface", "finish", "popularity", "odds", "tansho_payout", "expected_runner_count") if column in validation]].copy()
        evaluation_frame["prediction"] = predicted
        evaluation_frame["actual"] = y_validation.to_numpy()
        evaluation_frame["fold_year"] = year
        if not ranking:
            evaluation_frame["actual_speed_mps"] = raw_speed_mps(validation).to_numpy()
            evaluation_frame["prediction_speed_mps"] = _inverse_speed(predicted, validation, baseline)
        # This gate runs before artifacts: no partial race may silently pass.
        race_records = race_evaluation_records(
            evaluation_frame, task=task,
            progress=lambda count: notify(f"{year}: validate/evaluate complete races {count}", min(82, start_pct + int(65 / len(years)))),
        )
        evaluation = evaluate_race_records(race_records, seed=int(trial["seed"]), bootstrap_samples=0)
        evaluated_races.append(race_records)
        contract = {
            "schema": SCHEMA, "trial": trial, "cohort": COHORT, "real_world_all_races": False,
            "dataset_sha256": dataset_hash, "manifest_sha256": manifest_hash, "source_contract": source_contract,
            "train_period": ["20160101", f"{year - 1}1231"], "evaluation_year": year,
            "requested_features": columns, "feature_columns": encoder.feature_columns,
            "dropped_columns": encoder.dropped_columns, "categorical_features": fit_categories,
            "market_features": False, "validation_used_for_fit_or_early_stopping": False,
            "history_policy": "strict source_race_date < target_race_date, rolling observation replay",
            "task": task, "params": fold_params, "num_boost_round": rounds,
            "evaluation_label": "known_reused_retrospective" if year == 2026 else "development_forward_time",
        }
        artifact = output_dir / f"fold_{year}.joblib"
        joblib.dump({"model": model, "encoder": encoder, "baseline": baseline,
                     "feature_columns": encoder.feature_columns, "contract": contract}, artifact)
        folds.append({"year": year, "train_rows": len(train), "validation_rows": len(validation),
                      "train_races": int(train["race_id"].nunique()), "validation_races": int(validation["race_id"].nunique()),
                      "feature_count": len(encoder.feature_columns), "contract": contract,
                      "evaluation": evaluation, "artifact": str(artifact), "sha256": _hash(artifact)})
        predictions.append(evaluation_frame)
        # LightGBM TreeSHAP: bounded, training-only diagnostic; never a selector.
        sample = x_train.sample(n=min(128, len(x_train)), random_state=int(trial["seed"]))
        try:
            contributions = np.asarray(model.predict(sample, pred_contrib=True, num_threads=params["num_threads"]))
            importance = np.abs(contributions[:, :-1]).mean(axis=0)
            contribution_rows.extend({"fold_year": year, "feature": column, "mean_abs_contribution": float(value), "sample_rows": len(sample)} for column, value in zip(encoder.feature_columns, importance))
        except (lgb.basic.LightGBMError, ValueError, TypeError) as exc:
            contribution_rows.append({"fold_year": year, "diagnostic_unavailable": type(exc).__name__})
        notify(f"{year}: isolated fold saved", 5 + int((fold_number + 1) / len(years) * 78))
    notify("aggregate same-cohort evaluation and day bootstrap", 86)
    if _hash(dataset_path) != dataset_hash or _hash(manifest_path) != manifest_hash:
        raise ValueError("dataset or manifest changed during trial")
    combined = pd.concat(predictions, ignore_index=True)
    evaluation = evaluate_race_records(pd.concat(evaluated_races, ignore_index=True), seed=int(trial["seed"]),
                                      progress=lambda replicate: notify(f"date-cluster bootstrap {replicate}/300", 86 + int(replicate / 300 * 8)))
    prediction_path = output_dir / "predictions.parquet"
    combined.to_parquet(prediction_path, index=False)
    contribution_path = output_dir / "training_contributions.json"
    contribution_path.write_text(json.dumps({"diagnostic_only": True, "selection_evidence": False, "rows": contribution_rows}, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    result = {
        "schema": SCHEMA, "status": "completed", "trial_id": str(trial["id"]),
        "candidate_id": trial["candidate_id"], "phase": trial["phase"], "task": task,
        "cohort": COHORT, "real_world_all_races": False,
        "evaluation_label": "known_reused_retrospective" if trial["phase"] == "final" else "development_forward_time",
        "dataset_sha256": dataset_hash, "manifest_sha256": manifest_hash, "source_contract": source_contract,
        "folds": folds, "evaluation": evaluation,
        "artifacts": {"predictions": {"path": str(prediction_path), "sha256": _hash(prediction_path)},
                      "training_contributions": {"path": str(contribution_path), "sha256": _hash(contribution_path)}},
        "warnings": ["Retrospective recorded popularity is not a verified pre-cutoff market benchmark.",
                     "Complete-runner target cohort excludes incomplete races; not all real-world races.",
                     "Place and other bet types are not evaluated."] + (["2026 is known-reused retrospective, never an unseen holdout."] if trial["phase"] == "final" else []),
    }
    (output_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    notify("isolated trial complete", 100)
    return result

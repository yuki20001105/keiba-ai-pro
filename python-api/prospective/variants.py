"""Predeclared alternatives to a frozen H04; no tuning, promotion or bets.

The three retrained alternatives retain H04's features, loss and parameters.
All fitted transforms use strictly earlier fold-training rows. Reference and
ensemble alternatives only reuse hash-bound complete out-of-fold predictions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import joblib
import numpy as np
import pandas as pd

from experiments.common import file_sha256, json_sha256, read_json, write_json
from experiments.evaluation import COHORT
from experiments.search_features import MISSING_FLAGS, SOURCE_FEATURES, derive_search_features
from experiments.search_metrics import evaluate_primary_records, primary_race_records
from experiments.search_training import _search_features, _trial_contract
from experiments.training import FoldEncoder, _validate_frame


SCHEMA = "fixed-h04-prospective-variants-v1"
VARIANTS = ("reference", "history_shrink", "history_damped", "surface_split",
            "ensemble_speed", "ensemble_lambdarank")
SHRINKAGE_STRENGTH = 5.0
_KEY = ["race_id", "horse_number"]
_EVALUATION = ["race_id", "race_date", "horse_id", "horse_number", "surface", "finish",
               "popularity", "odds", "tansho_payout", "expected_runner_count"]


def _numeric(series):
    return pd.to_numeric(series, errors="coerce").astype(float).replace([np.inf, -np.inf], np.nan)


class HistoryShrinkage:
    """Count-aware shrinkage of observed features, never outcome target encoding.

    Priors are support-weighted means of *historical feature values* in the
    training partition. Missing history gets a prior, not an invented result;
    original counts and explicit missingness flags remain unchanged.
    """

    def __init__(self, damped=False):
        self.damped = bool(damped)
        self.priors = {}

    def fit(self, frame):
        mapping = {}
        for group in ("past3", "past5", "past10", "horse_surface", "horse_distance_band", "horse_course",
                      "jockey", "trainer", "sire", "damsire"):
            support = f"safe_{group}_{'count' if group.startswith('past') else 'starts'}"
            for suffix in ("win_rate", "top3_rate"):
                mapping[f"safe_{group}_{suffix}"] = support
            if self.damped and (group.startswith("past") or group.startswith("horse_")):
                mapping[f"safe_{group}_avg_speed_mps"] = support
                if group.startswith("past"):
                    mapping[f"safe_{group}_avg_finish"] = support
        if self.damped:
            for prefix in ("prev", "prev2"):
                for suffix in ("finish", "speed_mps"):
                    mapping[f"safe_{prefix}_{suffix}"] = "safe_prior_starts"
        if not set(mapping).union(mapping.values()).issubset(frame):
            raise ValueError("history-shrinkage-support-columns-missing")
        self.priors = {}
        for name, support in mapping.items():
            values, counts = _numeric(frame[name]), self._support(frame, name, support)
            valid = values.notna() & counts.gt(0)
            if name.endswith("_rate") and not values.dropna().between(0, 1).all():
                raise ValueError("historical-rate-outside-unit-interval")
            prior = float(np.average(values[valid], weights=counts[valid])) if valid.any() else None
            self.priors[name] = {"support": support, "prior": prior}
        return self

    @staticmethod
    def _support(frame, name, support):
        counts = _numeric(frame[support])
        if not counts.dropna().ge(0).all():
            raise ValueError("negative-history-support")
        if name.startswith("safe_prev2_"):
            counts = (counts - 1).clip(lower=0, upper=1)
        elif name.startswith("safe_prev_"):
            counts = counts.clip(lower=0, upper=1)
        return counts

    def transform(self, frame):
        if not self.priors:
            raise ValueError("history-shrinkage-not-fitted")
        output = frame.copy()
        original_flags = {name: output[name].copy() for name in MISSING_FLAGS if name in output}
        for name, setting in self.priors.items():
            values = _numeric(frame[name])
            counts = self._support(frame, name, setting["support"])
            if name.endswith("_rate") and not values.dropna().between(0, 1).all():
                raise ValueError("historical-rate-outside-unit-interval")
            # A missing value cannot claim support from other known fields.
            weight = counts.where(values.notna(), 0).fillna(0) / (counts.where(values.notna(), 0).fillna(0) + SHRINKAGE_STRENGTH)
            if setting["prior"] is not None:
                output[name] = weight * values.fillna(setting["prior"]) + (1 - weight) * setting["prior"]
        derived = derive_search_features(output)
        for column in derived:
            if column in output:
                output[column] = derived[column]
        for name, original in original_flags.items():
            output[name] = original
        return output

    def contract(self):
        return {"strength": SHRINKAGE_STRENGTH, "damped": self.damped, "priors": self.priors,
                "fitted_on": "fold training historical feature values only; no validation or outcome labels",
                "support_means": "observed eligible history, not proof of a complete career",
                "original_counts_and_missing_flags_preserved": True,
                "all_missing_training_feature": "leave missing; never fit from validation"}


def _settings(trial):
    if trial.get("variant") not in VARIANTS:
        raise ValueError("unknown-predeclared-variant")
    base = trial["base_spec"]
    if json_sha256(base) != trial.get("base_spec_sha256"):
        raise ValueError("frozen-h04-spec-hash-mismatch")
    if base.get("target") != "rank_regression" or base.get("regression_loss") != "huber":
        raise ValueError("frozen-h04-must-be-huber-normalized-rank-regression")
    if trial.get("device", "cpu") != "cpu" or trial.get("seed", base.get("seed", 42)) != base.get("seed", 42):
        raise ValueError("variants-use-fixed-seed-and-cpu-only")
    bootstrap = trial.get("bootstrap_samples", 300)
    if type(bootstrap) is not int or not 0 <= bootstrap <= 2000:
        raise ValueError("invalid-variant-bootstrap-count")
    request = {**base, "id": trial["id"], "validation_years": trial["validation_years"],
               "bootstrap_samples": 300, "contribution_sample": 128, "save_model": True,
               "num_threads": trial.get("num_threads", 4), "device": "cpu"}
    request.pop("phase", None)
    settings = _trial_contract(request)
    if settings["phase"] != "search" and bootstrap == 0:
        raise ValueError("later-verification-requires-bootstrap")
    return settings, bootstrap


def _rank_percentile(frame, column="prediction"):
    rank = frame.groupby("race_id", sort=False)[column].rank(method="average")
    count = frame.groupby("race_id", sort=False)[column].transform("size")
    return (rank - 1) / (count - 1)


def _evaluation(frame, bootstrap, seed, notify):
    records = primary_race_records(frame, task="ranking")
    ranked = _rank_percentile(frame)
    actual = (frame.expected_runner_count - frame.finish) / (frame.expected_runner_count - 1)
    squared = (ranked - actual) ** 2
    records["normalized_rank_squared_error"] = records.race_id.map(squared.groupby(frame.race_id).sum())
    result = evaluate_primary_records(records)

    def add(metric, subset):
        metric["normalized_rank_rmse"] = float(np.sqrt(subset.normalized_rank_squared_error.sum() / subset.runner_count.sum()))

    add(result["overall"], records)
    for key, group in records.groupby("year"):
        add(result["by_year"][str(key)], group)
    for key, group in records.groupby("surface"):
        add(result["by_surface"][str(key)], group)
    for (year, surface), group in records.groupby(["year", "surface"]):
        add(result["by_year_surface"][f"{year}:{surface}"], group)
    result["definitions"]["normalized_rank_rmse"] = "runner-weighted RMSE of within-race average-rank percentile versus (field_size - finish)/(field_size - 1); not physical speed or probability error"
    result["evaluation_mode"] = "accuracy_only_no_betting"
    result["selection_uses_roi"] = False
    support = _numeric(frame.safe_prior_starts)
    buckets = pd.Series(np.where(support.eq(0), "0", np.where(support.between(1, 2), "1-2", np.where(support.ge(3), "3+", "unknown"))), index=frame.index)
    result["by_runner_history"] = {key: {"runner_count": int(mask.sum()), "normalized_rank_rmse": float(np.sqrt(squared[mask].mean())),
                                               "rank_correlation": None, "definition": "runner error from intact whole-race ranking; not a subset race ranking"}
                                    for key in sorted(buckets.unique()) if (mask := buckets.eq(key)).any()}
    if bootstrap:
        days = list(records.race_date.unique())
        rng = np.random.default_rng(seed)
        samples = {"rank_correlation": [], "normalized_rank_rmse": []}
        by_day = records.groupby("race_date").agg(rho_sum=("rank_correlation", "sum"), rho_n=("rank_correlation", "count"),
                                                  error=("normalized_rank_squared_error", "sum"), runners=("runner_count", "sum")).reindex(days)
        values = by_day.to_numpy()
        for index in range(bootstrap):
            sums = values[rng.integers(0, len(days), len(days))].sum(axis=0)
            if sums[1]:
                samples["rank_correlation"].append(float(sums[0] / sums[1]))
            samples["normalized_rank_rmse"].append(float(np.sqrt(sums[2] / sums[3])))
            if index % 20 == 0:
                notify(f"date bootstrap {index + 1}/{bootstrap}", 90 + int(8 * (index + 1) / bootstrap))
        result["day_bootstrap_ci"] = {"unit": "race_date", "requested_replicates": bootstrap, "seed": seed, "evaluated": True,
                                      "level": 0.95, "metrics": {key: list(map(float, np.quantile(values, [0.025, 0.975]))) if values else None
                                                                for key, values in samples.items()}}
    return result


def _reference_prediction(role, descriptors, expected, dataset_manifest, years, base, tracked):
    if not descriptors:
        raise ValueError(f"missing-frozen-reference: {role}")
    parts, provenance = [], []
    for descriptor in descriptors:
        path = Path(descriptor["path"]).resolve()
        if file_sha256(path) != descriptor["sha256"]:
            raise ValueError("reference-result-hash-mismatch")
        tracked[path] = descriptor["sha256"]
        result = read_json(path)
        if result.get("status") != "completed":
            raise ValueError("reference-not-completed")
        if not set(result["validation_years"]).intersection(years):
            continue
        source = result.get("source_contract", {})
        if source.get("snapshot_sha256") != dataset_manifest.get("snapshot_sha256"):
            raise ValueError("reference-source-snapshot-mismatch")
        if role == "speed" and result.get("task") != "speed_regression":
            raise ValueError("speed-reference-must-be-speed-regression")
        if role == "lambdarank" and (result.get("target") != "ranking" or result.get("loss") != "lambdarank"):
            raise ValueError("lambda-reference-must-be-lambdarank")
        artifact = result["artifacts"]["predictions"]
        prediction_path = Path(artifact["path"]).resolve()
        if file_sha256(prediction_path) != artifact["sha256"]:
            raise ValueError("reference-prediction-hash-mismatch")
        tracked[prediction_path] = artifact["sha256"]
        frame = pd.read_parquet(prediction_path, filters=[("fold_year", "in", years)])
        fold_map = {fold["year"]: fold for fold in result["folds"] if fold["year"] in years}
        for year in frame.fold_year.unique():
            if year not in fold_map:
                raise ValueError("reference-fold-proof-missing")
            contract = fold_map[year]["contract"]
            start, end = contract["train_period"]
            if end >= f"{year}0101" or start > end or contract.get("validation_used_for_fit_or_early_stopping") is not False:
                raise ValueError("reference-not-strict-out-of-fold")
            if role == "h04":
                proof = contract["trial"]
                for name in ("target", "regression_loss", "params", "feature_columns", "train_start_year", "seed"):
                    if proof.get(name) != base.get(name):
                        raise ValueError(f"reference-is-not-frozen-h04: {name}")
            model_path = fold_map[year].get("artifact")
            model_hash = fold_map[year].get("sha256")
            if model_path and model_hash:
                model_path = Path(model_path).resolve()
                if file_sha256(model_path) != model_hash:
                    raise ValueError("reference-model-hash-mismatch")
                tracked[model_path] = model_hash
        frame["train_end_date"] = frame.fold_year.map({year: fold["contract"]["train_period"][1] for year, fold in fold_map.items()})
        if not frame.train_end_date.lt(frame.race_date).all():
            raise ValueError("reference-not-strict-out-of-fold")
        parts.append(frame)
        provenance.append({"role": role, "result": str(path), "result_sha256": descriptor["sha256"], "predictions": artifact,
                           "folds": [{"year": year, "train_period": fold["contract"]["train_period"], "model_sha256": fold.get("sha256")}
                                     for year, fold in fold_map.items()]})
    if not parts:
        raise ValueError("reference-window-not-available")
    combined = pd.concat(parts, ignore_index=True)
    primary_race_records(combined, task="ranking")
    expected_index = pd.MultiIndex.from_frame(expected[_KEY])
    observed_index = pd.MultiIndex.from_frame(combined[_KEY])
    if len(expected_index) != len(observed_index) or set(expected_index) != set(observed_index):
        raise ValueError("reference-runner-cohort-mismatch")
    combined = combined.set_index(_KEY).reindex(expected_index).reset_index()
    for column in ("race_date", "horse_id", "finish", "surface", "expected_runner_count"):
        left, right = combined[column].reset_index(drop=True), expected[column].reset_index(drop=True)
        if column in ("horse_id", "race_date", "surface"):
            same = left.astype("string").fillna("").eq(right.astype("string").fillna(""))
        else:
            same = pd.to_numeric(left).eq(pd.to_numeric(right))
        if not same.all():
            raise ValueError(f"reference-metadata-mismatch: {column}")
    if not combined.fold_year.eq(combined.race_date.astype(str).str[:4].astype(int)).all():
        raise ValueError("reference-fold-year-mismatch")
    return combined, provenance


def run_variant_trial(dataset_dir: Path, output_dir: Path, trial: dict,
                      progress: Callable[[str, int], None] | None = None) -> dict:
    """Execute one fixed alternative in an empty isolated output directory."""
    settings, bootstrap = _settings(trial)
    request, years = settings["trial"], trial["validation_years"]
    variant, base = trial["variant"], trial["base_spec"]
    notify = progress if progress is not None else lambda stage, pct: None
    dataset_dir, output_dir = Path(dataset_dir).resolve(), Path(output_dir).resolve()
    protected = Path(__file__).resolve().parents[1] / "models"
    if output_dir == dataset_dir or output_dir.is_relative_to(dataset_dir) or output_dir == protected or output_dir.is_relative_to(protected):
        raise ValueError("variant-output-must-be-isolated")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError("variant-output-must-be-empty")
    notify("verify frozen parent dataset and H04", 0)
    paths = {dataset_dir / "dataset.parquet": trial["dataset_sha256"], dataset_dir / "dataset_manifest.json": trial["manifest_sha256"]}
    for path, digest in paths.items():
        if file_sha256(path) != digest:
            raise ValueError("frozen-variant-dataset-hash-mismatch")
    manifest = read_json(dataset_dir / "dataset_manifest.json")
    if manifest.get("dataset_sha256") != trial["dataset_sha256"]:
        raise ValueError("manifest-dataset-binding-mismatch")
    columns, categories = _search_features(manifest, base["feature_columns"])
    # Read only the predeclared year band and earlier training data. The full
    # 101-feature view is needed to recompute dependencies after shrinkage.
    start = base["train_start_year"]
    frame = _validate_frame(pd.read_parquet(dataset_dir / "dataset.parquet",
                            filters=[("race_date", ">=", f"{start}0101"), ("race_date", "<=", f"{max(years)}1231")]))
    if not frame.surface.isin(["turf", "dirt"]).all():
        raise ValueError("variants-only-support-jra-turf-and-dirt")
    expected = frame.loc[frame.race_date.str[:4].astype(int).isin(years)].copy().reset_index(drop=True)
    if set(expected.race_date.str[:4].astype(int)) != set(years):
        raise ValueError("missing-variant-evaluation-period")
    expected["fold_year"] = expected.race_date.str[:4].astype(int)
    folds, parts, provenance, contributions = [], [], [], []
    retrain = variant in {"history_shrink", "history_damped", "surface_split"}
    output_dir.mkdir(parents=True, exist_ok=True)
    if not retrain:
        roles = ["h04"] + (["speed"] if variant == "ensemble_speed" else ["lambdarank"] if variant == "ensemble_lambdarank" else [])
        components = []
        for role in roles:
            component, proof = _reference_prediction(role, trial.get("reference_results", {}).get(role), expected, manifest, years, base, paths)
            components.append(component)
            provenance.extend(proof)
        evaluated = expected[_EVALUATION + ["safe_prior_starts", "fold_year"]].copy()
        evaluated["prediction"] = components[0].prediction.to_numpy() if len(components) == 1 else (
            0.5 * _rank_percentile(components[0]).to_numpy() + 0.5 * _rank_percentile(components[1]).to_numpy())
        evaluated["train_end_date"] = pd.concat([part.train_end_date for part in components], axis=1).max(axis=1).to_numpy()
        for year in years:
            rows = evaluated.loc[evaluated.fold_year.eq(year)]
            folds.append({"year": year, "validation_rows": len(rows), "validation_races": int(rows.race_id.nunique()),
                          "contract": {"train_period": [f"{start}0101", str(rows.train_end_date.max())],
                                       "validation_used_for_fit_or_early_stopping": False, "reused_components": provenance}})
        parts.append(evaluated)
    else:
        import lightgbm as lgb
        for index, year in enumerate(years):
            train = frame.loc[frame.race_date.between(f"{start}0101", f"{year - 1}1231")].copy().reset_index(drop=True)
            validation = expected.loc[expected.fold_year.eq(year)].copy().reset_index(drop=True)
            if train.empty:
                raise ValueError("missing-variant-training-period")
            transformer = None
            if variant.startswith("history_"):
                if not SOURCE_FEATURES.issubset(train):
                    raise ValueError("missing-dependencies-for-history-variant")
                transformer = HistoryShrinkage(damped=variant == "history_damped").fit(train)
                train, validation = transformer.transform(train), transformer.transform(validation)
            predicted = np.full(len(validation), np.nan)
            learners = []
            for surface in (["turf", "dirt"] if variant == "surface_split" else ["all"]):
                train_mask = train.surface.eq(surface) if surface != "all" else pd.Series(True, index=train.index)
                validation_mask = validation.surface.eq(surface) if surface != "all" else pd.Series(True, index=validation.index)
                if not validation_mask.any():
                    continue
                subset, check = train.loc[train_mask], validation.loc[validation_mask]
                if subset.empty:
                    raise ValueError("missing-training-surface; never-silently-fallback")
                encoder = FoldEncoder(columns, categories)
                x_train, x_validation = encoder.fit_transform(subset), encoder.transform(check)
                actual = (subset.expected_runner_count - subset.finish) / (subset.expected_runner_count - 1)
                cats = [name for name in categories if name in encoder.feature_columns]
                data = lgb.Dataset(x_train, label=actual.to_numpy(), categorical_feature=cats)

                def callback(env):
                    if env.iteration % 20 == 0:
                        notify(f"{year}/{surface}: fixed H04 round {env.iteration + 1}/{settings['rounds']}",
                               5 + int((index + (env.iteration + 1) / settings["rounds"]) / len(years) * 75))

                model = lgb.train(settings["params"], data, num_boost_round=settings["rounds"], callbacks=[callback])
                values = np.asarray(model.predict(x_validation, num_threads=request["num_threads"]), dtype=float)
                if values.shape != (len(check),) or not np.isfinite(values).all():
                    raise ValueError("nonfinite-or-incomplete-variant-prediction")
                predicted[validation_mask.to_numpy()] = values
                contract = {"schema": SCHEMA, "variant": variant, "base_spec": base, "base_spec_sha256": trial["base_spec_sha256"],
                            "dataset_sha256": trial["dataset_sha256"], "manifest_sha256": trial["manifest_sha256"],
                            "train_period": [f"{start}0101", f"{year - 1}1231"], "evaluation_year": year, "surface": surface,
                            "validation_used_for_fit_or_early_stopping": False, "score_is_probability": False,
                            "requested_features": columns, "feature_columns": encoder.feature_columns,
                            "dropped_columns": encoder.dropped_columns, "history_shrinkage": transformer.contract() if transformer else None}
                path = output_dir / f"fold_{year}_{surface}.joblib"
                joblib.dump({"model": model, "encoder": encoder, "history_transformer": transformer,
                             "feature_columns": encoder.feature_columns, "contract": contract}, path)
                learners.append({"surface": surface, "artifact": str(path), "sha256": file_sha256(path),
                                 "train_rows": len(subset), "validation_rows": len(check), "contract": contract})
                sample = x_train.sample(n=min(128, len(x_train)), random_state=request["seed"])
                importance = np.abs(np.asarray(model.predict(sample, pred_contrib=True, num_threads=request["num_threads"]))[:, :-1]).mean(axis=0)
                contributions.extend({"year": year, "surface": surface, "feature": name, "mean_abs_contribution": float(value)}
                                     for name, value in zip(encoder.feature_columns, importance))
            if not np.isfinite(predicted).all():
                raise ValueError("every-runner-needs-variant-prediction")
            evaluated = validation[_EVALUATION + ["safe_prior_starts", "fold_year"]].copy()
            evaluated["prediction"], evaluated["train_end_date"] = predicted, f"{year - 1}1231"
            parts.append(evaluated)
            folds.append({"year": year, "train_rows": len(train), "validation_rows": len(validation), "learners": learners,
                          "contract": {"train_period": [f"{start}0101", f"{year - 1}1231"],
                                       "validation_used_for_fit_or_early_stopping": False}})
    predictions = pd.concat(parts, ignore_index=True).sort_values(["race_date", *_KEY], kind="stable").reset_index(drop=True)
    if set(pd.MultiIndex.from_frame(predictions[_KEY])) != set(pd.MultiIndex.from_frame(expected[_KEY])) or len(predictions) != len(expected):
        raise ValueError("variant-final-cohort-mismatch")
    predictions["actual"] = (predictions.expected_runner_count - predictions.finish) / (predictions.expected_runner_count - 1)
    predictions["rank_percentile"] = _rank_percentile(predictions)
    predictions["fold_train_date_to"] = predictions.train_end_date
    evaluation = _evaluation(predictions, bootstrap, request["seed"], notify)
    for path, digest in paths.items():
        if file_sha256(path) != digest:
            raise ValueError("variant-input-changed-during-run")
    prediction_path = output_dir / "predictions.parquet"
    predictions.to_parquet(prediction_path, index=False)
    contribution_path = output_dir / "training_contributions.json"
    write_json(contribution_path, {"training_only": True, "diagnostic_only": True, "rows": contributions})
    result = {"schema": SCHEMA, "status": "completed", "trial_id": trial["id"], "variant": variant,
              "base_spec_sha256": trial["base_spec_sha256"], "validation_years": years, "phase": settings["phase"],
              "task": "ranking", "target": "rank_regression" if retrain else "reused_score", "score_is_probability": False,
              "dataset_sha256": trial["dataset_sha256"], "manifest_sha256": trial["manifest_sha256"],
              "source_contract": manifest.get("source_contract"), "feature_columns": columns, "folds": folds,
              "evaluation": evaluation, "cohort": COHORT, "real_world_all_races": False,
              "selection_years": [2021, 2022], "selection_uses_roi": False, "known_reused_confirmation_and_retrospective": True,
              "ensemble_weights": [0.5, 0.5] if variant.startswith("ensemble_") else None, "reference_provenance": provenance,
              "module_sha256": file_sha256(Path(__file__)),
              "artifacts": {"predictions": {"path": str(prediction_path), "sha256": file_sha256(prediction_path)},
                            "training_contributions": {"path": str(contribution_path), "sha256": file_sha256(contribution_path)}},
              "warnings": ["Observed history support is not complete career coverage.", "Rank error is not speed RMSE or probability calibration.",
                           "No live model promotion, betting, or selection using later windows."]}
    write_json(output_dir / "result.json", result)
    notify("fixed variant complete", 100)
    return result

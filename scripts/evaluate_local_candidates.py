"""Recompute fixed candidate accuracy without training or live-system writes.

Only a hash-pinned historical dataset, frozen evaluation code and predictions
are read. No joblib is deserialized and no database or HTTP client is used.
2023--2026 have already been observed: none is called an unseen holdout.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

for _thread_variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_thread_variable, "4")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports/generated/model-experiments/20260919-jra-safe-v1"
OUTPUT_ROOT = ROOT / "reports/generated/local-candidate-evaluation"
PINNED_MANIFESTS = {
    "contract.json": "711618158f667eaa91d04a857eb7bacd5a8c95b7a3a54f4fb6f256f5b0d85e5a",
    "trials/M01-dev/artifact_manifest.json": "fdb73c794ee121e20c75f4d9807c567219ccfc3b50cc843ee874cef26257d5ed",
    "trials/M02-dev/artifact_manifest.json": "ea556fd974c10aa00d5923379ba878bb8d6cf0cfe94d7d14314196c674fb5237",
    "trials/M01-final/artifact_manifest.json": "78adfbe38748700f21e0578de894b701c5a80f123e72150b3eda9cf389b0c751",
}
TRIALS = ("M01-dev", "M02-dev", "M01-final")
KEYS = ["race_id", "horse_number"]
METRICS = ("rank_correlation", "rmse_speed_mps", "rmse_speed_deviation", "top1_win_rate")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_path(base: Path, relative: str) -> Path:
    path = (base / relative.replace("\\", "/")).resolve()
    if not path.is_relative_to(base.resolve()) or path == base.resolve():
        raise ValueError("artifact escapes its isolated source directory")
    return path


def verify(path: Path, expected: str, receipts: dict[str, str]) -> None:
    observed = sha256(path)
    if observed != expected:
        raise ValueError(f"source SHA256 mismatch: {path.name}")
    receipts[str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)] = observed


def verify_sources() -> tuple[dict, dict[str, str]]:
    receipts: dict[str, str] = {}
    for relative, expected in PINNED_MANIFESTS.items():
        verify(checked_path(SOURCE, relative), expected, receipts)
    contract = json.loads((SOURCE / "contract.json").read_text(encoding="utf-8"))
    verify(SOURCE / "dataset/dataset.parquet", contract["dataset_sha256"], receipts)
    verify(SOURCE / "dataset/dataset_manifest.json", contract["dataset_manifest_sha256"], receipts)
    for relative, expected in contract["source_manifest"].items():
        verify(checked_path(SOURCE / "code-snapshot", relative), expected, receipts)
    for trial in TRIALS:
        base = SOURCE / "trials" / trial
        manifest = json.loads((base / "artifact_manifest.json").read_text(encoding="utf-8"))
        for relative, expected in manifest.items():
            verify(checked_path(base, relative), expected, receipts)
    return contract, receipts


def load_frozen_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, SOURCE / "code-snapshot" / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def aggregate(records: pd.DataFrame, weights: np.ndarray | None = None) -> dict:
    if records.empty:
        raise ValueError("no complete races to evaluate")
    weights = np.ones(len(records), dtype=float) if weights is None else np.asarray(weights, dtype=float)
    if weights.shape != (len(records),) or not np.isfinite(weights).all() or (weights < 0).any() or weights.sum() <= 0:
        raise ValueError("invalid race weights")
    def total(column: str) -> float:
        return float(np.dot(records[column].fillna(0).to_numpy(dtype=float), weights))
    def mean(column: str):
        valid = records[column].notna().to_numpy()
        count = float(weights[valid].sum())
        return total(column) / count if count else None
    speed_count, deviation_count = total("speed_mps_error_count"), total("error_count")
    return {
        "race_count": int(weights.sum()), "runner_count": int(total("runner_count")),
        "rank_correlation": mean("rank_correlation"),
        "constant_score_or_result_races": int(np.dot(records["rank_correlation"].isna(), weights)),
        "rmse_speed_mps": float(np.sqrt(total("speed_mps_squared_error") / speed_count)) if speed_count else None,
        "rmse_speed_deviation": float(np.sqrt(total("squared_error") / deviation_count)) if deviation_count else None,
        "top1_win_rate": mean("top1_win"),
    }


def same_cohort(left: pd.DataFrame, right: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Require equality; never silently restrict to a convenient intersection."""
    columns = ["race_id", "race_date", "horse_number", "horse_id", "finish", "surface", "actual", "actual_speed_mps"]
    if left.duplicated(KEYS).any() or right.duplicated(KEYS).any():
        raise ValueError("duplicate runner prediction")
    first = left.sort_values(KEYS, kind="stable").reset_index(drop=True)
    second = right.sort_values(KEYS, kind="stable").reset_index(drop=True)
    pd.testing.assert_frame_equal(first[columns], second[columns], check_dtype=False, check_exact=True)
    return first, second


def day_bootstrap(records: pd.DataFrame, *, samples: int = 2000, seed: int = 42,
                  paired: pd.DataFrame | None = None) -> dict:
    if isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= 2000:
        raise ValueError("bootstrap samples must be 1--2000")
    records = records.sort_values("race_id", kind="stable").reset_index(drop=True)
    if paired is not None:
        paired = paired.sort_values("race_id", kind="stable").reset_index(drop=True)
        pd.testing.assert_frame_equal(records[["race_id", "race_date", "runner_count"]],
                                      paired[["race_id", "race_date", "runner_count"]], check_dtype=False)
    days, inverse = np.unique(records.race_date.astype(str).to_numpy(), return_inverse=True)
    estimates = {metric: [] for metric in METRICS}
    rng = np.random.default_rng(seed)
    def day_totals(frame):
        columns = ["runner_count", "speed_mps_squared_error", "speed_mps_error_count",
                   "squared_error", "error_count", "top1_win", "rank_correlation"]
        values = frame[columns].fillna(0).to_numpy(dtype=float)
        values = np.column_stack([values, frame.rank_correlation.notna().to_numpy(dtype=float), np.ones(len(frame))])
        sums = np.zeros((len(days), values.shape[1]), dtype=float)
        np.add.at(sums, inverse, values)
        return sums
    primary_totals = day_totals(records)
    control_totals = day_totals(paired) if paired is not None else None
    def metrics_from_totals(totals):
        return {
            "rank_correlation": totals[6] / totals[7] if totals[7] else None,
            "rmse_speed_mps": float(np.sqrt(totals[1] / totals[2])) if totals[2] else None,
            "rmse_speed_deviation": float(np.sqrt(totals[3] / totals[4])) if totals[4] else None,
            "top1_win_rate": totals[5] / totals[8] if totals[8] else None,
        }
    if len(days) > 1:
        for _ in range(samples):
            counts = np.bincount(rng.integers(0, len(days), len(days)), minlength=len(days))
            primary = metrics_from_totals(counts @ primary_totals)
            control = metrics_from_totals(counts @ control_totals) if control_totals is not None else None
            for metric in METRICS:
                if primary[metric] is not None and (control is None or control[metric] is not None):
                    estimates[metric].append(primary[metric] - control[metric] if control else primary[metric])
    return {
        "unit": "whole race_date", "level": 0.95, "seed": seed, "samples": samples,
        "date_count": len(days), "paired_identical_draws": paired is not None,
        "multiplicity_adjusted": False, "post_selection_unbiased": False,
        "metrics": {metric: {
            "lower": float(np.quantile(values, 0.025)) if values else None,
            "upper": float(np.quantile(values, 0.975)) if values else None,
            "valid_replicates": len(values),
        } for metric, values in estimates.items()},
    }


def history_band(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series("unknown", index=values.index, dtype="object")
    result.loc[numeric.eq(0)] = "0"
    result.loc[numeric.gt(0) & numeric.lt(3)] = "1--2"
    result.loc[numeric.ge(3) & numeric.lt(10)] = "3--9"
    result.loc[numeric.ge(10)] = "10+"
    return result


def check_raw_targets(predictions: pd.DataFrame, dataset: pd.DataFrame, speed_module) -> dict:
    subset = dataset.merge(predictions[KEYS], on=KEYS, how="inner", validate="one_to_one")
    source = subset.set_index(KEYS).reindex(predictions.set_index(KEYS).index)
    if len(subset) != len(predictions) or source.race_date.isna().any():
        raise ValueError("prediction not fully present in pinned dataset")
    for column in ("race_date", "horse_id", "finish", "surface", "popularity", "tansho_payout"):
        pd.testing.assert_series_equal(source[column].reset_index(drop=True), predictions[column].reset_index(drop=True),
                                       check_dtype=False, check_names=False, check_exact=True)
    raw = speed_module.raw_speed_mps(source)
    np.testing.assert_allclose(raw, predictions.actual_speed_mps, rtol=0, atol=1e-12)
    maximum_deviation_difference = maximum_speed_difference = 0.0
    for year, cohort in predictions.groupby("fold_year", sort=True):
        train = dataset.loc[dataset.race_date.astype(str).between("20160101", f"{int(year) - 1}1231")]
        if train.empty or not train.race_date.astype(str).lt(f"{int(year)}0101").all():
            raise ValueError("training-only baseline date gate failed")
        baseline = speed_module.fit_speed_deviation_baseline(train)
        validation = source.loc[cohort.set_index(KEYS).index]
        actual = speed_module.apply_speed_deviation_baseline(validation, baseline)
        np.testing.assert_allclose(actual, cohort.actual, rtol=0, atol=1e-12)
        keys = speed_module._group_keys(validation)
        mean = keys.map(lambda key: baseline["groups"].get(key, {}).get("mean", baseline["global_mean"]))
        std = keys.map(lambda key: baseline["groups"].get(key, {}).get("std", baseline["global_std"]))
        predicted_speed = cohort.prediction.to_numpy() * std.to_numpy() + mean.to_numpy()
        np.testing.assert_allclose(predicted_speed, cohort.prediction_speed_mps, rtol=0, atol=1e-12)
        maximum_deviation_difference = max(maximum_deviation_difference, float(np.max(np.abs(actual.to_numpy() - cohort.actual.to_numpy()))))
        maximum_speed_difference = max(maximum_speed_difference, float(np.max(np.abs(predicted_speed - cohort.prediction_speed_mps.to_numpy()))))
    return {"raw_targets_verified": True, "train_only_normalizer_reconstructed": True,
            "maximum_deviation_target_difference": maximum_deviation_difference,
            "maximum_speed_inverse_difference_mps": maximum_speed_difference}


def report_trial(trial: str, predictions: pd.DataFrame, dataset: pd.DataFrame, evaluation_module, speed_module) -> tuple[dict, pd.DataFrame]:
    old = json.loads((SOURCE / "trials" / trial / "result.json").read_text(encoding="utf-8"))
    validation = check_raw_targets(predictions, dataset, speed_module)
    records = evaluation_module.race_evaluation_records(predictions.reset_index(drop=True), task="speed_regression")
    overall = aggregate(records)
    for current, previous in (("rank_correlation", "rank_correlation"), ("rmse_speed_mps", "rmse_speed_mps"),
                              ("rmse_speed_deviation", "rmse"), ("top1_win_rate", "top1_win_rate")):
        if not np.isclose(overall[current], old["evaluation"]["overall"][previous], rtol=0, atol=1e-12):
            raise ValueError(f"fixed evaluation disagrees with original: {trial}:{current}")
    history = dataset[KEYS + ["safe_prior_starts"]].merge(predictions[KEYS], on=KEYS, validate="one_to_one")
    median_history = history.groupby("race_id").safe_prior_starts.median()
    records["history_band"] = history_band(records.race_id.map(median_history)).to_numpy()
    aligned = predictions.merge(history, on=KEYS, validate="one_to_one")
    aligned["history_band"] = history_band(aligned.safe_prior_starts)
    runner_history = {}
    for key, group in aligned.groupby("history_band", sort=True):
        error = group.prediction_speed_mps - group.actual_speed_mps
        runner_history[key] = {"runner_count": len(group), "race_count_with_any_member": int(group.race_id.nunique()),
                               "rmse_speed_mps": float(np.sqrt(np.mean(error ** 2))),
                               "rank_correlation": None, "complete_race_cohort": False}
    result = {
        "trial_id": trial, "candidate_id": old["candidate_id"], "task": old["task"],
        "evaluation_years": sorted(int(value) for value in predictions.fold_year.unique()),
        "evaluation_date_from": str(predictions.race_date.min()), "evaluation_date_to": str(predictions.race_date.max()),
        "known_previously_observed_period": True, "is_unseen_holdout": False,
        "same_fixed_original_scores": True, "original_metric_agreement": True,
        "target_validation": validation, "overall": overall,
        "day_bootstrap_ci95": day_bootstrap(records),
        "by_year": {str(key): aggregate(group) for key, group in records.groupby("year", sort=True)},
        "by_surface": {str(key): aggregate(group) for key, group in records.groupby("surface", sort=True)},
        "by_year_surface": {f"{year}:{surface}": aggregate(group) for (year, surface), group in records.groupby(["year", "surface"], sort=True)},
        "by_race_median_prior_starts": {str(key): aggregate(group) for key, group in records.groupby("history_band", sort=True)},
        "by_runner_prior_starts_diagnostic": runner_history,
        "segment_day_bootstrap_ci95": {
            "by_year": {str(key): day_bootstrap(group) for key, group in records.groupby("year", sort=True)},
            "by_surface": {str(key): day_bootstrap(group) for key, group in records.groupby("surface", sort=True)},
            "by_race_median_prior_starts": {str(key): day_bootstrap(group) for key, group in records.groupby("history_band", sort=True)},
        },
        "artifact_folds": [{"year": fold["year"], "sha256": fold["sha256"],
                            "features": fold["feature_count"], "train_period": fold["contract"]["train_period"]}
                           for fold in old["folds"]],
    }
    return result, records


def write_new_report(output: Path, report: dict) -> None:
    output = output.resolve()
    if not output.is_relative_to(OUTPUT_ROOT.resolve()) or output.parent == OUTPUT_ROOT.resolve() or output.name != "evaluation.json":
        raise ValueError("report must be in a new local-candidate-evaluation run directory")
    output.parent.mkdir(parents=True, exist_ok=False)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)


def run() -> dict:
    script_sha = sha256(Path(__file__))
    contract, before = verify_sources()
    dataset = pd.read_parquet(SOURCE / "dataset/dataset.parquet")
    dataset = dataset.sort_values(["race_date", "race_id", "horse_number", "horse_id"], kind="stable").reset_index(drop=True)
    evaluation = load_frozen_module("fixed_candidate_evaluation", "python-api/experiments/evaluation.py")
    speed = load_frozen_module("fixed_candidate_speed", "keiba/keiba_ai/speed_deviation.py")
    results, records, predictions = {}, {}, {}
    for trial in TRIALS:
        predictions[trial] = pd.read_parquet(SOURCE / "trials" / trial / "artifacts/predictions.parquet")
        results[trial], records[trial] = report_trial(trial, predictions[trial], dataset, evaluation, speed)
        print(f"verified {trial}: {results[trial]['overall']['race_count']} complete races", flush=True)
    same_cohort(predictions["M01-dev"], predictions["M02-dev"])
    common = sorted(set(results["M01-dev"]["evaluation_years"]) & set(results["M02-dev"]["evaluation_years"]))
    comparison = {
        "control": "M01-dev", "candidate": "M02-dev", "same_complete_runner_cohort": True,
        "years": common, "delta_definition": "M02 minus M01; positive correlation / negative RMSE are improvements",
        "delta": {metric: results["M02-dev"]["overall"][metric] - results["M01-dev"]["overall"][metric] for metric in METRICS},
        "paired_day_bootstrap_ci95": day_bootstrap(records["M02-dev"], paired=records["M01-dev"]),
        "by_year": {str(year): {
            "delta": {metric: results["M02-dev"]["by_year"][str(year)][metric] - results["M01-dev"]["by_year"][str(year)][metric] for metric in METRICS},
            "paired_day_bootstrap_ci95": day_bootstrap(records["M02-dev"].loc[records["M02-dev"].year.eq(str(year))],
                                                     paired=records["M01-dev"].loc[records["M01-dev"].year.eq(str(year))]),
        } for year in common},
    }
    _, after = verify_sources()
    if before != after or sha256(Path(__file__)) != script_sha:
        raise ValueError("frozen artifacts changed during evaluation")
    return {
        "schema": "fixed-local-candidate-accuracy-v1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_script_sha256": script_sha,
        "source_experiment": "20260919-jra-safe-v1", "hash_verification_pass": True,
        "verified_source_hashes": before, "source_hashes_unchanged_after": True,
        "dataset_sha256": contract["dataset_sha256"],
        "definitions": {
            "rank_correlation": "macro mean per-complete-race Spearman(prediction, -finish), constant vectors excluded and counted",
            "rmse_speed_mps": "horse-weighted physical-speed error in metres/second; distance/time_seconds vs inverse(train-only baseline,prediction)",
            "rmse_speed_deviation": "horse-weighted standardized speed-target error; NOT m/s, NOT normalized rank",
            "history_race_segment": "median strictly prior observed eligible DB starts: 0, 1--2, 3--9, 10+; does not prove complete career",
            "history_runner_segment": "runner-level physical RMSE diagnostic only, never partial-runner Spearman",
            "bootstrap": "2000 paired whole-date cluster draws seed 42; descriptive CI, not selection/multiplicity-adjusted",
        },
        "trials": results, "same_period_comparison": comparison,
        "2026_M01_vs_M02": {"comparable": False, "reason": "M02 2026 fold/predictions are absent; M01-final 2026 cannot be compared with M02-dev 2025"},
        "ranking_rmse_candidate_conclusion": {
            "M02_over_M01_on_2023_2025": "SUPPORTED" if comparison["delta"]["rank_correlation"] > 0 and comparison["delta"]["rmse_speed_mps"] < 0 else "INCONCLUSIVE",
            "latest_2026_M02_accuracy": "NOT_EVALUATED",
            "additional_training": "A separately authorized/frozen M02 2026 artifact is needed for latest-year same-cohort comparison. Do not label its already-viewed period unseen.",
            "live_semantic_input_verification": "Separate runtime gate required; historical saved-score agreement is not live feature parity.",
        },
        "roi_evaluation": {"included": False, "separate_from_model_accuracy": True,
                           "score_is_win_probability": False, "profitability_claim": False},
        "preservation": {"active_model_modified": False, "forward_validation_modified": False,
                         "production_modified": False, "original_artifacts_modified": False,
                         "live_database_accessed": False, "training_performed": False,
                         "network_used": False, "model_activation_performed": False},
        "limitations": ["Selected complete-runner JRA flat cohort, not all real-world races.",
                        "2023--2026 are already-observed retrospective evaluation, not fresh out-of-sample evidence.",
                        "No calibration or executable odds edge is established by rank/RMSE improvements.",
                        "Joblib files are hash-verified, not deserialized or re-trained."],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_ROOT / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") / "evaluation.json")
    args = parser.parse_args()
    report = run()
    write_new_report(args.output, report)
    print(json.dumps({"report": str(args.output.resolve()), "verified": True,
                      "M02_minus_M01": report["same_period_comparison"]["delta"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

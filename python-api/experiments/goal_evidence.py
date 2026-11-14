"""Recompute goal-loop evidence from pinned cohorts, not agent self-reports."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.common import file_sha256, read_json
from experiments.evaluation import evaluate_race_records, race_evaluation_records


def _probability_metrics(frame: pd.DataFrame) -> dict:
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    labels = frame["finish"].eq(1).to_numpy(dtype=int)
    probabilities = frame["win_probability"].to_numpy(dtype=float)
    bins = np.minimum((probabilities * 10).astype(int), 9)
    error = sum(
        float(mask.mean() * abs(labels[mask].mean() - probabilities[mask].mean()))
        for index in range(10) if (mask := bins == index).any()
    )
    return {
        "auc": float(roc_auc_score(labels, probabilities)),
        "log_loss": float(log_loss(labels, probabilities, labels=[0, 1])),
        "brier": float(brier_score_loss(labels, probabilities)),
        "ece": float(error), "calibration_status": "not_evaluated",
    }


def checked_artifact(root: Path, artifact: dict) -> Path:
    path = Path(artifact["path"])
    resolved = path.resolve(strict=True)
    if path.is_symlink() or not resolved.is_relative_to(root.resolve()):
        raise ValueError("goal-artifact-outside-campaign")
    if file_sha256(resolved) != artifact["sha256"]:
        raise ValueError("goal-artifact-hash-mismatch")
    return resolved


def verify_trial(root: Path, row: dict, task: dict) -> tuple[dict, pd.DataFrame]:
    """Trust only completed queue rows and the exact approved development cohort."""
    result, payload = row["result"], row["payload"]
    if row["status"] != "completed" or not isinstance(result, dict):
        raise ValueError("goal-trial-not-completed")
    if (result["trial_id"] != row["trial_id"] or result["phase"] != "development"
            or result["target"] != payload["target"]
            or result["feature_columns"] != payload["feature_columns"]
            or result["validation_years"] != task["config"]["development_years"]
            or payload["validation_years"] != task["config"]["development_years"]):
        raise ValueError("goal-trial-contract-mismatch")
    output = root / "trials" / row["trial_id"]
    if read_json(output / "result.json") != result:
        raise ValueError("goal-result-ledger-mismatch")
    artifact_manifest = read_json(output / "artifact_manifest.json")
    for relative, digest in artifact_manifest.items():
        checked_artifact(output, {"path": str(output / relative), "sha256": digest})
    for artifact in result["artifacts"].values():
        checked_artifact(output, artifact)
    years = task["config"]["development_years"]
    if [fold["year"] for fold in result["folds"]] != years:
        raise ValueError("goal-fold-years-mismatch")
    for fold in result["folds"]:
        checked_artifact(output, {"path": fold["artifact"], "sha256": fold["sha256"]})
        contract = fold["contract"]
        if (contract["train_period"] != [
                f'{task["config"]["train_start_year"]}0101', f'{fold["year"] - 1}1231']
                or contract.get("validation_used_for_fit_or_early_stopping") is not False):
            raise ValueError("goal-fold-time-boundary-invalid")

    predictions = pd.read_parquet(checked_artifact(output, result["artifacts"]["predictions"]))
    source = pd.read_parquet(
        root / "dataset" / "dataset.parquet",
        filters=[("race_date", ">=", f"{min(years)}0101"),
                 ("race_date", "<=", f"{max(years)}1231")],
    )
    source = source.loc[source.race_date.astype(str).str[:4].isin([str(y) for y in years])]
    keys = ["race_id", "horse_number"]
    if (predictions.duplicated(keys).any() or source.duplicated(keys).any()
            or len(predictions) != len(source)):
        raise ValueError("goal-evaluation-cohort-mismatch")
    expected = source.sort_values(keys, kind="stable").reset_index(drop=True)
    actual = predictions.sort_values(keys, kind="stable").reset_index(drop=True)
    for column in keys + ["race_date", "finish", "surface", "popularity", "tansho_payout"]:
        left, right = expected[column], actual[column]
        if not ((left.eq(right)) | (left.isna() & right.isna())).all():
            raise ValueError(f"goal-evaluation-source-mismatch:{column}")
    if ("fold_year" not in actual
            or not actual["fold_year"].eq(expected.race_date.astype(str).str[:4].astype(int)).all()):
        raise ValueError("goal-prediction-fold-mismatch")
    if not np.isfinite(pd.to_numeric(actual["prediction"], errors="coerce")).all():
        raise ValueError("goal-nonfinite-prediction")
    if result["target"] == "speed_deviation":
        from keiba_ai.speed_deviation import (
            apply_speed_deviation_baseline, fit_speed_deviation_baseline,
        )
        history = pd.read_parquet(
            root / "dataset" / "dataset.parquet",
            filters=[("race_date", ">=", f'{task["config"]["train_start_year"]}0101'),
                     ("race_date", "<", f"{max(years)}0101")],
        )
        for year in years:
            training = history.loc[history.race_date.lt(f"{year}0101")]
            cohort = expected.race_date.astype(str).str[:4].eq(str(year))
            target = apply_speed_deviation_baseline(
                expected.loc[cohort], fit_speed_deviation_baseline(training),
            )
            if not np.allclose(actual.loc[cohort, "actual"], target, atol=1e-10, rtol=1e-10):
                raise ValueError("goal-regression-target-does-not-match-source")
        from experiments.training import _inverse_speed
        from keiba_ai.speed_deviation import raw_speed_mps
        actual["actual_speed_mps"] = raw_speed_mps(expected).to_numpy()
        for year in years:
            cohort = expected.race_date.astype(str).str[:4].eq(str(year))
            baseline = fit_speed_deviation_baseline(history.loc[history.race_date.lt(f"{year}0101")])
            actual.loc[cohort, "prediction_speed_mps"] = _inverse_speed(
                actual.loc[cohort, "prediction"].to_numpy(), expected.loc[cohort], baseline,
            )
    if result["target"] == "win":
        probabilities = pd.to_numeric(actual["win_probability"], errors="coerce")
        if (not np.isfinite(probabilities).all() or not probabilities.between(0, 1).all()
                or not np.allclose(actual.groupby("race_id")["win_probability"].sum(), 1)
                or not np.allclose(probabilities, actual["prediction"])):
            raise ValueError("goal-invalid-race-probabilities")
    task_name = "speed_regression" if result["target"] == "speed_deviation" else "ranking"
    records = race_evaluation_records(actual, task=task_name)
    records["rank_correlation"] = records["rank_correlation"].fillna(0.0)
    evaluation = evaluate_race_records(records, bootstrap_samples=0)
    if result["target"] == "win":
        recomputed = _probability_metrics(actual)
        reported = result["evaluation"]["probability_metrics"]
        for name in ("auc", "log_loss", "brier", "ece"):
            if not math.isclose(reported[name], recomputed[name], abs_tol=1e-10, rel_tol=1e-10):
                raise ValueError(f"goal-probability-metric-mismatch:{name}")
        evaluation["probability_metrics"] = recomputed
    for name in ("rank_correlation", "top1_win_rate", "race_count"):
        reported = result["evaluation"]["overall"].get(name)
        observed = evaluation["overall"][name]
        if (isinstance(reported, bool) or not isinstance(reported, (int, float))
                or not math.isfinite(reported)
                or not math.isclose(reported, observed, abs_tol=1e-10, rel_tol=1e-10)):
            raise ValueError(f"goal-reported-metric-mismatch:{name}")
    return evaluation, records


def compare_records(baseline: pd.DataFrame, candidate: pd.DataFrame, config: dict) -> dict:
    """Paired date bootstrap; missing/insufficient evidence never becomes a win."""
    left = baseline.sort_values("race_id").reset_index(drop=True)
    right = candidate.sort_values("race_id").reset_index(drop=True)
    if (not left["race_id"].equals(right["race_id"])
            or not left["race_date"].equals(right["race_date"])):
        raise ValueError("goal-comparison-requires-identical-cohort")
    differences = right.rank_correlation.to_numpy() - left.rank_correlation.to_numpy()
    days, inverse = np.unique(left.race_date, return_inverse=True)
    daily_sum = np.bincount(inverse, weights=differences)
    daily_count = np.bincount(inverse)
    samples = []
    rng = np.random.default_rng(config["seed"])
    if len(days) >= config["minimum_paired_dates"]:
        for _ in range(config["bootstrap_samples"]):
            selected = rng.integers(0, len(days), size=len(days))
            samples.append(float(daily_sum[selected].sum() / daily_count[selected].sum()))
    lower = float(np.quantile(samples, 0.025)) if samples else None
    upper = float(np.quantile(samples, 0.975)) if samples else None
    annual = {
        str(year): float(differences[left.year.astype(str).eq(str(year))].mean())
        for year in config["development_years"]
    }
    delta = float(differences.mean())
    top1_delta = float(right.top1_win.mean() - left.top1_win.mean())
    checks = {
        "minimum_improvement": delta >= config["minimum_improvement"],
        "annual_noninferiority": min(annual.values()) >= -config["maximum_annual_degradation"],
        "top1_noninferiority": top1_delta >= -config["maximum_top1_degradation"],
        "paired_evidence": lower is not None and lower > 0,
    }
    return {
        "verdict": ("candidate" if all(checks.values()) else
                    "inconclusive" if checks["minimum_improvement"] and not checks["paired_evidence"]
                    else "rejected"),
        "rank_correlation_delta": delta, "top1_win_rate_delta": top1_delta,
        "annual_delta": annual, "paired_date_count": len(days),
        "paired_bootstrap_ci": {"lower": lower, "upper": upper, "level": 0.95},
        "checks": checks, "uses_roi_for_selection": False,
        "unseen_evidence": False, "promotion_eligible": False,
    }

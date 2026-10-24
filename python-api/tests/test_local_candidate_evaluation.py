"""Fixed accuracy evaluation cannot silently change cohort, units or evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/evaluate_local_candidates.py"
SPEC = importlib.util.spec_from_file_location("local_candidate_evaluation_test", SCRIPT)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def records():
    return pd.DataFrame({
        "race_id": ["a", "b", "c"], "race_date": ["20230101", "20230101", "20230108"],
        "runner_count": [2, 4, 2], "rank_correlation": [1.0, 0.5, np.nan],
        "speed_mps_squared_error": [2.0, 0.0, 0.0], "speed_mps_error_count": [2, 4, 2],
        "squared_error": [8.0, 0.0, 0.0], "error_count": [2, 4, 2],
        "top1_win": [1.0, 0.0, 0.0],
    })


def predictions():
    return pd.DataFrame({
        "race_id": ["race", "race"], "race_date": ["20230101", "20230101"],
        "horse_number": [1, 2], "horse_id": ["horse1", "horse2"], "finish": [1, 2],
        "surface": ["turf", "turf"], "actual": [1.0, -1.0], "actual_speed_mps": [17.0, 16.0],
        "prediction": [0.5, 0.2],
    })


def test_physical_speed_rmse_is_horse_weighted_not_mean_race_rmse():
    result = evaluation.aggregate(records())
    assert result["rmse_speed_mps"] == 0.5
    assert result["rmse_speed_deviation"] == 1.0
    assert result["rank_correlation"] == 0.75
    assert result["constant_score_or_result_races"] == 1
    assert not any("roi" in key or "return" in key for key in result)


@pytest.mark.parametrize("weights", [np.array([1, 2]), np.array([-1, 2, 0]), np.array([0, 0, 0]), np.array([np.nan, 1, 1])])
def test_invalid_weights_fail(weights):
    with pytest.raises(ValueError, match="invalid race weights"):
        evaluation.aggregate(records(), weights)


def test_same_cohort_allows_different_scores_but_not_silent_intersection():
    first = predictions()
    second = first.iloc[::-1].copy()
    second["prediction"] += 1
    left, right = evaluation.same_cohort(first, second)
    assert (right.prediction - left.prediction).eq(1).all()
    with pytest.raises(AssertionError):
        evaluation.same_cohort(first, second.iloc[:1])


@pytest.mark.parametrize("column,value", [("finish", 2), ("actual_speed_mps", 18.0), ("horse_id", "another")])
def test_same_cohort_requires_identical_truth_and_identity(column, value):
    first = predictions()
    second = first.copy()
    second.loc[0, column] = value
    with pytest.raises(AssertionError):
        evaluation.same_cohort(first, second)


def test_duplicate_predictions_fail():
    first = pd.concat([predictions(), predictions().iloc[:1]])
    with pytest.raises(ValueError, match="duplicate"):
        evaluation.same_cohort(first, predictions())


def test_identical_paired_bootstrap_has_zero_delta_and_uses_whole_days():
    result = evaluation.day_bootstrap(records(), paired=records().iloc[::-1], samples=40)
    assert result["date_count"] == 2
    assert result["paired_identical_draws"] is True
    for interval in result["metrics"].values():
        assert interval["lower"] == interval["upper"] == 0
        assert 0 < interval["valid_replicates"] <= 40


def test_paired_bootstrap_rejects_date_mismatch():
    second = records()
    second.loc[0, "race_date"] = "20230109"
    with pytest.raises(AssertionError):
        evaluation.day_bootstrap(records(), paired=second, samples=10)


def test_day_aggregation_matches_naive_full_race_resampling():
    frame = records()
    actual = evaluation.day_bootstrap(frame, samples=40)
    days, inverse = np.unique(frame.race_date.to_numpy(), return_inverse=True)
    rng = np.random.default_rng(42)
    values = {key: [] for key in evaluation.METRICS}
    for _ in range(40):
        weights = np.bincount(rng.integers(0, len(days), len(days)), minlength=len(days))
        metrics = evaluation.aggregate(frame, weights[inverse])
        for key in evaluation.METRICS:
            if metrics[key] is not None:
                values[key].append(metrics[key])
    for key, samples in values.items():
        interval = actual["metrics"][key]
        np.testing.assert_allclose([interval["lower"], interval["upper"]], np.quantile(samples, [0.025, 0.975]), rtol=0, atol=1e-12)


def test_history_bins_preserve_unknown_instead_of_zero_fill():
    assert evaluation.history_band(pd.Series([None, 0, 1, 2, 3, 9, 10])).tolist() == ["unknown", "0", "1--2", "1--2", "3--9", "3--9", "10+"]


def test_hash_mismatch_fails_before_loading(tmp_path):
    path = tmp_path / "source"
    path.write_bytes(b"original")
    receipts = {}
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        evaluation.verify(path, "0" * 64, receipts)
    assert receipts == {}


def test_source_relative_path_cannot_escape(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        evaluation.checked_path(tmp_path, "../other")


def test_reports_are_exclusive_and_inside_new_run_only(tmp_path, monkeypatch):
    root = tmp_path / "reports"
    monkeypatch.setattr(evaluation, "OUTPUT_ROOT", root)
    output = root / "run" / "evaluation.json"
    evaluation.write_new_report(output, {"preserved": True})
    with pytest.raises(FileExistsError):
        evaluation.write_new_report(output, {"preserved": False})
    assert json.loads(output.read_text()) == {"preserved": True}
    with pytest.raises(ValueError):
        evaluation.write_new_report(root / "evaluation.json", {})
    with pytest.raises(ValueError):
        evaluation.write_new_report(tmp_path / "other" / "evaluation.json", {})

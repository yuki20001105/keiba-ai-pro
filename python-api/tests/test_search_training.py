from __future__ import annotations

import hashlib
import json

import joblib
import numpy as np
import pandas as pd
import pytest

from experiments.evaluation import evaluate_predictions
from experiments.search_metrics import evaluate_primary_records, primary_race_records
from experiments.search_training import _search_features, _trial_contract, run_search_trial


def dataset(tmp_path):
    directory = tmp_path / "dataset"
    directory.mkdir()
    rows = []
    for year in (2016, 2018, 2020, 2021, 2022, 2023, 2024, 2025, 2026):
        for day in (1, 2):
            for number in range(1, 5):
                rows.append({"race_id": f"{year}0501010{day}", "race_date": f"{year}010{day}",
                             "horse_id": f"h{number}", "horse_number": number, "num_horses": 4,
                             "finish": number, "time_seconds": 70 + number * 2 + day + (year - 2016) / 10,
                             "distance": 1200 if day == 1 else 1600, "surface": "turf" if day == 1 else "dirt",
                             "venue": "Tokyo", "odds": number * 2.0, "popularity": number,
                             "tansho_payout": 220.0 if number == 1 else np.nan,
                             "safe_number": float(number), "safe_history": float(number + day),
                             "safe_category": ("A" if day == 1 else "B") if year < 2021 else "future-category",
                             "safe_constant": 1.0, "safe_late": np.nan if year < 2023 else float(number),
                             "safe_expanded": float(number ** 2 + day)})
    frame = pd.DataFrame(rows)
    frame.to_parquet(directory / "dataset.parquet", index=False)
    base = ["safe_number", "safe_history", "safe_category", "safe_constant", "safe_late"]
    manifest = {"feature_sets": {"M03": base}, "feature_allowlist": base + ["safe_expanded"],
                "categorical_features": ["safe_category"], "source_contract": {"snapshot_sha256": "fixture"},
                "search_feature_contract": {"parent_dataset_sha256": "fixture-parent"}}
    write_manifest(directory, frame, manifest)
    return directory, frame, manifest


def write_manifest(directory, frame, manifest):
    frame.to_parquet(directory / "dataset.parquet", index=False)
    manifest["dataset_sha256"] = hashlib.sha256((directory / "dataset.parquet").read_bytes()).hexdigest()
    (directory / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def request(**overrides):
    return {"id": "fixture-search", "target": "regression", "validation_years": [2021, 2022],
            "train_start_year": 2018, "feature_columns": ["safe_number", "safe_history", "safe_category", "safe_constant", "safe_late", "safe_expanded"],
            "device": "cpu", "num_threads": 1, "seed": 73,
            "params": {"num_boost_round": 3, "num_leaves": 3, "min_data_in_leaf": 2},
            "bootstrap_samples": 0, "contribution_sample": 0, "save_model": False, **overrides}


def metric_frame():
    rng = np.random.default_rng(58)
    records = []
    for race in range(12):
        size = 3 + race % 4
        scores = rng.normal(size=size)
        if race == 0:
            scores[:] = 0
        finish = np.arange(1, size + 1)
        if race == 1:
            finish[:2] = 1
        actual = rng.normal(size=size)
        for index in range(size):
            records.append({"race_id": f"r{race}", "race_date": f"{2021 + race % 2}010{race % 3 + 1}",
                            "horse_id": f"h{index}", "horse_number": index + 1, "expected_runner_count": size,
                            "finish": finish[index], "prediction": scores[index], "actual": actual[index],
                            "actual_speed_mps": actual[index] + 15, "prediction_speed_mps": scores[index] + 15,
                            "surface": "turf" if race % 2 else "dirt", "popularity": index + 1,
                            "tansho_payout": 300.0 if finish[index] == 1 else np.nan})
    return pd.DataFrame(records)


@pytest.mark.parametrize("task", ["speed_regression", "ranking"])
def test_vectorized_primary_metrics_match_existing_full_evaluation(task):
    frame = metric_frame()
    fast = evaluate_primary_records(primary_race_records(frame, task=task))
    full = evaluate_predictions(frame, task=task, bootstrap_samples=0)
    keys = ["race_count", "runner_count", "rank_correlation", "rank_correlation_races", "constant_score_or_result_races",
            "rmse", "mae", "r2", "rmse_speed_mps", "mae_speed_mps"]
    pairs = [(fast["overall"], full["overall"])]
    for breakdown in ("by_year", "by_surface", "by_year_surface"):
        assert fast[breakdown].keys() == full[breakdown].keys()
        pairs.extend((fast[breakdown][key], full[breakdown][key]) for key in fast[breakdown])
    for actual, expected in pairs:
        for key in keys:
            if expected[key] is None:
                assert actual[key] is None
            else:
                assert actual[key] == pytest.approx(expected[key], abs=1e-12)
    assert fast["overall"]["win_roi_percent"] is None
    assert fast["bet_types"]["win"] == "not_evaluated"


@pytest.mark.parametrize("mutation", ["drop_runner", "nan_prediction", "duplicate_number", "missing_target", "inconsistent_date"])
def test_vectorized_metrics_reject_partial_or_corrupt_races(mutation):
    frame = metric_frame()
    if mutation == "drop_runner":
        frame = frame.drop(index=0)
    elif mutation == "nan_prediction":
        frame.loc[0, "prediction"] = np.nan
    elif mutation == "duplicate_number":
        frame.loc[0, "horse_number"] = 2
    elif mutation == "missing_target":
        frame["finish"] = frame["finish"].astype(float)
        frame.loc[0, "finish"] = np.nan
    else:
        frame.loc[0, "race_date"] = "20220101"
    with pytest.raises(ValueError):
        primary_race_records(frame, task="speed_regression")


def test_screening_isolated_outputs_train_start_and_fold_local_fit(tmp_path):
    directory, frame, _ = dataset(tmp_path)
    updates = []
    result = run_search_trial(directory, tmp_path / "out", request(), lambda *args: updates.append(args))
    assert result["phase"] == "search"
    assert result["feature_columns"] == request()["feature_columns"]
    assert result["validation_years"] == [2021, 2022]
    assert result["source_contract"]["search_feature_contract"] == {"parent_dataset_sha256": "fixture-parent"}
    assert result["evaluation_label"] == "search_forward_time"
    assert result["evaluation"]["evaluation_mode"] == "screening_primary_only"
    assert result["evaluation"]["overall"]["rmse"] is not None
    assert result["evaluation"]["overall"]["top1_win_rate"] is None
    assert not list((tmp_path / "out").glob("*.joblib"))
    assert all(fold["artifact"] is None for fold in result["folds"])
    first = result["folds"][0]
    assert first["contract"]["train_period"] == ["20180101", "20201231"]
    assert first["train_rows"] == len(frame[frame.race_date.between("20180101", "20201231")])
    assert first["contract"]["target_baseline"]["training_sample_count"] == first["train_rows"]
    assert "safe_late" not in first["contract"]["feature_columns"]
    assert "safe_expanded" in first["contract"]["feature_columns"]
    assert first["contract"]["validation_used_for_fit_or_early_stopping"] is False
    predictions = pd.read_parquet(result["artifacts"]["predictions"]["path"])
    assert set(predictions.race_date.str[:4]) == {"2021", "2022"}
    assert result["artifact_policy"]["save_model"] is False
    diagnostic = json.loads((tmp_path / "out" / "training_contributions.json").read_text())
    assert diagnostic["enabled"] is False and diagnostic["rows"] == []
    assert updates[-1] == ("isolated search trial complete", 100)


@pytest.mark.parametrize("target,loss", [("regression", "mse"), ("regression", "mae"), ("regression", "huber"),
                                        ("ranking", "lambdarank"), ("ranking", "rank_xendcg"),
                                        ("rank_regression", "mse"), ("rank_regression", "huber")])
def test_all_bounded_objectives_train_real_tiny_data(target, loss, tmp_path):
    directory, _, _ = dataset(tmp_path)
    payload = request(target=target, validation_years=[2021], save_model=True)
    payload["ranking_loss" if target == "ranking" else "regression_loss"] = loss
    if loss == "huber":
        payload["params"]["huber_alpha"] = 0.8
    result = run_search_trial(directory, tmp_path / "out", payload)
    bundle = joblib.load(result["folds"][0]["artifact"])
    assert result["loss"] == loss
    assert bundle["contract"]["target"] == target
    if target == "regression":
        assert result["evaluation"]["overall"]["rmse"] is not None
        assert bundle["baseline"] is not None
    else:
        assert result["task"] == "ranking"
        assert result["evaluation"]["overall"]["rmse"] is None
        assert bundle["baseline"] is None
    if loss == "huber":
        assert bundle["contract"]["params"]["alpha"] == 0.8
        assert bundle["contract"]["trial"]["params"]["huber_alpha"] == 0.8
    if target == "ranking":
        assert bundle["contract"]["params"]["objective"] == loss
    if target == "rank_regression":
        pred = pd.read_parquet(result["artifacts"]["predictions"]["path"])
        expected = (pred.expected_runner_count - pred.finish) / (pred.expected_runner_count - 1)
        np.testing.assert_allclose(pred.actual, expected)
        assert bundle["contract"]["score_is_probability"] is False


def test_confirmation_requires_full_artifacts_and_uses_existing_evaluator(tmp_path):
    directory, _, _ = dataset(tmp_path)
    result = run_search_trial(directory, tmp_path / "out", request(validation_years=[2023, 2024, 2025],
                             train_start_year=2020, bootstrap_samples=5, contribution_sample=2, save_model=True))
    assert result["phase"] == "confirmation"
    assert result["evaluation_label"] == "known_reused_confirmation"
    assert len(list((tmp_path / "out").glob("*.joblib"))) == 3
    assert result["evaluation"]["evaluation_mode"] == "full_verification"
    assert result["evaluation"]["overall"]["top1_win_rate"] is not None
    assert result["evaluation"]["day_bootstrap_ci"]["requested_replicates"] == 5
    first = joblib.load(result["folds"][0]["artifact"])
    assert first["contract"]["train_period"] == ["20200101", "20221231"]
    diagnostic = json.loads((tmp_path / "out" / "training_contributions.json").read_text())
    assert diagnostic["training_only"] is True
    assert all(row["sample_rows"] == 2 for row in diagnostic["rows"])


def test_retrospective_label_never_claims_unseen(tmp_path):
    directory, _, _ = dataset(tmp_path)
    result = run_search_trial(directory, tmp_path / "out", request(validation_years=[2026],
                             bootstrap_samples=5, contribution_sample=2, save_model=True))
    assert result["evaluation_label"] == "known_reused_retrospective"
    assert result["folds"][0]["contract"]["evaluation_label"] == "known_reused_retrospective"


@pytest.mark.parametrize("change", [
    {"validation_years": [2021, 2023]}, {"validation_years": [2026, 2025]}, {"validation_years": [2021, 2021]},
    {"validation_years": [True]}, {"validation_years": [2027]}, {"train_start_year": 2019}, {"train_start_year": True},
    {"num_threads": 5}, {"num_boost_round": 1001, "params": {}}, {"num_boost_round": 4},
    {"target": "binary"}, {"phase": "confirmation"}, {"feature_columns": ["safe_number", "safe_number"]},
    {"params": {"objective": "binary"}}, {"params": {"huber_alpha": 0.8}}, {"regression_loss": "unsafe"},
    {"ranking_loss": "rank_xendcg"}, {"validation_years": [2023]}, {"validation_years": [2026]},
    {"save_model": "false"}, {"bootstrap_samples": True}, {"contribution_sample": 513},
])
def test_trial_contract_rejects_unsafe_or_ambiguous_settings(change):
    with pytest.raises(ValueError):
        _trial_contract(request(**change))


def test_manifest_expansion_cannot_authorize_results_or_market():
    for column in ("odds", "finish", "time_seconds", "prev_race_finish", "safe_market_ratio"):
        manifest = {"feature_sets": {"M03": ["safe_number"]}, "feature_allowlist": [column]}
        with pytest.raises(ValueError):
            _search_features(manifest, [column])
    with pytest.raises(ValueError):
        _search_features({"feature_sets": {"M03": ["safe_number"]}}, ["not_in_manifest"])


def test_cancellation_inside_fit_does_not_publish_completed_result(tmp_path):
    directory, _, _ = dataset(tmp_path)
    def cancel(stage, pct):
        if "training round" in stage:
            raise RuntimeError("test cancellation")
    with pytest.raises(RuntimeError, match="test cancellation"):
        run_search_trial(directory, tmp_path / "cancel", request(), cancel)
    assert not (tmp_path / "cancel" / "result.json").exists()


def test_bad_hash_and_partial_race_fail_before_training(tmp_path):
    directory, frame, manifest = dataset(tmp_path)
    with pytest.raises(ValueError, match="fixed trial dataset_sha256"):
        run_search_trial(directory, tmp_path / "wrong-hash", request(dataset_sha256="wrong"))
    assert not (tmp_path / "wrong-hash").exists()
    frame = frame.drop(frame[frame.race_date.eq("20210101")].index[0])
    write_manifest(directory, frame, manifest)
    with pytest.raises(ValueError, match="partial runner"):
        run_search_trial(directory, tmp_path / "partial", request())


def test_future_targets_cannot_change_first_search_fold(tmp_path):
    directory, frame, manifest = dataset(tmp_path)
    first = run_search_trial(directory, tmp_path / "before", request(save_model=True))
    frame.loc[frame.race_date.ge("20210101"), "time_seconds"] *= 1.5
    frame.loc[frame.race_date.ge("20210101"), "safe_late"] = 900.0
    write_manifest(directory, frame, manifest)
    second = run_search_trial(directory, tmp_path / "after", request(save_model=True))
    one, two = (joblib.load(result["folds"][0]["artifact"]) for result in (first, second))
    assert one["baseline"] == two["baseline"]
    assert one["encoder"].categories == two["encoder"].categories
    assert one["model"].model_to_string() == two["model"].model_to_string()
    assert "future-category" not in one["encoder"].categories["safe_category"]


def test_output_never_overwrites_and_dataset_unchanged(tmp_path):
    directory, _, manifest = dataset(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "owned.txt"
    marker.write_text("preserve")
    with pytest.raises(ValueError, match="empty"):
        run_search_trial(directory, output, request())
    with pytest.raises(ValueError, match="separate"):
        run_search_trial(directory, directory / "not-allowed", request())
    assert marker.read_text() == "preserve"
    assert hashlib.sha256((directory / "dataset.parquet").read_bytes()).hexdigest() == manifest["dataset_sha256"]

from __future__ import annotations

import hashlib
import json

import joblib
import numpy as np
import pandas as pd
import pytest

from experiments.training import FoldEncoder, _features, _ranking_groups, run_trial


def make_dataset(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    rows = []
    for year in (2021, 2022, 2023, 2024, 2025, 2026):
        for day in (1, 2):
            for runner in range(1, 5):
                race = f"{year}010{day}01"
                rows.append({
                    "race_date": f"{year}010{day}", "race_id": race,
                    "horse_id": f"h{runner}", "horse_number": runner, "finish": runner,
                    "time_seconds": 70 + runner * 2 + day + (year - 2021) / 10,
                    "distance": 1200 if day == 1 else 1600,
                    "surface": "芝" if day == 1 else "ダート", "venue": "東京" if year < 2023 else "未見会場",
                    "odds": runner * 2.0, "popularity": runner,
                    "tansho_payout": 220.0 if runner == 1 else np.nan,
                    "num_horses": 4, "safe_history": float(runner + day),
                    "validation_only": np.nan if year < 2023 else float(runner),
                })
    frame = pd.DataFrame(rows)
    frame.to_parquet(dataset / "dataset.parquet", index=False)
    features = ["distance", "surface", "venue", "horse_number", "num_horses", "safe_history", "validation_only"]
    manifest = {"feature_sets": {key: features for key in ("M01", "M02", "M03", "M05")},
                "categorical_features": ["surface", "venue"], "feature_groups": {"history": ["safe_history"]}}
    manifest["dataset_sha256"] = hashlib.sha256((dataset / "dataset.parquet").read_bytes()).hexdigest()
    (dataset / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return dataset, frame, manifest


def trial(candidate="M01", phase="development"):
    return {"id": "test", "candidate_id": candidate, "phase": phase, "device": "cpu",
            "num_threads": 1, "seed": 41,
            "params": {"num_boost_round": 3, "num_leaves": 3, "min_data_in_leaf": 2}}


def test_encoder_fit_is_train_only_unknowns_and_missing_values_stay_nan():
    train = pd.DataFrame({"category": ["a", "b", None], "number": [1.0, 2.0, np.nan],
                          "missing": [np.nan] * 3, "constant": [1.0] * 3})
    valid = pd.DataFrame({"category": ["future"], "number": [np.nan], "missing": [7], "constant": [9]})
    encoder = FoldEncoder(list(train.columns), ["category"])
    encoder.fit_transform(train)
    result = encoder.transform(valid)
    assert result.columns.tolist() == ["category", "number"]
    assert result.isna().all().all()
    assert encoder.categories == {"category": ["a", "b"]}
    assert encoder.dropped_columns == {"missing": "all_missing_in_training", "constant": "constant_in_training"}


def test_real_tiny_development_training_uses_only_prior_years_and_is_isolated(tmp_path):
    dataset, frame, _ = make_dataset(tmp_path)
    updates = []
    result = run_trial(dataset, tmp_path / "result", trial(), lambda stage, pct: updates.append((stage, pct)))
    assert result["status"] == "completed"
    assert [fold["year"] for fold in result["folds"]] == [2023, 2024, 2025]
    assert result["evaluation"]["overall"]["race_count"] == 6
    assert result["cohort"] == "complete_runner_target_cohort"
    assert result["real_world_all_races"] is False
    bundle = joblib.load(result["folds"][0]["artifact"])
    assert bundle["baseline"]["training_sample_count"] == len(frame[frame.race_date < "20230101"])
    assert "validation_only" not in bundle["feature_columns"]
    assert bundle["contract"]["train_period"] == ["20160101", "20221231"]
    assert bundle["contract"]["validation_used_for_fit_or_early_stopping"] is False
    assert "未見会場" not in bundle["encoder"].categories["venue"]
    predicted = pd.read_parquet(result["artifacts"]["predictions"]["path"])
    assert set(predicted.race_date.str[:4]) == {"2023", "2024", "2025"}
    assert predicted.groupby("race_id").size().eq(4).all()
    assert updates[-1] == ("isolated trial complete", 100)
    assert (tmp_path / "result" / "result.json").is_file()
    assert set(path.name for path in dataset.iterdir()) == {"dataset.parquet", "dataset_manifest.json"}


def test_real_tiny_ranking_has_exact_queries_no_probability_or_regression_metrics(tmp_path):
    dataset, _, _ = make_dataset(tmp_path)
    result = run_trial(dataset, tmp_path / "ranking", trial("M05", "final"))
    assert len(result["folds"]) == 1
    assert result["evaluation_label"] == "known_reused_retrospective"
    assert result["task"] == "ranking"
    assert result["evaluation"]["overall"]["rmse"] is None
    bundle = joblib.load(result["folds"][0]["artifact"])
    assert bundle["baseline"] is None
    assert bundle["contract"]["params"]["objective"] == "lambdarank"
    assert bundle["contract"]["evaluation_label"] == "known_reused_retrospective"
    assert _ranking_groups(pd.DataFrame({"race_id": ["a", "a", "b", "b", "b"]})) == [2, 3]
    with pytest.raises(ValueError, match="contiguous"):
        _ranking_groups(pd.DataFrame({"race_id": ["a", "b", "a"]}))


def test_cancellation_callback_interrupts_inside_training(tmp_path):
    dataset, _, _ = make_dataset(tmp_path)
    class Cancelled(RuntimeError):
        pass
    def cancel(stage, pct):
        if "training round" in stage:
            raise Cancelled("stop")
    with pytest.raises(Cancelled):
        run_trial(dataset, tmp_path / "cancelled", trial(), cancel)
    assert not (tmp_path / "cancelled" / "result.json").exists()
    assert not list((tmp_path / "cancelled").glob("*.joblib"))


@pytest.mark.parametrize("bad_feature", ["odds", "popularity", "finish", "time_seconds", "prev_race_finish", "market_entropy"])
def test_manifest_cannot_authorize_unsafe_features(bad_feature):
    manifest = {"feature_sets": {"M01": [bad_feature]}, "categorical_features": []}
    with pytest.raises(ValueError, match="unsafe"):
        _features(manifest, trial())


def test_m04_is_only_a_locked_subset_of_m03():
    manifest = {"feature_sets": {"M03": ["distance", "safe_history"]}, "categorical_features": []}
    request = trial("M04")
    with pytest.raises(ValueError, match="locked"):
        _features(manifest, request)
    request["feature_columns"] = ["distance"]
    assert _features(manifest, request)[0] == ["distance"]
    request["feature_columns"] = ["unapproved"]
    with pytest.raises(ValueError, match="subset"):
        _features(manifest, request)


def test_hash_mismatch_and_existing_output_fail_before_training(tmp_path):
    dataset, _, manifest = make_dataset(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    (output / "do-not-overwrite.txt").write_text("user data")
    with pytest.raises(ValueError, match="empty"):
        run_trial(dataset, output, trial())
    assert (output / "do-not-overwrite.txt").read_text() == "user data"
    manifest["dataset_sha256"] = "wrong"
    (dataset / "dataset_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="hash"):
        run_trial(dataset, tmp_path / "hash-fail", trial())
    assert not (tmp_path / "hash-fail").exists()


def test_partial_runner_cohort_is_rejected_not_filtered(tmp_path):
    dataset, frame, manifest = make_dataset(tmp_path)
    frame.iloc[1:].to_parquet(dataset / "dataset.parquet", index=False)
    manifest["dataset_sha256"] = hashlib.sha256((dataset / "dataset.parquet").read_bytes()).hexdigest()
    (dataset / "dataset_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="partial runner"):
        run_trial(dataset, tmp_path / "partial", trial())


@pytest.mark.parametrize("unsafe", [{"objective": "binary"}, {"num_threads": 64}, {"num_boost_round": 0}])
def test_parameter_contract_rejects_overrides(tmp_path, unsafe):
    dataset, _, _ = make_dataset(tmp_path)
    request = trial()
    request["params"].update(unsafe)
    with pytest.raises(ValueError):
        run_trial(dataset, tmp_path / "bad-params", request)


def test_numeric_missingness_is_not_erased_as_a_constant_feature():
    encoder = FoldEncoder(["observed_or_missing"], [])
    encoded = encoder.fit_transform(pd.DataFrame({"observed_or_missing": [0.0, 0.0, np.nan]}))
    assert encoded.columns.tolist() == ["observed_or_missing"]
    assert np.isnan(encoded.iloc[2, 0])


def test_future_targets_do_not_fit_first_fold_baseline_or_predictions(tmp_path):
    dataset, frame, manifest = make_dataset(tmp_path)
    first = run_trial(dataset, tmp_path / "first", trial())
    first_bundle = joblib.load(first["folds"][0]["artifact"])
    frame.loc[frame.race_date >= "20230101", "time_seconds"] *= 2
    frame.to_parquet(dataset / "dataset.parquet", index=False)
    manifest["dataset_sha256"] = hashlib.sha256((dataset / "dataset.parquet").read_bytes()).hexdigest()
    (dataset / "dataset_manifest.json").write_text(json.dumps(manifest))
    second = run_trial(dataset, tmp_path / "second", trial())
    second_bundle = joblib.load(second["folds"][0]["artifact"])
    assert first_bundle["baseline"] == second_bundle["baseline"]
    assert first_bundle["model"].model_to_string() == second_bundle["model"].model_to_string()
    assert first_bundle["encoder"].categories == second_bundle["encoder"].categories


def test_missing_provider_ids_do_not_block_complete_number_identified_cohorts(tmp_path):
    dataset, frame, manifest = make_dataset(tmp_path)
    frame.loc[(frame.race_date >= "20260101") & frame.horse_number.le(2), "horse_id"] = None
    frame.to_parquet(dataset / "dataset.parquet", index=False)
    manifest["dataset_sha256"] = hashlib.sha256((dataset / "dataset.parquet").read_bytes()).hexdigest()
    (dataset / "dataset_manifest.json").write_text(json.dumps(manifest))
    result = run_trial(dataset, tmp_path / "missing-ids", trial("M01", "final"))
    prediction = pd.read_parquet(result["artifacts"]["predictions"]["path"])
    assert prediction.horse_id.eq("").sum() == 4
    assert prediction.groupby("race_id").horse_number.nunique().eq(4).all()

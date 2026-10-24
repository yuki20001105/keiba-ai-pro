"""Synthetic-only tests; no production dataset, model or process is touched."""
from copy import deepcopy

import joblib
import numpy as np
import pandas as pd
import pytest

from experiments.common import file_sha256, json_sha256, read_json, write_json
from experiments.features import engineer_features
from experiments.search_features import derive_search_features
from prospective.variants import HistoryShrinkage, _evaluation, _rank_percentile, _settings, run_variant_trial


def fixture(tmp_path):
    rows = []
    for year in (2016, 2018, 2020, 2021, 2022, 2023, 2024, 2025, 2026):
        for day in (1, 2):
            for number in range(1, 5):
                rows.append({"race_id": f"{year}010101{day:02d}", "race_date": f"{year}010{day}",
                             "horse_id": f"horse{number}", "horse_number": number, "num_horses": 4,
                             "surface": "turf" if day == 1 else "dirt", "venue": "Tokyo", "distance": 1200,
                             "finish": number, "time_seconds": 68 + number, "last_3f_time": 34 + number,
                             "jockey_id": f"j{number}", "trainer_id": f"t{number}", "sire": f"s{number}", "damsire": f"d{number}",
                             "odds": number * 2.0, "popularity": number, "tansho_payout": 200.0 if number == 1 else np.nan})
    frame, manifest = engineer_features(pd.DataFrame(rows))
    derived = derive_search_features(frame)
    frame = pd.concat([frame, derived], axis=1)
    columns = manifest["feature_sets"]["M03"] + list(derived)
    manifest["feature_allowlist"] = columns
    manifest["feature_sets"]["M03"] = columns
    manifest["snapshot_sha256"] = "a" * 64
    manifest["source_contract"] = {"snapshot_sha256": "a" * 64}
    directory = tmp_path / "dataset"
    directory.mkdir()
    frame.to_parquet(directory / "dataset.parquet", index=False)
    manifest["dataset_sha256"] = file_sha256(directory / "dataset.parquet")
    write_json(directory / "dataset_manifest.json", manifest)
    base = {"id": "H04", "target": "rank_regression", "regression_loss": "huber", "validation_years": [2021, 2022],
            "train_start_year": 2016, "feature_columns": columns, "params": {"num_boost_round": 3, "num_leaves": 3,
            "min_data_in_leaf": 2}, "seed": 42, "bootstrap_samples": 0, "contribution_sample": 0, "save_model": False}
    trial = {"id": "V-fixture", "variant": "reference", "validation_years": [2021, 2022], "base_spec": base,
             "base_spec_sha256": json_sha256(base), "dataset_sha256": manifest["dataset_sha256"],
             "manifest_sha256": file_sha256(directory / "dataset_manifest.json"), "num_threads": 1,
             "bootstrap_samples": 0}
    return directory, frame, manifest, trial


def reference(tmp_path, frame, trial, role="h04", identifier="reference"):
    years = trial["validation_years"]
    evaluated = frame.loc[frame.race_date.str[:4].astype(int).isin(years)].copy().reset_index(drop=True)
    evaluated["expected_runner_count"] = evaluated.num_horses
    evaluated["fold_year"] = evaluated.race_date.str[:4].astype(int)
    evaluated["prediction"] = (5 - evaluated.horse_number).astype(float)
    if role == "speed":
        evaluated["prediction"] = evaluated.horse_number.astype(float) ** 2 + 100
    directory = tmp_path / identifier
    directory.mkdir()
    path = directory / "predictions.parquet"
    evaluated.to_parquet(path, index=False)
    target = "regression" if role == "speed" else "ranking" if role == "lambdarank" else "rank_regression"
    result = {"status": "completed", "target": target, "task": "speed_regression" if role == "speed" else "ranking",
              "loss": "lambdarank" if role == "lambdarank" else "huber", "validation_years": years,
              "source_contract": {"snapshot_sha256": "a" * 64},
              "artifacts": {"predictions": {"path": str(path), "sha256": file_sha256(path)}},
              "folds": [{"year": year, "contract": {"train_period": ["20160101", f"{year - 1}1231"],
                         "validation_used_for_fit_or_early_stopping": False, "trial": deepcopy(trial["base_spec"])}} for year in years]}
    write_json(directory / "result.json", result)
    return {"path": str(directory / "result.json"), "sha256": file_sha256(directory / "result.json")}, evaluated


@pytest.mark.parametrize("variant", ["history_shrink", "history_damped", "surface_split"])
def test_three_retrained_variants_preserve_complete_chronological_cohort_and_artifacts(tmp_path, variant):
    directory, frame, _, trial = fixture(tmp_path)
    trial["variant"] = variant
    result = run_variant_trial(directory, tmp_path / "output", trial)
    predictions = pd.read_parquet(result["artifacts"]["predictions"]["path"])
    assert len(predictions) == 16
    assert predictions.groupby("race_id").size().eq(4).all()
    assert predictions.train_end_date.lt(predictions.race_date).all()
    assert set(predictions.surface) == {"turf", "dirt"}
    assert result["evaluation"]["overall"]["rmse"] is None
    assert result["evaluation"]["overall"]["rmse_speed_mps"] is None
    assert result["evaluation"]["overall"]["normalized_rank_rmse"] >= 0
    assert result["evaluation"]["overall"]["win_roi_percent"] is None
    assert result["score_is_probability"] is False
    for fold in result["folds"]:
        assert fold["contract"]["validation_used_for_fit_or_early_stopping"] is False
        assert len(fold["learners"]) == (2 if variant == "surface_split" else 1)
        for learner in fold["learners"]:
            assert file_sha256(learner["artifact"]) == learner["sha256"]
            artifact = joblib.load(learner["artifact"])
            assert artifact["contract"]["base_spec"] == trial["base_spec"]
            if variant.startswith("history_"):
                assert artifact["history_transformer"] is not None
    assert read_json(tmp_path / "output/result.json") == result
    assert file_sha256(result["artifacts"]["predictions"]["path"]) == result["artifacts"]["predictions"]["sha256"]
    assert file_sha256(directory / "dataset.parquet") == trial["dataset_sha256"]


def test_history_prior_fit_never_reads_validation_labels_or_refits(tmp_path):
    _, frame, _, _ = fixture(tmp_path)
    train = frame.loc[frame.race_date.lt("20210101")].copy()
    validation = frame.loc[frame.race_date.ge("20210101")].copy()
    transformer = HistoryShrinkage(damped=True).fit(train)
    proof = deepcopy(transformer.contract())
    transformed = transformer.transform(validation)
    changed = validation.copy()
    changed["finish"], changed["time_seconds"], changed["odds"] = 99, 1, 999999
    again = transformer.transform(changed)
    for name in transformer.priors:
        pd.testing.assert_series_equal(transformed[name], again[name])
    assert transformer.contract() == proof
    assert transformed.safe_prior_starts.equals(validation.safe_prior_starts)
    assert transformed.safe_search_missing_jockey_history.equals(validation.safe_search_missing_jockey_history)
    recalculated = derive_search_features(transformed)
    pd.testing.assert_series_equal(transformed.safe_search_rel_past5_win_rate_rank, recalculated.safe_search_rel_past5_win_rate_rank)


def test_thin_history_shrinks_more_preserving_missing_evidence(tmp_path):
    _, frame, _, _ = fixture(tmp_path)
    train = frame.loc[frame.race_date.lt("20210101")].copy()
    transformer = HistoryShrinkage().fit(train)
    test = frame.iloc[:4].copy()
    test["safe_jockey_win_rate"] = [1.0, 1.0, np.nan, 1.0]
    test["safe_jockey_starts"] = [1, 100, 0, 10]
    test["safe_search_missing_jockey_history"] = [0, 0, 1, 0]
    output = transformer.transform(test)
    prior = transformer.priors["safe_jockey_win_rate"]["prior"]
    assert output.safe_jockey_win_rate.iloc[0] < output.safe_jockey_win_rate.iloc[1] < 1
    assert output.safe_jockey_win_rate.iloc[2] == pytest.approx(prior)
    assert output.safe_search_missing_jockey_history.tolist() == [0, 0, 1, 0]
    assert output.safe_jockey_starts.tolist() == [1, 100, 0, 10]


def test_missing_training_prior_stays_missing_instead_of_learning_from_validation(tmp_path):
    _, frame, _, _ = fixture(tmp_path)
    train = frame.iloc[:8].copy()
    train["safe_jockey_win_rate"] = np.nan
    transformer = HistoryShrinkage().fit(train)
    assert transformer.priors["safe_jockey_win_rate"]["prior"] is None
    assert transformer.transform(train).safe_jockey_win_rate.isna().all()


@pytest.mark.parametrize("variant,role", [("reference", None), ("ensemble_speed", "speed"), ("ensemble_lambdarank", "lambdarank")])
def test_frozen_reuse_and_fixed_percentile_ensembles(tmp_path, variant, role):
    directory, frame, _, trial = fixture(tmp_path)
    main, main_frame = reference(tmp_path, frame, trial)
    trial.update(variant=variant, reference_results={"h04": [main]})
    secondary_frame = None
    if role:
        secondary, secondary_frame = reference(tmp_path, frame, trial, role=role, identifier=role)
        trial["reference_results"][role] = [secondary]
    result = run_variant_trial(directory, tmp_path / "output", trial)
    predictions = pd.read_parquet(result["artifacts"]["predictions"]["path"])
    expected = main_frame.prediction if role is None else 0.5 * _rank_percentile(main_frame) + 0.5 * _rank_percentile(secondary_frame)
    np.testing.assert_allclose(predictions.prediction, expected)
    assert result["ensemble_weights"] == ([0.5, 0.5] if role else None)
    assert len(result["reference_provenance"]) == (2 if role else 1)
    assert not list((tmp_path / "output").glob("*.joblib"))


@pytest.mark.parametrize("mutation,expected_error", [
    ("partial", "incomplete"), ("future_training", "strict-out-of-fold"),
    ("wrong_spec", "frozen-h04"), ("wrong_snapshot", "snapshot-mismatch"),
    ("validation_fit", "strict-out-of-fold"), ("wrong_outcome", "metadata-mismatch"),
    ("wrong_year", "fold-proof-missing"), ("duplicate", "duplicate"),
])
def test_references_fail_closed_on_bad_cohort_lineage_or_chronology(tmp_path, mutation, expected_error):
    directory, frame, _, trial = fixture(tmp_path)
    descriptor, predicted = reference(tmp_path, frame, trial)
    result = read_json(descriptor["path"])
    if mutation == "partial":
        predicted = predicted.iloc[1:]
    elif mutation == "duplicate":
        predicted = pd.concat([predicted, predicted.iloc[:1]], ignore_index=True)
    elif mutation == "future_training":
        result["folds"][0]["contract"]["train_period"][1] = "20211231"
    elif mutation == "wrong_spec":
        result["folds"][0]["contract"]["trial"]["params"]["num_leaves"] = 7
    elif mutation == "wrong_snapshot":
        result["source_contract"]["snapshot_sha256"] = "b" * 64
    elif mutation == "validation_fit":
        result["folds"][0]["contract"]["validation_used_for_fit_or_early_stopping"] = True
    elif mutation == "wrong_outcome":
        predicted.loc[predicted.horse_number.eq(4), "finish"] = 3
    elif mutation == "wrong_year":
        result["folds"] = result["folds"][:1]
    artifact = result["artifacts"]["predictions"]
    predicted.to_parquet(artifact["path"], index=False)
    artifact["sha256"] = file_sha256(artifact["path"])
    write_json(descriptor["path"], result)
    descriptor["sha256"] = file_sha256(descriptor["path"])
    trial["reference_results"] = {"h04": [descriptor]}
    with pytest.raises(ValueError, match=expected_error):
        run_variant_trial(directory, tmp_path / "output", trial)


def test_reuse_rejects_changed_hashed_artifact(tmp_path):
    directory, frame, _, trial = fixture(tmp_path)
    descriptor, predictions = reference(tmp_path, frame, trial)
    trial["reference_results"] = {"h04": [descriptor]}
    predictions.prediction += 1
    predictions.to_parquet(tmp_path / "reference/predictions.parquet", index=False)
    with pytest.raises(ValueError, match="prediction-hash"):
        run_variant_trial(directory, tmp_path / "output", trial)


@pytest.mark.parametrize("mutation", ["mixed_years", "different_seed", "gpu", "changed_base", "confirmation_screening"])
def test_fixed_spec_and_period_contract(tmp_path, mutation):
    _, _, _, trial = fixture(tmp_path)
    if mutation == "mixed_years":
        trial["validation_years"] = [2022, 2023]
    elif mutation == "different_seed":
        trial["seed"] = 7
    elif mutation == "gpu":
        trial["device"] = "gpu"
    elif mutation == "changed_base":
        trial["base_spec"]["params"]["num_boost_round"] = 100
    else:
        trial["validation_years"] = [2023, 2024, 2025]
    with pytest.raises(ValueError):
        _settings(trial)


def test_known_reused_confirmation_and_date_bootstrap(tmp_path):
    directory, frame, _, trial = fixture(tmp_path)
    trial.update(validation_years=[2023, 2024, 2025], bootstrap_samples=10)
    descriptor, _ = reference(tmp_path, frame, trial)
    trial["reference_results"] = {"h04": [descriptor]}
    result = run_variant_trial(directory, tmp_path / "output", trial)
    assert result["phase"] == "confirmation"
    assert result["known_reused_confirmation_and_retrospective"] is True
    assert result["evaluation"]["day_bootstrap_ci"]["evaluated"] is True
    assert result["evaluation"]["day_bootstrap_ci"]["unit"] == "race_date"
    assert result["evaluation"]["overall"]["rank_correlation"] == pytest.approx(1)
    assert result["evaluation"]["overall"]["normalized_rank_rmse"] == pytest.approx(0)


def test_cancellation_propagates_during_fitting(tmp_path):
    directory, _, _, trial = fixture(tmp_path)
    trial["variant"] = "surface_split"
    def cancel(stage, pct):
        if "round" in stage:
            raise RuntimeError("cancelled-fixture")
    with pytest.raises(RuntimeError, match="cancelled-fixture"):
        run_variant_trial(directory, tmp_path / "output", trial, cancel)
    assert not (tmp_path / "output/result.json").exists()


def test_isolated_output_and_dataset_binding(tmp_path):
    directory, frame, _, trial = fixture(tmp_path)
    with pytest.raises(ValueError, match="isolated"):
        run_variant_trial(directory, directory, trial)
    trial["dataset_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="dataset-hash"):
        run_variant_trial(directory, tmp_path / "output", trial)


def test_normalized_rank_error_is_scale_invariant_and_not_speed_error(tmp_path):
    _, frame, _, trial = fixture(tmp_path)
    _, evaluated = reference(tmp_path, frame, trial)
    direct = _evaluation(evaluated, 0, 42, lambda *args: None)
    evaluated.prediction = np.exp(evaluated.prediction)
    transformed = _evaluation(evaluated, 0, 42, lambda *args: None)
    assert direct["overall"] == transformed["overall"]
    assert transformed["overall"]["normalized_rank_rmse"] == 0
    assert transformed["overall"]["rmse_speed_mps"] is None

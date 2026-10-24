import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from market_edge.data import PHASES, development_frame, prepare_development


def frame():
    rows = []
    dates = ["20210105", "20210620", "20210710", "20210919", "20211003", "20211226", "20220105", "20221225"]
    for index, date in enumerate(dates, 1):
        rid = date[:4] + f"0501{index:02d}01"
        for number in (1, 2, 3):
            rows.append({"race_id": rid, "race_date": date, "horse_id": f"horse{number}", "horse_number": number,
                         "surface": "turf", "finish": number, "popularity": number, "odds": 2 * number,
                         "tansho_payout": 200. if number == 1 else np.nan, "expected_runner_count": 3,
                         "prediction": (4 - number) / 4 if index != 1 else number / 4, "fold_year": int(date[:4])})
    return pd.DataFrame(rows)


def folds(value):
    return [{"year": y, "train_period": ["20160101", f"{y-1}1231"],
             "validation_rows": int(value.fold_year.eq(y).sum()),
             "validation_races": int(value.loc[value.fold_year.eq(y), "race_id"].nunique())}
            for y in (2021, 2022)]


def prepare(value=None, **kwargs):
    data = frame() if value is None else value
    return development_frame(data, folds(data), **kwargs)


def test_chronological_roles_and_calibration_are_strictly_separate():
    original = frame(); output, calibration, audit = prepare(original)
    pd.testing.assert_frame_equal(original, frame())
    assert set(output.phase) == set(PHASES)
    assert calibration["fit_date_to"] == "20210620" and calibration["fit_years"] == [2021]
    assert output.loc[output.phase.eq("calibration_seed"), "h04_probability"].isna().all()
    later = output.loc[~output.phase.eq("calibration_seed")]
    assert later.h04_probability.notna().all()
    assert np.allclose(later.groupby("race_id").h04_probability.sum(), 1)
    assert (later.fold_train_date_to < later.race_date).all()
    assert audit["phases"]["meta_fit"]["date_from"] == "20210710"
    assert audit["phases"]["development"]["date_from"] == "20211003"


def test_later_labels_payouts_or_odds_do_not_fit_seed_temperature():
    baseline, cal, _ = prepare()
    changed = frame(); mask = changed.race_date.gt("20210630")
    changed.loc[mask, "finish"] = 4 - changed.loc[mask, "finish"]
    changed.loc[mask, "tansho_payout"] = 99990
    changed.loc[mask, "odds"] *= 3
    output, altered, _ = prepare(changed)
    assert cal == altered
    np.testing.assert_allclose(output.h04_probability, baseline.h04_probability, equal_nan=True)


def test_market_probabilities_and_predeclared_differences():
    output, _, _ = prepare()
    assert np.allclose(output.groupby("race_id").normalized_market_probability.sum(), 1)
    np.testing.assert_allclose(output.implied_probability, 1 / output.odds)
    np.testing.assert_allclose(output.log_odds, np.log(output.odds))
    np.testing.assert_allclose(output.rank_difference, output.h04_rank - output.market_rank)
    np.testing.assert_allclose(output.probability_difference, output.h04_probability - output.normalized_market_probability, equal_nan=True)
    assert output.groupby("race_id").is_win.sum().eq(1).all()


@pytest.mark.parametrize("mutation", ["future", "wrong_fold", "duplicate_number", "duplicate_horse", "count", "nonfinite_score", "nar", "missing_column"])
def test_structural_or_chronological_failure_aborts(mutation):
    data = frame()
    if mutation == "future": data.loc[:2, "race_date"] = "20230101"
    if mutation == "wrong_fold": data.loc[0, "fold_year"] = 2022
    if mutation == "duplicate_number": data.loc[1, "horse_number"] = 1
    if mutation == "duplicate_horse": data.loc[1, "horse_id"] = "horse1"
    if mutation == "count": data.loc[0, "expected_runner_count"] = 2
    if mutation == "nonfinite_score": data.loc[0, "prediction"] = np.inf
    if mutation == "nar": data.loc[:2, "race_id"] = "202150010101"
    if mutation == "missing_column": data = data.drop(columns="prediction")
    with pytest.raises(ValueError): prepare(data)


def test_cannot_apply_serving_2025_trained_model_to_2021():
    data = frame(); evidence = folds(data); evidence[0]["train_period"][1] = "20251231"
    with pytest.raises(ValueError, match="prior-year"):
        development_frame(data, evidence)


def test_unknown_horse_id_is_not_fabricated_or_conflated():
    data = frame(); data.loc[:2, "horse_id"] = None
    output, _, _ = prepare(data)
    assert output.loc[:2, "horse_id"].isna().all()


@pytest.mark.parametrize("invalid", [np.nan, 0, .9, np.inf, "--"])
def test_bad_odds_excludes_entire_race(invalid):
    data = frame(); data["odds"] = data.odds.astype(object); data.loc[0, "odds"] = invalid
    rid = data.loc[0, "race_id"]; output, _, audit = prepare(data)
    assert rid not in set(output.race_id)
    assert audit["incomplete_market_excluded_race_ids"] == [rid]
    assert len(output) == len(data) - 3


@pytest.mark.parametrize("mutation", ["dead_heat", "missing_finish"])
def test_partial_or_dead_heat_outcomes_exclude_whole_race(mutation):
    data = frame(); data["finish"] = data.finish.astype(float)
    data.loc[1, "finish"] = 1 if mutation == "dead_heat" else np.nan
    rid = data.loc[0, "race_id"]; output, _, audit = prepare(data)
    assert rid not in set(output.race_id) and audit["outcome_excluded_race_ids"] == [rid]


@pytest.mark.parametrize("payout", [np.nan, 0, -1, 101, np.inf])
def test_unknown_or_invalid_payout_remains_unknown_not_zero(payout):
    data = frame(); data.loc[0, "tansho_payout"] = payout
    output, _, audit = prepare(data); first = output.race_id.eq(data.loc[0, "race_id"])
    assert len(output) == len(data) and not output.loc[first, "payout_confirmed_race"].any()
    assert output.loc[first, "tansho_payout"].isna().all()
    assert audit["payout_unconfirmed_races"] == 1


def test_reference_column_is_explicitly_contaminated_not_primary():
    baseline, calibrator, _ = prepare()
    calibrator = {**calibrator, "fit_date_from": "20210105", "fit_date_to": "20221225",
                  "global": {**calibrator["global"], "temperature": .1477}}
    output, _, audit = prepare(reference_calibrator=calibrator)
    assert output.h04_probability_fixed_reference.notna().all()
    np.testing.assert_allclose(output.h04_probability, baseline.h04_probability, equal_nan=True)
    assert audit["fixed_calibration_reference"]["allowed_for_validation_gates"] is False
    calibrator["fit_date_to"] = "20231231"
    with pytest.raises(ValueError, match="post-2022"):
        prepare(reference_calibrator=calibrator)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fixture(tmp_path):
    root = tmp_path / "source"; trial = root / "trials/winner-search"; artifacts = trial / "artifacts"
    artifacts.mkdir(parents=True)
    data = frame(); predictions = artifacts / "predictions.parquet"; data.to_parquet(predictions, index=False)
    features = [f"safe_feature_{n}" for n in range(99)]
    proof = folds(data)
    for fold in proof:
        model = artifacts / f"fold_{fold['year']}.joblib"; model.write_bytes(b"test-hash-only-no-deserialization")
        fold.update(artifact=str(model), sha256=digest(model), contract={
            "train_period": fold["train_period"], "evaluation_year": fold["year"],
            "validation_used_for_fit_or_early_stopping": False, "market_features": False,
            "feature_columns": features, "dataset_sha256": "a" * 64})
    result = {"status": "completed", "validation_years": [2021, 2022], "target": "rank_regression",
              "task": "ranking", "loss": "huber", "feature_columns": features, "folds": proof,
              "dataset_sha256": "a" * 64, "manifest_sha256": "b" * 64,
              "source_contract": {"source_contract": {"history_boundary": "source_race_date < target_race_date"}},
              "artifacts": {"predictions": {"path": str(predictions), "sha256": digest(predictions)}}}
    (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (trial / "artifact_manifest.json").write_text(json.dumps({"result.json": digest(trial / "result.json")}), encoding="utf-8")
    return root


def test_preparation_outputs_are_exclusive_hash_bound_and_do_not_execute_models(tmp_path):
    source = source_fixture(tmp_path); output = tmp_path / "isolated-research"
    output.mkdir(); (output / "experiment_plan.json").write_text("{}")
    result = prepare_development(source, output)
    assert result["status"] == "completed" and result["track"] == "B"
    assert result["artifact"]["sha256"] == digest(output / "development.parquet")
    assert result["seed_calibrator"]["sha256"] == digest(output / "seed-calibrator.json")
    assert result["track_a_modified"] is False and result["post_2022_outcomes_opened"] is False
    assert result["h04_configuration_reused_known_2021_2022_selection"] is True
    assert result["historical_odds_timestamp_verified"] is False
    assert len(pd.read_parquet(output / "development.parquet")) == 24
    with pytest.raises(FileExistsError): prepare_development(source, output)
    with pytest.raises(ValueError, match="isolated"): prepare_development(source, source / "new")


@pytest.mark.parametrize("target", ["result.json", "artifacts/predictions.parquet", "artifacts/fold_2021.joblib"])
def test_changed_provenance_or_model_bytes_fail_before_output(tmp_path, target):
    source = source_fixture(tmp_path); path = source / "trials/winner-search" / target
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="SHA256"):
        prepare_development(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()

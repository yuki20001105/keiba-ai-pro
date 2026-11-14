from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest


def research():
    return importlib.import_module("betting.research_race_selection")


def fixture_frame():
    rows = []
    for year in range(2021, 2025):
        for month in (1, 7):
            for day in range(1, 7):
                winner = 1 if day % 2 else 2
                others = [number for number in (1, 2, 3) if number != winner]
                for number in (1, 2, 3):
                    odds = {1: 3.0, 2: 12.0, 3: 40.0}[number]
                    rows.append({
                        "race_id": f"{year}-{month}-{day}",
                        "race_date": f"{year}{month:02d}{day:02d}",
                        "horse_number": number,
                        "prediction": 5.0 + day / 10 if number == winner else 3.0 - number,
                        "finish": 1 if number == winner else 2 + others.index(number),
                        "odds": odds,
                        "popularity": number,
                        "tansho_payout": odds * 100 if number == winner else np.nan,
                        "expected_runner_count": 3,
                        "fold_train_date_to": f"{year - 1}1231",
                        "distance": 1200 if day % 2 else 1800,
                        "surface": "turf",
                        "venue": "fixture-course",
                    })
    return pd.DataFrame(rows)


def evaluate(frame, output, **options):
    return research().optimize_race_selection(
        frame, output, model_id="fixture-oof",
        min_bets=2, min_dates=2, min_hits=1, bootstrap_samples=100, **options,
    )


def test_future_outcomes_and_markets_cannot_change_frozen_model_or_policy(tmp_path):
    frame = fixture_frame()
    before = evaluate(frame, tmp_path / "before")
    changed = frame.copy()
    future = changed["race_date"].ge("20230101")
    changed.loc[future, "finish"] = 4 - changed.loc[future, "finish"]
    changed.loc[future, "prediction"] *= -100
    changed.loc[future, "odds"] *= 100
    changed.loc[future, "tansho_payout"] = np.where(
        changed.loc[future, "finish"].eq(1), 99900, np.nan,
    )
    after = evaluate(changed, tmp_path / "after")
    assert before["frozen_policy_sha256"] == after["frozen_policy_sha256"]
    assert before["probability_model"]["sha256"] == after["probability_model"]["sha256"]
    assert before["source_predictions_sha256"] != after["source_predictions_sha256"]
    assert before["probability_model"]["fitted_through"] < "20220701"
    assert before["selection_period"]["date_to"] == "20221231"


def test_model_inference_ignores_outcomes_and_market_values(tmp_path):
    report = evaluate(fixture_frame(), tmp_path / "run")
    path = tmp_path / "run" / "probability_model.joblib"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == report["probability_model"]["sha256"]
    model = joblib.load(path)
    frame = fixture_frame().query("race_date >= '20230101'").copy()
    probabilities = model.predict_probabilities(frame)
    changed = frame.copy()
    changed[["finish", "odds", "popularity", "tansho_payout"]] = 999
    np.testing.assert_array_equal(probabilities, model.predict_probabilities(changed))
    np.testing.assert_allclose(
        pd.Series(probabilities, index=frame.index).groupby(frame["race_id"]).sum(), 1,
    )
    with pytest.raises(ValueError, match="strictly after"):
        model.predict_probabilities(fixture_frame().query("race_date < '20220701'"))


def test_saved_model_rejects_a_different_feature_implementation(tmp_path):
    evaluate(fixture_frame(), tmp_path / "run")
    model = joblib.load(tmp_path / "run" / "probability_model.joblib")
    model.feature_source_sha256 = "0" * 64
    with pytest.raises(ValueError, match="feature-source-mismatch"):
        model.predict_probabilities(fixture_frame().query("race_date >= '20230101'"))


def test_whitespace_ids_do_not_silently_drop_course_context(tmp_path):
    frame = fixture_frame()
    frame["race_id"] = " " + frame["race_id"] + " "
    report = evaluate(frame, tmp_path / "run")
    assert report["input_audit"]["race_count"] == frame["race_id"].nunique()
    assert len(report["evaluation"]) == 2


def test_policies_are_alternatives_and_never_an_executable_purchase(tmp_path):
    report = evaluate(fixture_frame(), tmp_path / "run")
    assert set(report["selection"]) == {"steady", "longshot"}
    assert report["automatic_purchase"] is False
    assert report["production_rule_activated"] is False
    assert report["live_trading_reproduction"] is False
    assert report["unseen_period_claimed"] is False
    decisions = pd.read_parquet(tmp_path / "run" / "race_decisions.parquet")
    assert not decisions.duplicated(["race_id", "profile"]).any()
    assert decisions.groupby("race_id").size().eq(2).all()
    assert decisions["purchase_eligible"].eq(False).all()
    assert decisions["simulated_stake_yen"].isin([0, 100]).all()
    for profile in ("steady", "longshot"):
        tickets = decisions.loc[
            decisions["profile"].eq(profile) & decisions["simulated_stake_yen"].gt(0),
        ]
        assert not tickets["race_id"].duplicated().any()
    with pytest.raises(FileExistsError):
        evaluate(fixture_frame(), tmp_path / "run")


def test_insufficient_evidence_produces_skip_not_forced_bets(tmp_path):
    report = research().optimize_race_selection(
        fixture_frame(), tmp_path / "run", model_id="fixture",
        min_bets=10000, bootstrap_samples=100,
    )
    assert all(not selection["admissible"] for selection in report["selection"].values())
    decisions = pd.read_parquet(tmp_path / "run" / "race_decisions.parquet")
    assert decisions["simulated_action"].eq("skip").all()
    assert decisions["simulated_stake_yen"].eq(0).all()


def test_unverified_winner_payout_is_not_replaced_by_odds(tmp_path):
    frame = fixture_frame()
    frame.loc[frame["race_date"].eq("20230101") & frame["finish"].eq(1), "tansho_payout"] = np.nan
    report = evaluate(frame, tmp_path / "run")
    baseline = report["evaluation"]["2023"]["baselines"]["base_top1"]
    assert baseline["gross_return_rate_percent"] is None
    assert baseline["date_bootstrap_net_roi_95_ci"]["lower"] is None
    assert baseline["payout_missing_races"] == 1


@pytest.mark.parametrize("kind", [
    "no_train_boundary", "future_training", "incomplete_cohort", "inconsistent_distance",
])
def test_invalid_prediction_or_context_proof_is_rejected_before_writing(tmp_path, kind):
    frame = fixture_frame()
    if kind == "no_train_boundary":
        frame = frame.drop(columns="fold_train_date_to")
    elif kind == "future_training":
        frame.loc[0, "fold_train_date_to"] = "20230101"
    elif kind == "incomplete_cohort":
        frame = frame.iloc[1:]
    else:
        frame.loc[0, "distance"] = 9999
    with pytest.raises(ValueError):
        evaluate(frame, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_production_model_and_data_directories_are_not_outputs():
    module = research()
    for folder in (module.REPO_ROOT / "python-api" / "models", module.REPO_ROOT / "keiba" / "data"):
        with pytest.raises(ValueError, match="isolated"):
            evaluate(fixture_frame(), folder / "race-selection-must-not-create")


def test_source_prediction_hash_is_checked_before_parquet_read(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    parquet = root / "predictions.parquet"
    parquet.write_bytes(b"not parquet")
    frozen = {
        "model_id": "fixture",
        "prediction_provenance": {
            "out_of_time_folds": [{
                "artifact": {"path": str(parquet), "sha256": "a" * 64},
                "folds": [{"year": 2021, "train_period": ["20160101", "20201231"]}],
            }],
        },
    }
    (root / "betting").mkdir()
    text = json.dumps(frozen)
    (root / "betting" / "frozen_rule.json").write_text(text, encoding="utf-8")
    (root / "betting" / "frozen_rule.sha256").write_text(
        hashlib.sha256(text.encode()).hexdigest(), encoding="utf-8",
    )
    with pytest.raises(ValueError, match="sha256"):
        research().load_frozen_oof(root)


def test_frozen_loader_preserves_cohorts_and_recorded_training_boundaries(tmp_path):
    root = tmp_path / "source"
    (root / "betting").mkdir(parents=True)
    (root / "dataset").mkdir()
    frame = fixture_frame()
    parquet = root / "predictions.parquet"
    predictions = frame.drop(columns=["distance", "venue"]).copy()
    predictions["fold_year"] = predictions["race_date"].str[:4].astype(int)
    predictions.to_parquet(parquet, index=False)
    frozen = {
        "model_id": "fixture",
        "prediction_provenance": {
            "out_of_time_folds": [{
                "artifact": {"path": str(parquet), "sha256": hashlib.sha256(parquet.read_bytes()).hexdigest()},
                "folds": [
                    {"year": year, "train_period": ["20160101", f"{year - 1}1231"]}
                    for year in range(2021, 2025)
                ],
            }],
        },
    }
    text = json.dumps(frozen)
    (root / "betting" / "frozen_rule.json").write_text(text, encoding="utf-8")
    (root / "betting" / "frozen_rule.sha256").write_text(
        hashlib.sha256(text.encode()).hexdigest(), encoding="utf-8",
    )
    dataset = root / "dataset" / "dataset.parquet"
    frame[["race_id", "horse_number", "distance", "venue"]].to_parquet(dataset, index=False)
    (root / "dataset" / "dataset_manifest.json").write_text(
        json.dumps({"dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest()}),
        encoding="utf-8",
    )
    loaded, provenance = research().load_frozen_oof(root)
    assert len(loaded) == len(frame)
    assert loaded["fold_train_date_to"].eq(frame["fold_train_date_to"]).all()
    assert loaded["distance"].eq(frame["distance"]).all()
    assert provenance["model_id"] == "fixture"

    predictions["fold_train_date_to"] = "20990101"
    predictions.to_parquet(parquet, index=False)
    frozen["prediction_provenance"]["out_of_time_folds"][0]["artifact"]["sha256"] = hashlib.sha256(parquet.read_bytes()).hexdigest()
    text = json.dumps(frozen)
    (root / "betting" / "frozen_rule.json").write_text(text, encoding="utf-8")
    (root / "betting" / "frozen_rule.sha256").write_text(hashlib.sha256(text.encode()).hexdigest(), encoding="utf-8")
    with pytest.raises(ValueError, match="boundary"):
        research().load_frozen_oof(root)

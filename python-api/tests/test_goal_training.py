from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from experiments.common import file_sha256, write_json
from experiments.goal_training import run_goal_trial


def make_dataset(root):
    dataset = root / "dataset"
    dataset.mkdir()
    rows = []
    for year in range(2020, 2025):
        for day in (1, 2):
            for runner in range(1, 5):
                rows.append({
                    "race_date": f"{year}010{day}", "race_id": f"{year}05010{day}01",
                    "horse_id": f"h{runner}", "horse_number": runner, "finish": runner,
                    "time_seconds": 70 + runner * 2 + day + (year - 2020) / 10,
                    "distance": 1200 if day == 1 else 1600, "surface": "turf",
                    "venue": ("東京" if year == 2020 else "京都") if year < 2022 else "future_category",
                    "odds": float(runner * 2), "popularity": runner,
                    "tansho_payout": 220.0 if runner == 1 else np.nan, "num_horses": 4,
                    "history": np.nan if runner == 4 else float(runner + day),
                    "validation_only": np.nan if year < 2022 else float(runner),
                    "unused_unsafe_payload": "must not load",
                })
    frame = pd.DataFrame(rows)
    features = ["distance", "venue", "horse_number", "history", "validation_only"]
    manifest = {
        "feature_sets": {"M03": features}, "categorical_features": ["venue"],
        "row_count": len(frame), "race_count": frame.race_id.nunique(),
    }
    save_dataset(dataset, frame, manifest)
    return dataset, frame, manifest


def save_dataset(dataset, frame, manifest):
    frame.to_parquet(dataset / "dataset.parquet", index=False)
    manifest["dataset_sha256"] = file_sha256(dataset / "dataset.parquet")
    write_json(dataset / "dataset_manifest.json", manifest)


def trial(target="speed_deviation", **overrides):
    return {
        "id": "tiny", "target": target, "validation_years": [2022, 2023],
        "train_start_year": 2020,
        "feature_columns": ["distance", "venue", "horse_number", "history", "validation_only"],
        "params": {"num_boost_round": 3, "num_leaves": 3, "min_data_in_leaf": 2},
        "bootstrap_samples": 0, **overrides,
    }


@pytest.mark.parametrize("target", ["speed_deviation", "rank", "win"])
def test_real_annual_training_is_train_only_persistent_and_hashed(tmp_path, target):
    dataset, frame, _ = make_dataset(tmp_path)
    updates = []
    request = trial(target, experiment_contract_sha256="a" * 64, proposal_id="proposal-1")
    result = run_goal_trial(dataset, tmp_path / target, request, lambda stage, pct: updates.append((stage, pct)))
    assert result["status"] == "completed"
    assert result["trial_id"] == "tiny"
    assert result["phase"] == "development"
    assert result["target"] == target
    assert result["task"] == ("speed_regression" if target == "speed_deviation" else "ranking")
    assert result["feature_columns"] == request["feature_columns"]
    assert result["validation_years"] == [2022, 2023]
    assert result["evaluation_label"] == "development_known_exposed"
    assert result["phase3n_financial_evidence_approved"] is False
    assert result["observed_timestamp_assurance"] is False
    assert result["promotion_approved"] is False
    assert result["calibration_status"] == "not_evaluated"
    assert result["dataset_sha256"] == file_sha256(dataset / "dataset.parquet")
    assert result["manifest_sha256"] == file_sha256(dataset / "dataset_manifest.json")
    assert result["evaluation"]["overall"]["race_count"] == 4
    assert set(result["evaluation"]["by_year"]) == {"2022", "2023"}
    assert result["evaluation"]["definitions"]["score_is_probability"] is False
    assert result["evaluation"]["definitions"]["bet_rule"].startswith("one 100-yen")
    for fold in result["folds"]:
        bundle = joblib.load(fold["artifact"])
        assert fold["sha256"] == file_sha256(Path(fold["artifact"]))
        assert set(bundle) == {"model", "encoder", "feature_columns", "baseline", "target",
                               "probability_transform", "contract"}
        contract = bundle["contract"]
        assert contract["train_period"] == ["20200101", f"{fold['year'] - 1}1231"]
        assert contract["validation_used_for_fit_or_early_stopping"] is False
        assert contract["trial"]["seed"] == 42
        assert contract["params"]["num_threads"] == 1
        assert contract["params"]["device_type"] == "cpu"
        if target == "speed_deviation":
            assert bundle["baseline"]["training_sample_count"] == len(frame[frame.race_date < f"{fold['year']}0101"])
        else:
            assert bundle["baseline"] is None
        if fold["year"] == 2022:
            assert bundle["encoder"].categories["venue"] == ["京都", "東京"]
            assert bundle["encoder"].dropped_columns["validation_only"] == "all_missing_in_training"
            transformed = bundle["encoder"].transform(frame[frame.race_date.eq("20220101")])
            assert np.isnan(transformed.loc[frame.horse_number.eq(4), "history"]).all()
            assert transformed["venue"].isna().all()
    assert set(result["artifacts"]) == {"predictions", "race_records", "contributions"}
    for artifact in result["artifacts"].values():
        assert artifact["sha256"] == file_sha256(Path(artifact["path"]))
    predicted = pd.read_parquet(result["artifacts"]["predictions"]["path"])
    assert set(predicted.race_date.str[:4]) == {"2022", "2023"}
    assert np.isfinite(predicted.prediction).all()
    assert predicted.groupby("race_id").size().eq(4).all()
    contributions = json.loads(Path(result["artifacts"]["contributions"]["path"]).read_text())
    assert contributions["training_only"] is True
    assert all(r["sample_rows"] <= 128 and r["sample_period"][1] < f"{r['fold_year']}0101"
               for r in contributions["rows"])
    assert contributions["rows"]
    if target == "win":
        assert predicted.win_probability.between(0, 1).all()
        np.testing.assert_allclose(predicted.groupby("race_id").win_probability.sum(), 1)
        metrics = result["evaluation"]["probability_metrics"]
        assert all(np.isfinite(metrics[key]) for key in ("auc", "log_loss", "brier", "ece"))
        assert metrics["separate_calibration_applied"] is False
        assert metrics["calibration_status"] == "not_evaluated"
        assert metrics["probability_source"] == "race_normalized_sigmoid"
        assert set(metrics["by_year"]) == {"2022", "2023"}
        assert joblib.load(result["folds"][0]["artifact"])["model"].params["objective"] == "binary"
    else:
        assert "win_probability" not in predicted
        assert "probability_metrics" not in result["evaluation"]
    assert json.loads((tmp_path / target / "result.json").read_text()) == result
    assert updates[-1] == ("isolated goal trial complete", 100)
    assert set(p.name for p in dataset.iterdir()) == {"dataset.parquet", "dataset_manifest.json"}


@pytest.mark.parametrize("target", ["speed_deviation", "rank", "win"])
def test_future_targets_and_features_do_not_fit_earlier_fold(tmp_path, target):
    dataset, frame, manifest = make_dataset(tmp_path)
    request = trial(target, validation_years=[2022])
    first = run_goal_trial(dataset, tmp_path / "first", request)
    future = frame.race_date.ge("20220101")
    frame.loc[future, "time_seconds"] *= 2
    frame.loc[future, "finish"] = 5 - frame.loc[future, "finish"]
    frame.loc[future, "history"] = 1_000_000
    frame.loc[future, "venue"] = "different_future_category"
    # Beyond the requested end, even invalid cohorts/labels must not be loaded.
    frame.loc[frame.race_date.ge("20230101"), "finish"] = np.nan
    save_dataset(dataset, frame, manifest)
    second = run_goal_trial(dataset, tmp_path / "second", request)
    a, b = [joblib.load(r["folds"][0]["artifact"]) for r in (first, second)]
    assert a["baseline"] == b["baseline"]
    assert a["encoder"].categories == b["encoder"].categories
    assert a["model"].model_to_string() == b["model"].model_to_string()
    assert first["dataset_sha256"] != second["dataset_sha256"]


def test_parquet_projection_and_filter_and_repeatability(tmp_path, monkeypatch):
    dataset, _, _ = make_dataset(tmp_path)
    original, calls = pd.read_parquet, []

    def read(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", read)
    first = run_goal_trial(dataset, tmp_path / "first", trial())
    assert calls[0]["filters"] == [("race_date", ">=", "20200101"), ("race_date", "<=", "20231231")]
    assert "unused_unsafe_payload" not in calls[0]["columns"]
    second = run_goal_trial(dataset, tmp_path / "second", trial())
    assert first["evaluation"] == second["evaluation"]
    for key in first["artifacts"]:
        assert first["artifacts"][key]["sha256"] == second["artifacts"][key]["sha256"]


@pytest.mark.parametrize("override", [
    {"target": "place"}, {"validation_years": [2023, 2022]}, {"validation_years": [2022, 2022]},
    {"validation_years": [2016]}, {"validation_years": [2101]}, {"validation_years": [True]},
    {"train_start_year": 2015}, {"train_start_year": 2022}, {"phase": "final"},
    {"device": "gpu"}, {"num_threads": 5}, {"num_threads": True}, {"seed": 1.2},
    {"bootstrap_samples": 1001}, {"bootstrap_samples": -1}, {"bootstrap_samples": True},
    {"save_model": False}, {"ranking_loss": "rank_xendcg"}, {"candidate_id": "M05"},
    {"feature_columns": ["odds"]}, {"feature_columns": ["history", "history"]},
    {"params": {"objective": "binary"}}, {"params": {"device_type": "gpu"}},
    {"params": {"num_threads": 4}}, {"params": {"num_boost_round": 0}},
    {"params": {"learning_rate": np.nan}}, {"params": {"feature_fraction": 0}},
    {"params": {"lambda_l2": -1}}, {"params": {"num_leaves": 1}},
    {"params": {"max_depth": 0}}, {"params": {"extra_trees": 1}},
])
def test_unsafe_or_unknown_trial_fields_fail_before_training(tmp_path, override):
    dataset, _, _ = make_dataset(tmp_path)
    with pytest.raises(ValueError):
        run_goal_trial(dataset, tmp_path / "bad", trial(**override))
    assert not (tmp_path / "bad" / "result.json").exists()
    assert not list(tmp_path.glob("bad\\*.joblib"))


@pytest.mark.parametrize("column", ["finish", "odds", "prev_race_finish", "market_entropy"])
def test_poisoned_manifest_cannot_authorize_unsafe_features(tmp_path, column):
    dataset, _, manifest = make_dataset(tmp_path)
    manifest["feature_sets"]["M03"].append(column)
    write_json(dataset / "dataset_manifest.json", manifest)
    with pytest.raises(ValueError, match="unsafe"):
        run_goal_trial(dataset, tmp_path / "unsafe", trial(feature_columns=[column]))


def test_integrity_isolation_and_complete_cohorts_fail_closed(tmp_path):
    dataset, frame, manifest = make_dataset(tmp_path)
    for output in (dataset, dataset / "nested", Path(__file__).resolve().parents[1] / "models" / "goal"):
        with pytest.raises(ValueError, match="separate"):
            run_goal_trial(dataset, output, trial())
    output = tmp_path / "occupied"
    output.mkdir()
    sentinel = output / "sentinel"
    sentinel.write_text("preserve")
    with pytest.raises(ValueError, match="empty"):
        run_goal_trial(dataset, output, trial())
    assert sentinel.read_text() == "preserve"
    manifest["dataset_sha256"] = "wrong"
    write_json(dataset / "dataset_manifest.json", manifest)
    with pytest.raises(ValueError, match="hash"):
        run_goal_trial(dataset, tmp_path / "hash", trial())
    save_dataset(dataset, frame.iloc[1:], manifest)
    with pytest.raises(ValueError, match="partial runner"):
        run_goal_trial(dataset, tmp_path / "partial", trial())
    frame["finish"] = 1
    save_dataset(dataset, frame, manifest)
    with pytest.raises(ValueError, match="both classes"):
        run_goal_trial(dataset, tmp_path / "one-class", trial("win"))


def test_callback_cancellation_and_source_changes_never_mark_completed(tmp_path):
    dataset, _, manifest = make_dataset(tmp_path)

    def cancel(stage, pct):
        if "training round" in stage:
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        run_goal_trial(dataset, tmp_path / "cancelled", trial(), cancel)
    assert not (tmp_path / "cancelled" / "result.json").exists()
    assert not list((tmp_path / "cancelled").glob("*.joblib"))

    def mutate(stage, pct):
        if pct == 100:
            manifest["mutated"] = True
            write_json(dataset / "dataset_manifest.json", manifest)

    with pytest.raises(ValueError, match="changed during"):
        run_goal_trial(dataset, tmp_path / "mutated", trial(), mutate)
    assert not (tmp_path / "mutated" / "result.json").exists()


def test_nonfinite_predictions_fail_closed(tmp_path, monkeypatch):
    import lightgbm as lgb

    dataset, _, _ = make_dataset(tmp_path)
    monkeypatch.setattr(lgb.Booster, "predict", lambda self, data, **kwargs: np.full(len(data), np.nan))
    with pytest.raises(ValueError, match="finite predictions"):
        run_goal_trial(dataset, tmp_path / "nan", trial())
    assert not (tmp_path / "nan" / "result.json").exists()


def test_constant_race_scores_contribute_zero_instead_of_disappearing(tmp_path, monkeypatch):
    import lightgbm as lgb

    dataset, _, _ = make_dataset(tmp_path)
    original = lgb.Booster.predict

    def predict(model, data, **kwargs):
        if kwargs.get("pred_contrib"):
            return original(model, data, **kwargs)
        return np.array([0.0] * 4 + [-1.0, -2.0, -3.0, -4.0])

    monkeypatch.setattr(lgb.Booster, "predict", predict)
    result = run_goal_trial(dataset, tmp_path / "constant", trial("rank"))
    records = pd.read_parquet(result["artifacts"]["race_records"]["path"])
    assert sorted(records.rank_correlation.tolist()) == pytest.approx([0, 0, 1, 1])
    for evaluation in [result["evaluation"]] + [fold["evaluation"] for fold in result["folds"]]:
        assert evaluation["overall"]["rank_correlation"] == pytest.approx(0.5)
        assert evaluation["overall"]["rank_correlation_races"] == evaluation["overall"]["race_count"]
        assert "constant predictions contribute 0.0" in evaluation["definitions"]["rank_correlation"]

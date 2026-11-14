from __future__ import annotations

from importlib import import_module

import numpy as np
import pandas as pd
import pytest

from training import db_context_features as context


def module():
    return import_module("training.pace_forecast")


def population(year=2019, races=240):
    rows = []
    for day in range(races):
        date = (pd.Timestamp(year, 1, 1) + pd.Timedelta(days=day)).strftime("%Y%m%d")
        for horse in range(1, 9):
            rows.append({"race_id": f"{year}01{day:04d}01", "race_date": date,
                         "horse_number": horse, "num_horses": 8,
                         "_ctx_history_corners": (horse, horse),
                         "surface": "芝", "venue": "東京", "distance": 1600,
                         "field_condition": "良"})
    target = pd.DataFrame(rows)
    x = pd.DataFrame(0., index=target.index, columns=context.FEATURE_COLUMNS)
    x["safe_horse_number"] = target.horse_number.astype(float)
    for name in context.CATEGORICAL_COLUMNS:
        x[name] = "known"
    return target, x


def test_insufficient_evidence_abstains_instead_of_inventing_pace_or_position():
    target, x = population(races=10)
    api = module()
    heads = api.fit_heads(target, x)
    future = target.copy()
    future["race_date"] = "20210101"
    forecast = api.predict_heads(heads, future, x)
    assert forecast.pace_model_available.eq(0).all()
    assert forecast.position_model_available.eq(0).all()
    assert forecast.pace_fast_probability.isna().all()
    assert forecast.position_front_probability.isna().all()
    assert heads["pace"]["status"] == "insufficient_labels"


def test_trained_position_forecast_is_probabilistic_and_field_order_is_coherent():
    target, x = population()
    api = module()
    heads = api.fit_heads(target, x)
    future = target.copy()
    future["race_date"] = "20210101"
    result = api.predict_heads(heads, future, x)
    probabilities = result[["position_front_probability", "position_middle_probability", "position_back_probability"]]
    np.testing.assert_allclose(probabilities.sum(axis=1), 1, atol=1e-6)
    assert probabilities.ge(0).all().all() and probabilities.le(1).all().all()
    assert result.position_model_available.eq(1).all()
    assert result.pace_model_available.eq(0).all()
    assert result.groupby(future.race_id).position_expected_order.max().gt(0).all()
    assert result.position_entropy.ge(0).all()
    assert heads["position"]["label_semantics"] == "first_recorded_passage_not_first_corner"


def test_same_day_predictions_and_result_columns_are_rejected():
    target, x = population()
    api = module()
    heads = api.fit_heads(target, x)
    with pytest.raises(ValueError, match="strictly-before"):
        api.predict_heads(heads, target, x)
    contaminated = x.assign(finish=1)
    with pytest.raises(ValueError, match="fixed-context"):
        api.fit_heads(target, contaminated)


def test_unverified_or_future_lap_records_do_not_become_supervised_labels():
    target, x = population()
    api = module()
    labels = pd.DataFrame({"race_id": target.race_id.unique(), "first600_seconds": 35.,
                           "validated": False, "source_sha256": "a" * 64})
    heads = api.fit_heads(target, x, pace_labels=labels)
    assert heads["pace"]["status"] == "insufficient_labels"
    labels["validated"] = True
    labels["race_id"] = "209901010101"
    with pytest.raises(ValueError, match="lap-label"):
        api.fit_heads(target, x, pace_labels=labels)


def test_oof_later_position_labels_cannot_change_earlier_forecasts():
    a, xa = population(2019)
    b, xb = population(2020, races=12)
    c, xc = population(2021, races=12)
    target = pd.concat([a, b, c], ignore_index=True)
    x = pd.concat([xa, xb, xc], ignore_index=True)
    api = module()
    original, audit = api.forward_features(target, x)
    changed = target.copy()
    changed.loc[changed.race_date.ge("20210101"), "_ctx_history_corners"] = pd.Series(
        [(9 - int(n), 1) for n in changed.loc[changed.race_date.ge("20210101"), "horse_number"]],
        index=changed.index[changed.race_date.ge("20210101")], dtype=object)
    updated, _ = api.forward_features(changed, x)
    pd.testing.assert_frame_equal(original.loc[target.race_date.lt("20210101")],
                                  updated.loc[target.race_date.lt("20210101")])
    assert all(row["training_date_to"] < row["prediction_date_from"] for row in audit if row["training_date_to"])
    assert original.loc[target.race_date.str.startswith("2019"), "position_model_available"].eq(0).all()


def test_pace_has_one_training_observation_and_one_forecast_per_race():
    target, x = population()
    labels = pd.DataFrame({"race_id": target.race_id.unique(),
                           "first600_seconds": np.tile([33., 36., 39.], 80),
                           "validated": True, "source_sha256": "a" * 64})
    first_sex = np.repeat(np.tile(["牡", "牝", "騸"], 80), 8)
    x["safe_sex"] = np.where(target.horse_number.eq(1), first_sex, "牝")
    api = module()
    heads = api.fit_heads(target, x, pace_labels=labels)
    assert heads["pace"]["status"] == "trained"
    assert heads["pace"]["runner_count"] == 240
    future = target.copy()
    future["race_date"] = "20210101"
    probabilities = api.predict_heads(heads, future, x)
    for name in api.FORECAST_COLUMNS[:3]:
        assert probabilities.groupby(future.race_id)[name].nunique().eq(1).all()


def test_final_model_freezes_heads_at_train_end_and_blocks_activation_without_pace():
    targets, matrices = [], []
    for year in range(2013, 2019):
        target, x = population(year, races=60)
        target["horse_id"] = target.horse_number.map(lambda n: f"horse{n}")
        target["finish"] = target.horse_number
        target["time_seconds"] = 95. + target.horse_number / 2
        targets.append(target)
        matrices.append(x)
    target = pd.concat(targets, ignore_index=True)
    x = pd.concat(matrices, ignore_index=True)
    train = np.flatnonzero(target.race_date.lt("20170101"))
    cal = np.flatnonzero(target.race_date.between("20170101", "20171231"))
    test = np.flatnonzero(target.race_date.ge("20180101"))
    api = import_module("training.pace_fit")
    bundle, result, predictions = api.fit_on_partitions(target, x, {"TRAIN": train, "CAL": cal, "TEST": test})
    assert bundle["heads"]["training_date_to"] == str(target.iloc[train].race_date.max())
    assert bundle["heads"]["training_date_to"] < str(target.iloc[cal].race_date.min())
    assert bundle["feature_columns"] == list(context.FEATURE_COLUMNS) + list(module().FORECAST_COLUMNS)
    assert bundle["production_eligible"] is False
    assert "pace_labels_insufficient" in bundle["activation_blockers"]
    assert result["test_used_for_selection_or_fit"] is False
    assert predictions["target"].race_id.nunique() == 60
    scores, forecasts = api.predict_scores(bundle, target.iloc[test], x.iloc[test])
    np.testing.assert_allclose(scores, predictions["scores"])
    assert forecasts.position_model_available.eq(1).all()


@pytest.mark.parametrize("horse_number", [0, 1.5, np.inf])
def test_head_rejects_invalid_runner_identity(horse_number):
    target, x = population(races=2)
    target["horse_number"] = target.horse_number.astype(float)
    target.loc[0, "horse_number"] = horse_number
    with pytest.raises(ValueError, match="complete-field"):
        module().fit_heads(target, x)


def test_pace_evaluation_uses_train_thresholds_and_measured_seconds():
    target, x = population(races=240)
    x["safe_age"] = np.repeat(np.tile([3., 4., 5.], 80), 8)
    lap = pd.DataFrame({"race_id": target.race_id.unique(), "first600_seconds": np.tile([33., 36., 39.], 80),
                        "validated": True, "source_sha256": "a" * 64})
    api = module()
    heads = api.fit_heads(target, x, pace_labels=lap)
    future, future_x = population(2020, races=240)
    future_lap = lap.copy()
    future_lap["race_id"] = future.race_id.unique()
    future_lap["first600_seconds"] = 40.
    metric = api.evaluate_heads(heads, future, future_x, pace_labels=future_lap)
    assert metric["pace"]["observed_class_counts"] == {"0": 240}
    assert metric["pace"]["race_count"] == 240
    assert metric["pace"]["first600_mae_seconds"] > 0
    assert metric["position"]["runner_count"] == len(future)


def test_constant_race_context_abstains_instead_of_crashing():
    target, x = population(races=240)
    lap = pd.DataFrame({"race_id": target.race_id.unique(), "first600_seconds": np.tile([33., 36., 39.], 80),
                        "validated": True, "source_sha256": "a" * 64})
    heads = module().fit_heads(target, x, pace_labels=lap)
    assert heads["pace"]["status"] == "insufficient_features"
    assert heads["pace"]["model"] is None


def test_withdrawals_allow_noncontiguous_original_horse_numbers():
    target, x = population(races=2)
    target.loc[7, "horse_number"] = 18
    x.loc[7, "safe_horse_number"] = 18.
    heads = module().fit_heads(target, x)
    assert heads["position"]["race_count"] == 2


def test_recovered_lap_distance_must_match_immutable_target(tmp_path):
    import json
    runner = import_module("scripts.train_pace_stack")
    target, _ = population(races=2)
    row = target.iloc[0]
    record = {"race_id": row.race_id, "race_date": row.race_date, "first600_seconds": 35.,
              "source_sha256": "a" * 64, "validated": True, "lap_evidence": {"total_distance": 1200}}
    path = tmp_path / "laps.json"
    path.write_text(json.dumps({"schema": "verified-pace-lap-recovery-v1", "observations": [record]}), encoding="utf-8")
    with pytest.raises(ValueError, match="distance"):
        runner._labels(path, target)


def test_missing_control_probability_evidence_never_passes_comparison():
    runner = import_module("scripts.train_pace_stack")
    results = {"baseline46": {"probability_metrics": None},
               "context80": {"probability_metrics": {"nll": .3, "brier": .3}},
               "pace_stack": {"probability_metrics": {"nll": .2, "brier": .2}}}
    assert runner._comparison_passed(results) is False
    results["baseline46"]["probability_metrics"] = {"nll": .3, "brier": .3}
    assert runner._comparison_passed(results) is True
    results["baseline46"]["probability_metrics"]["brier"] = .1
    assert runner._comparison_passed(results) is False

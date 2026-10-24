from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from experiments.features import ALLOWED_FEATURES, engineer_features


def frame():
    rows = []
    for rid, day in [("201505010101", "20151201"), ("201505010201", "20151220"),
                     ("201605010101", "20160110"), ("201605010102", "20160110"),
                     ("201605010201", "20160201")]:
        for number in (1, 2):
            rows.append(dict(race_id=rid, race_date=day, horse_id=f"H{number}", horse_number=number,
                             finish=number, time_seconds=100 + number, distance=1600, surface="turf",
                             venue="東京", num_horses=2, jockey_id=f"J{number}", trainer_id="T",
                             sire="S", damsire="D", last_3f_time=35 + number, odds=number + 1,
                             popularity=number, prev_race_finish=99, horse_total_runs=999,
                             safe_prev_finish=999, surprise_numeric=999))
    return pd.DataFrame(rows)


def row(result, rid, horse="H1"):
    return result.loc[(result.race_id == rid) & (result.horse_id == horse)].iloc[0]


def test_explicit_feature_allowlist_rejects_original_prev_profile_and_markets():
    result, manifest = engineer_features(frame())
    columns = manifest["feature_sets"]["M03"]
    assert set(columns) == ALLOWED_FEATURES
    assert len(columns) == len(set(columns))
    assert all(name.startswith("safe_") for name in columns)
    assert not set(columns) & {"finish", "time_seconds", "last_3f_time", "prev_race_finish",
                               "horse_total_runs", "surprise_numeric", "odds", "popularity"}
    assert set(manifest["feature_sets"]["M01"]) < set(manifest["feature_sets"]["M02"])
    assert set(manifest["feature_sets"]["M02"]) < set(columns)
    assert manifest["feature_sets"]["M05"] == columns
    assert row(result, "201605010101").safe_prev_finish == 1


def test_strictly_prior_day_history_and_entities_exclude_all_same_day_results():
    result, manifest = engineer_features(frame())
    first = row(result, "201605010101")
    second = row(result, "201605010102")
    assert first.safe_prior_starts == second.safe_prior_starts == 2
    assert first.safe_days_since_last == second.safe_days_since_last == 21
    assert first.safe_jockey_starts == second.safe_jockey_starts == 2
    assert first.safe_trainer_starts == second.safe_trainer_starts == 4
    assert first.safe_sire_starts == second.safe_sire_starts == 4
    assert first.safe_damsire_starts == second.safe_damsire_starts == 4
    assert first.safe_horse_course_starts == second.safe_horse_course_starts == 2
    assert first.safe_past5_count == 2
    assert first.safe_past5_observed_coverage == pytest.approx(0.4)
    assert manifest["point_in_time_audit"]["same_day_future_references"] == 0


def test_current_and_future_outcome_mutations_cannot_change_earlier_features():
    original = frame()
    expected, manifest = engineer_features(original)
    changed = original.copy()
    changed.loc[changed.race_date.ge("20160110"), ["finish", "time_seconds", "last_3f_time"]] = [2, 90, 25]
    changed["odds"] = 999
    changed["popularity"] = 999
    changed["prev_race_finish"] = -10000
    changed["horse_total_runs"] = -10000
    observed, _ = engineer_features(changed)
    columns = manifest["feature_sets"]["M03"]
    pd.testing.assert_frame_equal(expected.loc[expected.race_date.le("20160110"), columns],
                                  observed.loc[observed.race_date.le("20160110"), columns])


def test_missing_history_stays_missing_without_synthetic_zero_speed():
    source = frame().drop(columns=["jockey_id", "trainer_id", "sire", "damsire", "last_3f_time"])
    result, _ = engineer_features(source)
    first = row(result, "201505010101")
    assert first.safe_prior_starts == 0
    assert first.safe_past3_count == 0
    assert np.isnan(first.safe_prev_finish)
    assert np.isnan(first.safe_past3_avg_speed_mps)
    assert np.isnan(first.safe_jockey_starts)
    assert np.isnan(row(result, "201605010101").safe_past5_avg_last3f_time)


def test_missing_horse_identity_does_not_merge_unidentified_horses():
    source = frame()
    source["horse_id"] = ""
    result, _ = engineer_features(source)
    assert result.safe_prior_starts.eq(0).all()
    assert result.safe_prev_finish.isna().all()
    assert result.safe_horse_course_starts.isna().all()


def test_input_order_does_not_change_generated_values():
    expected, expected_manifest = engineer_features(frame())
    observed, observed_manifest = engineer_features(frame().sample(frac=1, random_state=42))
    pd.testing.assert_frame_equal(expected, observed)
    assert expected_manifest == observed_manifest


@pytest.mark.parametrize("mutation", ["missing_column", "empty", "date", "result"])
def test_malformed_input_fails_closed(mutation):
    source = frame()
    if mutation == "missing_column":
        source = source.drop(columns="race_date")
    elif mutation == "empty":
        source = source.iloc[:0]
    elif mutation == "date":
        source.loc[0, "race_date"] = "20160231"
    else:
        source.loc[0, "time_seconds"] = 0
    with pytest.raises(ValueError, match="experiment-feature"):
        engineer_features(source)

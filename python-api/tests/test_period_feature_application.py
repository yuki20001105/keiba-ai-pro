import numpy as np
import pandas as pd
import pytest

from experiments.features import engineer_features
from training.period_feature_application import backoff_observed_history, rebuild_same_cohort
from training.structural_imputation import FALLBACKS, FLAGS


def observation(race_id, day, horse="A", **changes):
    return {
        "race_id": race_id, "race_date": day, "horse_id": horse, "horse_number": 1,
        "finish": 1, "time_seconds": 150.0, "distance": 2500.0, "surface": "turf",
        "venue": "Tokyo", "num_horses": 1, "last_3f_time": 35.0, **changes,
    }


def test_individual_history_recovers_prior_performance_without_changing_target_cohort():
    current = observation("201606010101", "20160102", venue="Tokyo")
    before, manifest = engineer_features(pd.DataFrame([current]))
    prior = observation("201505010101", "20150101", finish=2)
    result, audit = rebuild_same_cohort(before, pd.DataFrame([current, prior]), manifest["feature_sets"]["M03"])
    assert len(result) == len(before) == 1
    assert result.safe_prior_starts.iloc[0] == 1
    assert result.safe_horse_course_starts.iloc[0] == 1
    assert result.safe_horse_course_win_rate.iloc[0] == 0
    assert result.safe_horse_course_avg_speed_mps.iloc[0] == pytest.approx(2500 / 150)
    assert audit["same_day_results_excluded"]
    assert before.safe_horse_course_win_rate.isna().all()


def test_current_and_same_day_results_cannot_change_target_history_features():
    prior = observation("201505010101", "20150101", finish=2)
    current = observation("201606010101", "20160102")
    same_day = observation("201606010102", "20160102")
    before, manifest = engineer_features(pd.DataFrame([current]))
    history = pd.DataFrame([prior, current, same_day])
    first, _ = rebuild_same_cohort(before, history, manifest["feature_sets"]["M03"])
    history.loc[history.race_date.eq("20160102"), ["finish", "time_seconds"]] = [15, 999]
    second, _ = rebuild_same_cohort(before, history, manifest["feature_sets"]["M03"])
    pd.testing.assert_frame_equal(first, second)


def test_missing_or_duplicate_target_cannot_silently_change_the_population():
    current = observation("201606010101", "20160102")
    before, manifest = engineer_features(pd.DataFrame([current]))
    features = manifest["feature_sets"]["M03"]
    with pytest.raises(ValueError, match="target-not-in-history"):
        rebuild_same_cohort(before, pd.DataFrame([observation("201605010101", "20160101")]), features)
    with pytest.raises(ValueError, match="duplicate-identity"):
        rebuild_same_cohort(before, pd.DataFrame([current, current]), features)


def test_research_backoff_keeps_unknown_coverage_explicit_and_never_uses_other_rows():
    current = observation("201606010101", "20160102", venue="Nakayama")
    prior = observation("201505010101", "20150101", venue="Tokyo", distance=1600, time_seconds=100, finish=2)
    frame, _ = engineer_features(pd.DataFrame([prior, current]))
    target = frame.loc[frame.race_date.eq("20160102")].reset_index(drop=True)
    original = target.copy(deep=True)
    result, journal = backoff_observed_history(target)
    assert len(journal) == 6
    assert result.safe_horse_course_starts.iloc[0] == 0
    assert result.safe_horse_distance_band_starts.iloc[0] == 0
    assert result.safe_horse_course_win_rate.iloc[0] == 0
    assert result.safe_horse_course_avg_speed_mps.iloc[0] == 16
    assert result[FLAGS].sum().sum() == 6
    assert not journal.public_career_completeness_verified.any()
    assert set(journal.reason) == {"no_prior_condition_in_frozen_eligible_store"}
    pd.testing.assert_frame_equal(original, target)
    for column in FALLBACKS:
        assert result.at[0, column] == original.at[0, FALLBACKS[column]]


def test_debutant_missing_fallback_and_unknown_condition_count_remain_nan():
    current = observation("201606010101", "20160102")
    frame, _ = engineer_features(pd.DataFrame([current]))
    result, journal = backoff_observed_history(frame)
    assert journal.empty
    assert result.safe_horse_course_win_rate.isna().all()
    frame["safe_horse_surface_starts"] = 5
    frame["safe_horse_surface_win_rate"] = 0.5
    frame["safe_horse_course_starts"] = np.nan
    result, _ = backoff_observed_history(frame)
    assert result.safe_horse_course_win_rate.isna().all()


@pytest.mark.parametrize("tamper", [False, True])
def test_independent_replay_handles_same_day_boundaries_and_unknown_entities(tamper):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "verify_period_feature_application.py"
    spec = importlib.util.spec_from_file_location("period_replay", path)
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    history = pd.DataFrame([
        observation("201505010101", "20150101", finish=2, last_3f_time=None),
        observation("201606010101", "20160102", finish=3),
        observation("201606010102", "20160102", finish=4),
        observation("201606020101", "20160103", venue="Nakayama", distance=1600),
    ])
    history = history.assign(jockey_id="J", trainer_id="T", sire="unknown_local", damsire=None)
    rebuilt, _ = engineer_features(history)
    verified = replay.verify_histories(history, history, rebuilt)
    assert len(verified) == 32
    if tamper:
        rebuilt.loc[1, "safe_horse_course_starts"] += 1
        with pytest.raises(ValueError, match="feature-disagrees"):
            replay.verify_aggregates(history, history, rebuilt)
    else:
        assert len(replay.verify_aggregates(history, history, rebuilt)) == 24

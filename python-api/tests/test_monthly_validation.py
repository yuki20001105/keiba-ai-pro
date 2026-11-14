from copy import deepcopy
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from test_db_shared_features import rows
from test_db_shared_optimized import fixture_data, pure_fit


def monthly_races():
    source = rows().iloc[:4].copy()
    frames = []
    for year, month in [(2021, 1), (2021, 2), (2024, 1), (2026, 8)]:
        for race in range(20):
            frame = source.copy()
            day = f"{year}{month:02d}{1 + race // 10:02d}"
            frame["race_id"] = f"{year}05{month:02d}{1 + race // 10:02d}{1 + race % 10:02d}"
            frame["race_date"] = day
            frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def test_seeded_monthly_split_keeps_whole_races_and_represents_recent_years():
    from training.monthly_validation import stratified_partitions
    source = monthly_races()
    roles = stratified_partitions(source, seed=42)
    assert len(roles) == 80 and roles.race_id.is_unique
    for _, part in roles.groupby(roles.race_date.str[:6]):
        assert part.partition.value_counts().to_dict() == {"TRAIN": 13, "TEST": 4, "CAL": 3}
    shuffled = stratified_partitions(source.sample(frac=1, random_state=7), seed=42)
    pd.testing.assert_frame_equal(roles, shuffled)
    assert not roles.equals(stratified_partitions(source, seed=43))
    assert set(roles.loc[roles.partition.eq("TRAIN")].race_date.str[:4]) == {"2021", "2024", "2026"}


def test_one_race_cannot_have_two_dates_or_a_partial_partition():
    from training.monthly_validation import stratified_partitions
    source = monthly_races()
    source.loc[0, "race_date"] = "20220101"
    with pytest.raises(ValueError):
        stratified_partitions(source, seed=42)


def test_random_holdout_results_never_become_later_training_features():
    from training.monthly_validation import build_holdout_features
    history = rows()
    target = history.loc[history.race_date.ge("20240101")].copy()
    withheld = set(target.loc[target.race_date.lt("20250101")].race_id)
    actual = build_holdout_features(target, history, withheld)
    assert actual.safe_prior_starts.eq(1).all()
    assert actual.loc[target.race_date.eq("20250105"), "safe_prev_time_seconds"].tolist() == [91, 92, 93, 94]
    changed = deepcopy(history)
    changed.loc[changed.race_id.isin(withheld), ["finish", "time_seconds", "last_3f_time"]] = 999999
    pd.testing.assert_frame_equal(build_holdout_features(target, changed, withheld), actual)
    assert actual.attrs["heldout_outcomes_consumed"] is False
    assert actual.attrs["same_day_or_future_history_used"] is False


def test_daily_feature_integrity_hash_does_not_reserialize_unrelated_career_histories(monkeypatch):
    from forward_adapters import feature_state
    from training.monthly_validation import build_holdout_features
    history = rows()
    extra = history.iloc[:4].copy()
    extra["race_id"], extra["race_date"] = "201305010102", "20130101"
    extra["horse_id"] = ["unrelated-" + str(i) for i in range(4)]
    history = pd.concat([extra, history], ignore_index=True)
    target = history.loc[history.race_date.ge("20240101")].copy()
    original = feature_state.build_current_features

    def checked(envelope, current):
        assert set(envelope["state"]["histories"]).issubset(set(current.horse_id))
        assert set(envelope["state"]["horse_counts"]).issubset(set(current.horse_id))
        return original(envelope, current)

    monkeypatch.setattr(feature_state, "build_current_features", checked)
    result = build_holdout_features(target, history, set(target.loc[target.race_date.lt("20250101")].race_id))
    assert result.safe_prior_starts.eq(1).all()


def test_forward_validation_never_trains_or_calibrates_on_the_evaluation_year():
    from training.monthly_validation import forward_partitions
    source = monthly_races()
    roles = forward_partitions(source, year=2026)
    test = roles.loc[roles.partition.eq("TEST")]
    assert test.race_date.str.startswith("2026").all() and len(test) == 20
    assert roles.loc[roles.partition.isin(["TRAIN", "CAL"])].race_date.lt("20260101").all()
    assert not roles.race_id.duplicated().any()


def research_inputs():
    from training import monthly_validation, db_shared_features
    target, features = fixture_data(days=59)
    roles = monthly_validation.stratified_partitions(target)
    features.attrs.update(
        current_outcomes_consumed=False, same_day_or_future_history_used=False,
        heldout_outcomes_consumed=False,
        withheld_races=sorted(roles.loc[roles.partition.isin(["CAL", "TEST"])].race_id),
        feature_source_hashes={**db_shared_features.source_hashes(),
            "training.monthly_validation": hashlib.sha256(Path(monthly_validation.__file__).read_bytes()).hexdigest()},
    )
    return target, features, roles


def test_research_reports_only_disjoint_test_races_and_never_grants_live_use(pure_fit):
    from training.monthly_validation_experiment import fit_candidate, evaluate_candidate
    target, features, roles = research_inputs()
    bundle, fitted = fit_candidate(target, features, roles, kind="monthly_random")
    evaluated = evaluate_candidate(bundle, target, features, roles)
    test_ids = set(roles.loc[roles.partition.eq("TEST")].race_id)
    development_ids = set(roles.loc[roles.partition.isin(["TRAIN", "CAL"])].race_id)
    assert set(evaluated.race_id) == test_ids
    assert not set(evaluated.race_id) & development_ids
    assert evaluated.groupby("race_id").p_norm.sum().to_numpy() == pytest.approx(1.0)
    assert bundle["reference_only"] is True
    assert bundle["probability_validation"]["eligible_for_ev"] is False
    assert fitted["active_model_changed"] is False
    assert fitted["seed"] == 42


@pytest.mark.parametrize("change", ["overlap", "history_leak", "source", "different_seed"])
def test_research_refuses_untrusted_split_or_feature_contract(pure_fit, change):
    from training.monthly_validation_experiment import fit_candidate
    target, features, roles = research_inputs()
    if change == "overlap":
        extra = roles.iloc[[0]].copy()
        extra["partition"] = "TEST"
        roles = pd.concat([roles, extra], ignore_index=True)
    elif change == "history_leak":
        features.attrs["heldout_outcomes_consumed"] = True
    elif change == "source":
        features.attrs["feature_source_hashes"] = {}
    else:
        roles["partition"] = roles["partition"].replace({"TRAIN": "TEST", "TEST": "TRAIN"})
    with pytest.raises(ValueError):
        fit_candidate(target, features, roles, kind="monthly_random")


def test_random_test_labels_do_not_change_fit_or_calibration(pure_fit):
    from training.monthly_validation_experiment import fit_candidate
    target, features, roles = research_inputs()
    first, first_result = fit_candidate(target, features, roles, kind="monthly_random")
    changed = target.copy()
    test = changed.race_id.isin(roles.loc[roles.partition.eq("TEST")].race_id)
    changed.loc[test, "finish"] = 4 - changed.loc[test, "finish"]
    changed.loc[test, "time_seconds"] += 50
    second, second_result = fit_candidate(changed, features, roles, kind="monthly_random")
    assert first["baseline"] == second["baseline"]
    assert first["calibrator"] == second["calibrator"]
    assert first_result["selected_preset"] == second_result["selected_preset"]

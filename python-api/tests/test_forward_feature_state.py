"""Exact historical parity and negative PIT checks; synthetic input only."""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from experiments import features as legacy, search_features as search
from forward_adapters.feature_state import _hash, build_current_features, build_history_state


def observations():
    rows = []
    # Include pre-2016 support, same-day shared entity appearances, switches,
    # missing values, identities with no history, and more than ten starts.
    days = [f"201301{day:02d}" for day in range(1, 16)] + ["20180101", "20210918", "20210919"]
    for index, day in enumerate(days):
        for race in (1, 2):
            for number in range(1, 5):
                rows.append({"race_id": f"{day[:4]}05{index + 1:02d}01{race:02d}", "race_date": day,
                             "horse_id": f"horse-{number}" if number < 4 else ("" if index % 2 else f"new-{index}"),
                             "horse_number": number, "num_horses": 4, "venue": "東京",
                             "surface": "turf" if race == 1 else "dirt", "distance": 1200 if index % 2 else 1600,
                             "finish": number, "time_seconds": 80 + number + index / 10,
                             "last_3f_time": np.nan if (index + number) % 3 == 0 else 34 + number / 10,
                             "bracket_number": number, "age": 3 + index // 5,
                             "sex": "牝" if number % 2 else "牡", "burden_weight": 53 + number,
                             "horse_weight": np.nan if index % 2 else 450 + number,
                             "horse_weight_change": number - 2, "field_condition": "良", "weather": "晴",
                             "race_class": "未勝利" if index < 8 else "1勝クラス",
                             "jockey_id": "shared-j" if number != 4 else None,
                             "trainer_id": f"trainer-{number}", "sire": "shared-sire" if number != 4 else None,
                             "damsire": f"damsire-{number % 2}", "odds": number * 2, "popularity": number})
    return pd.DataFrame(rows)


@pytest.mark.parametrize("target", ["20130101", "20130102", "20130110", "20130115", "20180101", "20210919"])
def test_all_101_features_exactly_match_historical_pipeline_with_current_outcomes_removed(target):
    history_and_current = observations()
    original, _ = legacy.engineer_features(history_and_current)
    original = pd.concat([original, search.derive_search_features(original)], axis=1)
    reference = original.loc[original.race_date.eq(target)].reset_index(drop=True)
    history = history_and_current.loc[history_and_current.race_date.lt(target)].copy()
    current = history_and_current.loc[history_and_current.race_date.eq(target)].drop(columns=["finish", "time_seconds", "last_3f_time", "odds", "popularity"])
    envelope = build_history_state(history, target_date=target)
    result = build_current_features(envelope, current)
    columns = list(legacy.ALLOWED_FEATURES) + list(search.SEARCH_FEATURES)
    assert len(columns) == 101
    pd.testing.assert_frame_equal(result[columns], reference[columns], check_exact=True)
    assert result.attrs["feature_count"] == 101
    assert result.attrs["input_outcomes_consumed"] is False
    assert result.attrs["live_card_parser_verified"] is False
    assert not {"finish", "time_seconds", "last_3f_time", "odds", "popularity"}.intersection(result)


def test_json_round_trip_and_reused_day_state_does_not_update_between_current_races():
    source = observations()
    target = "20210919"
    envelope = build_history_state(source.loc[source.race_date.lt(target)], target_date=target)
    envelope = json.loads(json.dumps(envelope, allow_nan=False))
    before = deepcopy(envelope)
    current = source.loc[source.race_date.eq(target)]
    combined = build_current_features(envelope, current)
    parts = [build_current_features(envelope, race) for _, race in current.groupby("race_id", sort=True)]
    pd.testing.assert_frame_equal(combined, pd.concat(parts, ignore_index=True), check_exact=True)
    assert envelope == before
    assert envelope["state"]["history_min_source_date"] == "20130101"
    assert envelope["state"]["history_max_source_date"] == "20210918"


def test_unknown_horse_id_never_acquires_another_unknown_horse_history():
    source = observations()
    target = "20210919"
    envelope = build_history_state(source.loc[source.race_date.lt(target)], target_date=target)
    current = source.loc[source.race_date.eq(target)].copy()
    current["horse_id"] = ""
    result = build_current_features(envelope, current)
    assert result.safe_prior_starts.eq(0).all()
    assert result.safe_past10_count.eq(0).all()
    assert result.safe_prev_finish.isna().all()
    assert result.safe_horse_surface_starts.isna().all()
    # Known current jockey/trainer identities still have their own prior state.
    assert result.loc[result.horse_number.lt(4), "safe_jockey_starts"].gt(0).all()


def test_outcomes_market_and_legacy_fake_safe_columns_are_never_consumed():
    source = observations()
    target = "20210919"
    envelope = build_history_state(source.loc[source.race_date.lt(target)], target_date=target)
    current = source.loc[source.race_date.eq(target)].copy()
    expected = build_current_features(envelope, current)
    for name in ("finish", "time_seconds", "last_3f_time", "odds", "popularity", "safe_prev_finish", "safe_search_rel_prev_speed_rank"):
        current[name] = 999999
    pd.testing.assert_frame_equal(build_current_features(envelope, current), expected, check_exact=True)


@pytest.mark.parametrize("boundary", ["20210918", "20210919"])
def test_same_day_and_future_history_rejected(boundary):
    source = observations()
    with pytest.raises(ValueError, match="strictly-before-target"):
        build_history_state(source, target_date=boundary)


@pytest.mark.parametrize("changed_day", ["20210918", "20210920"])
def test_state_cannot_be_reused_for_prior_or_different_future_day(changed_day):
    source = observations()
    envelope = build_history_state(source.loc[source.race_date.lt("20210919")], target_date="20210919")
    current = source.loc[source.race_date.eq("20210919")].copy()
    current["race_date"] = changed_day
    with pytest.raises(ValueError, match="prepared-target-day"):
        build_current_features(envelope, current)


@pytest.mark.parametrize("historical", [True, False])
@pytest.mark.parametrize("mutation", ["partial", "duplicate_number", "duplicate_horse", "venue", "surface", "distance", "conflicting_date", "missing_count"])
def test_cohort_identity_and_scope_fail_closed(historical, mutation):
    source = observations()
    history = source.loc[source.race_date.lt("20210919")]
    current = source.loc[source.race_date.eq("20210919")].copy()
    candidate = (history if historical else current).copy().reset_index(drop=True)
    if mutation == "partial":
        candidate = candidate.iloc[1:]
    elif mutation == "duplicate_number":
        candidate.loc[1, "horse_number"] = candidate.loc[0, "horse_number"]
    elif mutation == "duplicate_horse":
        candidate.loc[1, "horse_id"] = candidate.loc[0, "horse_id"]
    elif mutation == "venue":
        candidate.loc[0, "venue"] = "京都"
    elif mutation == "surface":
        candidate.loc[0, "surface"] = "jump"
    elif mutation == "distance":
        candidate.loc[0, "distance"] = 3333
    elif mutation == "conflicting_date":
        candidate.loc[0, "race_date"] = "20210917"
    else:
        candidate = candidate.drop(columns="num_horses")
    with pytest.raises(ValueError):
        if historical:
            build_history_state(candidate, target_date="20210919")
        else:
            envelope = build_history_state(history, target_date="20210919")
            build_current_features(envelope, candidate)


@pytest.mark.parametrize("field,value", [("finish", np.nan), ("finish", 0), ("time_seconds", 0), ("time_seconds", np.nan)])
def test_history_requires_actual_confirmed_results(field, value):
    history = observations().iloc[:8].copy()
    history.loc[0, field] = value
    with pytest.raises(ValueError, match="confirmed-complete-results"):
        build_history_state(history, target_date="20210919")


def test_empty_history_remains_explicit_missing_not_freshly_observed():
    current = observations().iloc[:8]
    envelope = build_history_state(pd.DataFrame(), target_date="20130101")
    assert envelope["state"]["history_max_source_date"] is None
    assert envelope["state"]["history_row_count"] == 0
    result = build_current_features(envelope, current)
    assert result.safe_prior_starts.eq(0).all()
    assert result.safe_prev_speed_mps.isna().all()
    assert result.safe_past3_count.eq(0).all()
    assert result.safe_past3_observed_coverage.eq(0).all()


def test_state_hash_and_rehashed_future_state_rejected():
    source = observations()
    envelope = build_history_state(source.loc[source.race_date.lt("20210919")], target_date="20210919")
    current = source.loc[source.race_date.eq("20210919")]
    envelope["state"]["horse_counts"]["horse-1"] += 1
    with pytest.raises(ValueError, match="integrity"):
        build_current_features(envelope, current)
    envelope["state"]["history_max_source_date"] = "20210919"
    envelope["state_sha256"] = _hash(envelope["state"])
    with pytest.raises(ValueError, match="same-day-or-future"):
        build_current_features(envelope, current)


def test_history_input_hash_order_stable_and_support_changes_are_bound():
    history = observations().iloc[:80]
    a = build_history_state(history, target_date="20210919")
    b = build_history_state(history.sample(frac=1, random_state=5), target_date="20210919")
    assert a == b
    changed = history.copy()
    changed.loc[0, "time_seconds"] += 0.1
    c = build_history_state(changed, target_date="20210919")
    assert a["state"]["source_rows_sha256"] != c["state"]["source_rows_sha256"]
    assert a["state_sha256"] != c["state_sha256"]

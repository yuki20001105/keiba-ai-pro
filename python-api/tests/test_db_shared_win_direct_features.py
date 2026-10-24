"""Shared 88 historical/current feature parity and strict quote-only tests."""
from copy import deepcopy
from datetime import date, timedelta
import importlib
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "python-api"), str(ROOT / "keiba")]
features = importlib.import_module("db_shared_win_direct_features")
from experiments import features as frozen
from experiments import search_features as search
from training import db_shared_features as normal


def full_history():
    rows = []
    # The third horse changes, but the unrelated runner's rider/trainer results
    # remain part of the complete world used for support/rider comparisons.
    for year in (2013, 2014, 2016, 2020, 2021, 2023, 2025, 2026):
        for day in range(3):
            current = (date(year, 1, 5) + timedelta(days=day)).strftime("%Y%m%d")
            race_id = f"{year}050101{day + 1:02d}"
            for horse in (1, 2, 3):
                winner = day % 3 + 1
                order = [winner] + [number for number in (1, 2, 3) if number != winner]
                rows.append({"race_id": race_id, "race_date": current, "horse_number": horse,
                    "horse_id": f"h{horse}" if horse < 3 else f"unrelated{year}",
                    "finish": order.index(horse) + 1, "time_seconds": 95. + horse + day * .1,
                    "distance": 1600 if day != 1 else 1800, "surface": "turf", "venue": "東京", "num_horses": 3,
                    "last_3f_time": 34.5 + horse * .1, "jockey_id": f"j{horse % 2}",
                    "trainer_id": f"t{horse % 2}", "sire": f"s{horse}", "damsire": f"ds{horse}",
                    "odds": 3. + horse, "popularity": horse, "bracket_number": horse,
                    "age": 3 + day, "sex": "牡", "burden_weight": 56., "horse_weight": 480. + horse,
                    "horse_weight_change": np.nan if horse == 2 else float(day), "field_condition": "良",
                    "weather": "晴", "race_class": "1勝"})
    return pd.DataFrame(rows).reindex(columns=features.canonical.SOURCE_COLUMNS)


@pytest.fixture
def source():
    target = full_history()
    generated, _ = frozen.engineer_features(target)
    raw47 = generated.loc[:, list(features.RAW47_COLUMNS)].copy()
    return target, raw47


def test_fixed_88_schema_46_plus_13_plus_29_and_complete_world_contract():
    assert len(features.FEATURE_COLUMNS) == len(set(features.FEATURE_COLUMNS)) == 88
    assert features.FEATURE_COLUMNS == features.BASE46_COLUMNS + tuple(frozen.APTITUDE_COLUMNS) + search.SEARCH_FEATURES
    assert "safe_past5_top3_rate" not in features.FEATURE_COLUMNS
    assert features.HISTORY_CONTRACT["current_horse_only_history_allowed"] is False
    assert features.HISTORY_CONTRACT["same_day_results_allowed"] is False
    assert features.HISTORY_CONTRACT["full_world_completeness_verified"] is False


def test_training_pipeline_exact_raw47_base_parity_and_input_unmodified(source):
    target, raw47 = source
    original, oldraw = target.copy(deep=True), raw47.copy(deep=True)
    result, receipt = features.build_training_features(target, raw47)
    assert list(result) == list(features.IDENTITY_COLUMNS) + list(features.FEATURE_COLUMNS)
    assert receipt["feature_count"] == 88
    assert receipt["base46_parity"]["verified"] is True
    assert receipt["base46_parity"]["exact_numeric_and_nan_equality"]
    assert receipt["point_in_time_audit"]["same_day_future_references"] == 0
    assert receipt["input_outcomes_consumed"] is False
    assert receipt["historical_outcomes_used_for_later_dates"] is True
    assert receipt["eligible_for_ev"] is False
    pd.testing.assert_frame_equal(original, target)
    pd.testing.assert_frame_equal(oldraw, raw47)
    assert receipt["frame_sha256"] == features.worker.frame_hash(result)


def test_cache_contract_matches_existing_normal_raw47_feature_equations(source):
    target, _ = source
    verified = normal.build_training_features(target, target)
    result, receipt = features.build_training_features(target, verified)
    assert receipt["base46_parity"]["verified"]
    pd.testing.assert_frame_equal(result.loc[:, list(features.BASE46_COLUMNS)],
        verified.loc[:, list(features.BASE46_COLUMNS)], check_names=False)


@pytest.mark.parametrize("kind", ["numeric", "nan", "category", "row-count", "missing-column", "duplicate-column", "key"])
def test_raw47_exact_binding_fails_closed(source, kind):
    target, raw = source
    if kind == "numeric":
        raw.loc[0, "safe_distance"] += 1.
    elif kind == "nan":
        raw.loc[0, "safe_days_since_last"] = 0.
    elif kind == "category":
        raw.loc[0, "safe_weather"] = "not-the-source-weather"
    elif kind == "row-count":
        raw = raw.iloc[:-1]
    elif kind == "missing-column":
        raw = raw.drop(columns=["safe_distance"])
    elif kind == "duplicate-column":
        raw = pd.concat([raw, raw[["safe_distance"]]], axis=1)
    else:
        raw = pd.concat([target.loc[:, list(features.IDENTITY_COLUMNS)], raw], axis=1)
        raw.loc[0, "horse_number"] = 4
    with pytest.raises(ValueError):
        features.build_training_features(target, raw)


def test_reordered_target_with_correspondingly_reordered_raw47_is_key_aligned(source):
    target, raw = source
    order = target.sample(frac=1., random_state=5).index
    result, _ = features.build_training_features(target.loc[order].reset_index(drop=True), raw.loc[order].reset_index(drop=True))
    expected, _ = features.build_training_features(target, raw)
    pd.testing.assert_frame_equal(result, expected)


def test_current_state88_equals_historical_generation_every_column(source):
    target, raw47 = source
    day = target.race_date.max()
    current = target.loc[target.race_date.eq(day)].reset_index(drop=True)
    history = target.loc[target.race_date.lt(day)].reset_index(drop=True)
    current_raw = raw47.loc[target.race_date.eq(day)].reset_index(drop=True)
    result, receipt = features.build_current_features(current, history, raw47=current_raw)
    allfeatures, _ = features.build_training_features(target, raw47)
    expected = allfeatures.loc[allfeatures.race_date.eq(day)].reset_index(drop=True)
    pd.testing.assert_frame_equal(result, expected)
    assert receipt["history_date_to"] < day
    assert receipt["history_rows"] == len(history)
    assert receipt["base46_parity"]["verified"]
    assert receipt["dummy_current_results_used"] is False
    assert receipt["input_outcomes_consumed"] is False
    assert receipt["same_day_or_future_history_used"] is False
    assert receipt["prestart_capture_provenance_verified"] is False


def test_current_outcome_odds_comment_poison_ignored(source):
    target, _ = source
    day = target.race_date.max()
    current = target.loc[target.race_date.eq(day)].reset_index(drop=True)
    history = target.loc[target.race_date.lt(day)].reset_index(drop=True)
    expected, before = features.build_current_features(current, history)
    poisoned = current.copy()
    for column in ("finish", "time_seconds", "last_3f_time", "odds", "popularity", "grade", "comment", "tansho_payout"):
        poisoned[column] = object()
    actual, after = features.build_current_features(poisoned, history)
    pd.testing.assert_frame_equal(actual, expected)
    assert after["card_input_sha256"] == before["card_input_sha256"]
    assert after["state_sha256"] == before["state_sha256"]


@pytest.mark.parametrize("period", ["same-day", "future", "pre2013"])
def test_history_same_day_future_or_wrong_scope_is_rejected(source, period):
    target, _ = source
    current = target.loc[target.race_date.eq(target.race_date.max())].reset_index(drop=True)
    if period == "same-day":
        history = target.copy()
    elif period == "future":
        history = target.copy()
        history.loc[history.race_date.eq(history.race_date.max()), "race_date"] = "20261231"
    else:
        history = target.loc[target.race_date.lt(current.race_date.iloc[0])].copy()
        old = history.race_date.str.startswith("2013")
        history.loc[old, "race_date"] = history.loc[old, "race_date"].str.replace("2013", "2012")
        history.loc[old, "race_id"] = history.loc[old, "race_id"].str.replace("2013", "2012")
    with pytest.raises(ValueError):
        features.build_current_features(current, history)


def test_empty_history_is_explicit_nan_not_fabricated_career(source):
    target, _ = source
    current = target.loc[target.race_date.eq(target.race_date.min())].reset_index(drop=True)
    result, receipt = features.build_current_features(current, target.iloc[:0])
    assert result.safe_prior_starts.eq(0.).all()
    assert result.safe_prev_finish.isna().all()
    assert result.safe_horse_course_win_rate.isna().all()
    assert receipt["history_rows"] == 0
    assert receipt["history_date_to"] is None
    assert receipt["complete_career_coverage_verified"] is False


def test_entire_world_is_needed_for_jockey_and_trainer_support(source):
    target, _ = source
    day = target.race_date.max()
    current = target.loc[target.race_date.eq(day)].reset_index(drop=True)
    full = target.loc[target.race_date.lt(day)].reset_index(drop=True)
    current_horses = set(current.horse_id)
    # Remove complete old races containing unrelated historical IDs. This is
    # structurally valid but is not the same aggregate universe or contract.
    selected_ids = full.loc[full.horse_id.isin(current_horses)].race_id.unique()
    # Choose a deliberate narrow set of complete races, not runner deletion.
    narrow = full.loc[full.race_id.isin(selected_ids[-3:])].reset_index(drop=True)
    expected, _ = features.build_current_features(current, full)
    different, _ = features.build_current_features(current, narrow)
    assert not expected.safe_search_log_jockey_starts.equals(different.safe_search_log_jockey_starts)


def quoted_field(source):
    target, _ = source
    result = target.loc[target.race_date.eq(target.race_date.max())].copy().reset_index(drop=True)
    result["expected_runner_count"] = result.num_horses
    return result


def test_market_features_quote_only_complete_and_tie_rank_min(source):
    frame = quoted_field(source)
    frame["odds"] = [4., 4., 8.]
    original = frame.copy()
    result = features.add_market_features(frame)
    np.testing.assert_allclose(result.normalized_market_probability, [.4, .4, .2])
    np.testing.assert_array_equal(result.market_rank, [1., 1., 3.])
    np.testing.assert_array_equal(result.field_size, [3., 3., 3.])
    pd.testing.assert_frame_equal(original, frame)
    features.worker.market_values(result)


def test_market_quote_computation_is_outcome_independent(source):
    frame = quoted_field(source)
    expected = features.add_market_features(frame)
    for column in ("finish", "tansho_payout", "time_seconds"):
        frame[column] = object()
    actual = features.add_market_features(frame)
    pd.testing.assert_frame_equal(expected.loc[:, list(features.MARKET_COLUMNS)], actual.loc[:, list(features.MARKET_COLUMNS)])


@pytest.mark.parametrize("kind", ["missing-odds", "partial", "duplicate", "count", "num-horses", "invalid-odds", "number"])
def test_quote_fields_fail_closed(source, kind):
    frame = quoted_field(source)
    if kind == "missing-odds":
        frame = frame.drop(columns=["odds"])
    elif kind == "partial":
        frame = frame.iloc[:-1]
    elif kind == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    elif kind == "count":
        frame.loc[0, "expected_runner_count"] = 2
    elif kind == "num-horses":
        frame.loc[0, "num_horses"] = 4
    elif kind == "invalid-odds":
        frame.loc[0, "odds"] = np.nan
    else:
        frame.loc[0, "horse_number"] = 0
    with pytest.raises(ValueError):
        features.add_market_features(frame)


def test_market_reordered_rows_return_identical_key_values(source):
    frame = quoted_field(source)
    before = features.add_market_features(frame).set_index(["race_id", "horse_number"])
    after = features.add_market_features(frame.sample(frac=1., random_state=9)).set_index(["race_id", "horse_number"])
    pd.testing.assert_frame_equal(before.sort_index(), after.sort_index())


def test_feature_source_drift_is_rejected(monkeypatch):
    altered = deepcopy(features._IMPORTED_SOURCES)
    altered[next(iter(altered))] = "d" * 64
    monkeypatch.setattr(features, "_IMPORTED_SOURCES", altered)
    with pytest.raises(ValueError, match="changed-since-import"):
        features.source_hashes()

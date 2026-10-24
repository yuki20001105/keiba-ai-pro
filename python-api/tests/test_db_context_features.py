"""Fixed context profile: PIT, population denominators, missingness and parity."""
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pytest

from experiments import dataset as canonical
from training import db_context_features as context
from training import db_shared_features as shared
from training import db_shared_optimized as optimized


def fixture():
    rows = []
    for index, day in enumerate(("20130101", "20240101", "20240103", "20250105")):
        for number in range(1, 5):
            row = {"race_id": f"{day[:4]}0501{index+1:02d}01", "race_date": day,
                "horse_id": f"h{number}", "horse_number": number, "distance": 1600,
                "surface": "turf", "venue": "東京", "num_horses": 4, "finish": number,
                "time_seconds": 90.0 + index + number, "last_3f_time": 34.0 + number,
                "odds": 2.0+number, "popularity": number, "bracket_number": number,
                "age": 4, "sex": "牡", "burden_weight": 57.0,
                "horse_weight": 470.0+index*2+number, "horse_weight_change": 2.0,
                "field_condition": "良", "weather": "晴", "race_class": "未勝利",
                "jockey_id": "j1", "trainer_id": "t1", "sire": "父", "damsire": "母父"}
            row[context._CORNER_COLUMN] = (number, number)
            rows.append(row)
    return pd.DataFrame(rows)


def write_db(path, source):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT,created_at TEXT DEFAULT '2026-10-12')")
    connection.execute("CREATE TABLE race_results_ultimate(race_id TEXT,data TEXT,created_at TEXT DEFAULT '2026-10-12')")
    connection.execute("CREATE INDEX rr_race ON race_results_ultimate(race_id)")
    connection.execute("CREATE INDEX rr_horse ON race_results_ultimate(json_extract(data,'$.horse_id'))")
    for rid, group in source.groupby("race_id", sort=False):
        info = group.iloc[0].drop(context._CORNER_COLUMN).to_dict()
        connection.execute("INSERT INTO races_ultimate(race_id,data) VALUES(?,?)", (rid, json.dumps(info, ensure_ascii=False)))
        for row in group.to_dict("records"):
            corners = row.pop(context._CORNER_COLUMN)
            row["corner_positions_list"] = list(corners) if corners else []
            row["corner_positions"] = "-".join(map(str, corners)) if corners else ""
            row["weight_diff"] = row.pop("horse_weight_change")
            connection.execute("INSERT INTO race_results_ultimate(race_id,data) VALUES(?,?)", (rid, json.dumps(row, ensure_ascii=False)))
    connection.commit()
    connection.close()
    return path


@pytest.fixture(autouse=True)
def clear_in_process_state():
    with context._SERVING_LOCK:
        context._SERVING_STATES.clear()
    yield
    with context._SERVING_LOCK:
        context._SERVING_STATES.clear()


def test_fixed_schema_and_original46_are_unchanged():
    source = fixture()
    target = source.loc[source.race_date.ge("20240101")]
    actual = context.build_training_features(target, source)
    expected = optimized.select_features(shared.build_training_features(target, source))
    pd.testing.assert_frame_equal(actual[list(optimized.FEATURE_COLUMNS)], expected, check_exact=True, check_flags=False)
    assert len(context.FEATURE_COLUMNS) == 80
    assert len(context.EXTRA_COLUMNS) == 34
    assert len(set(context.FEATURE_COLUMNS)) == 80
    assert tuple(context.CATEGORICAL_COLUMNS) == tuple(optimized.CATEGORICAL_COLUMNS)
    assert actual.attrs["current_outcomes_consumed"] is False
    assert actual.attrs["context_caveats"]["going_is_not_turf_damage"]


def test_target_outcomes_market_actual_style_and_comments_are_not_inputs():
    source = fixture()
    target = source.loc[source.race_date.eq("20250105")].copy()
    expected = context.build_training_features(target, source)
    for name in ("finish", "finish_position", "time_seconds", "finish_time", "last_3f_time", "odds", "popularity",
                 "running_style_num", "running_style", "race_pace_front", "comment", "speed_deviation"):
        target[name] = 999999
    target[context._CORNER_COLUMN] = [(4, 1)] * len(target)
    target["corner_positions_list"] = [(4, 1)] * len(target)
    pd.testing.assert_frame_equal(context.build_training_features(target, source), expected, check_exact=True)


def test_same_day_results_and_same_day_ancestry_never_enter_features():
    source = fixture()
    target = source.loc[source.race_date.eq("20250105")]
    expected = context.build_training_features(target, source)
    modified = source.copy()
    same = modified.race_date.eq("20250105")
    modified.loc[same, "finish"] = [4, 3, 2, 1]
    modified.loc[same, "time_seconds"] = 1000.0
    modified.loc[same, "sire"] = "未来矛盾父"
    for index in modified.index[same]:
        modified.at[index, context._CORNER_COLUMN] = (4, 1)
    pd.testing.assert_frame_equal(context.build_training_features(target, modified), expected, check_exact=True)


def test_future_history_fails_closed_and_no_race_id_order_cutoff():
    source = fixture()
    target = source.loc[source.race_date.eq("20240103")]
    with pytest.raises(ValueError, match="boundary"):
        context.build_training_features(target, source)


def test_history_missing_is_nan_but_observed_counts_are_zero():
    source = fixture()
    target = source.loc[source.race_date.eq("20250105")].copy()
    target["horse_weight"] = np.nan
    target["horse_weight_change"] = np.nan
    features = context.build_training_features(target, pd.DataFrame(columns=canonical.SOURCE_COLUMNS))
    assert features.ctx_style_count5.eq(0).all()
    assert features.ctx_weight_history_count5.eq(0).all()
    assert features.ctx_condition_starts.eq(0).all()
    assert features.ctx_field_style_coverage.eq(0).all()
    for name in ("ctx_front_rate5", "ctx_other_front_pressure", "ctx_field_front_pressure",
                 "ctx_weight_delta_last", "ctx_weight_std5", "ctx_sire_surface_starts",
                 "ctx_sire_surface_rank_mean", "ctx_condition_rank_mean"):
        assert features[name].isna().all(), name
    assert features.ctx_pedigree_verified_prior.eq(0).all()


def test_weight_trend_is_chronological_and_not_health_diagnosis():
    source = fixture()
    features = context.build_training_features(source.loc[source.race_date.eq("20250105")], source)
    assert features.ctx_weight_delta_last.eq(2).all()
    assert features.ctx_weight_trend5.eq(2).all()
    assert features.ctx_weight_delta_mean5.eq(4).all()
    assert features.ctx_weight_history_count5.eq(3).all()
    assert features.attrs["context_caveats"]["weight_is_not_health_diagnosis"] is True


def test_pedigree_conflict_is_prior_only_and_not_backfilled_from_future():
    source = fixture()
    source.loc[source.race_date.eq("20240103") & source.horse_id.eq("h1"), "sire"] = "別父"
    target = source.loc[source.race_date.ge("20240103")].copy()
    # h1 current pair is contradictory on 2024-01-03, unknown on 2025-01-05;
    # the unchanged h2 pair remains eligible. No global future correction.
    features = context.build_training_features(target, source)
    h1 = target.horse_id.eq("h1").to_numpy()
    assert features.loc[h1, "ctx_pedigree_verified_prior"].eq(0).all()
    assert features.loc[h1, "ctx_sire_surface_rank_mean"].isna().all()
    assert features.loc[~h1, "ctx_pedigree_verified_prior"].eq(1).all()


def test_population_pedigree_denominator_includes_other_horses():
    source = fixture()
    target = source.loc[source.race_date.eq("20250105")].copy()
    features = context.build_training_features(target, source)
    # Three prior complete races x four horses, not just three prior h1 runs.
    assert features.ctx_sire_surface_starts.eq(12).all()
    assert features.ctx_sire_surface_rank_mean.eq(.5).all()


def test_style_is_recorded_position_and_field_proxy_excludes_own():
    source = fixture()
    target = source.loc[source.race_date.eq("20250105")]
    features = context.build_training_features(target, source)
    assert features.ctx_early_position_mean5.tolist() == pytest.approx([0, 1/3, 2/3, 1])
    assert features.ctx_front_rate5.tolist() == [1, 1, 0, 0]
    assert features.ctx_field_front_pressure.eq(.5).all()
    assert features.ctx_other_front_pressure.tolist() == pytest.approx([1/3, 1/3, 2/3, 2/3])
    assert features.ctx_field_style_coverage.eq(1).all()
    assert features.attrs["context_caveats"]["pace_is_historical_style_proxy_not_lap_forecast"]


@pytest.mark.parametrize("value,count", [("**", 4), ([], 4), ([0, 1], 4), ([1, 5], 4), ([1, 2], None),
                                       ([True, 2], 4), ([1.2, 2], 4), ([1]*5, 4)])
def test_invalid_masked_corners_do_not_fabricate_style(value, count):
    assert context._corners(value, count) is None


def test_order_index_and_current_field_permutation_preserved():
    source = fixture()
    target = source.loc[source.race_date.eq("20250105")].sample(frac=1, random_state=3)
    target.index = pd.Index([7]*len(target), name="caller")
    actual = context.build_training_features(target, source)
    original = source.loc[source.race_date.eq("20250105")]
    expected = context.build_training_features(original, source)
    expected.index = original.horse_number
    expected = expected.loc[target.horse_number]
    expected.index = target.index
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)


def test_loader_readonly_extras_and_serving_match_training(tmp_path):
    source = fixture()
    path = write_db(tmp_path / "source.db", source)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    target, history, coverage = context.load_db_frames(path, "2025-01", "2025-01")
    assert len(target) == 4
    assert len(history) == 16
    assert coverage["context_corner_audit"]["available"] == 16
    expected = context.build_training_features(target, history)
    info = target.iloc[0].to_dict()
    current = target.copy()
    current["corner_positions_list"] = [[4, 1]]*4  # current outcome injection ignored
    actual, audit = context.build_serving_features(path, info, current)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert audit["expected_count"] == 80
    assert audit["card_capture_time_verified"] is False
    assert audit["same_day_or_future_history_used"] is False
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_corners_alias_conflict_is_missing_not_chosen(tmp_path):
    path = write_db(tmp_path / "source.db", fixture())
    connection = sqlite3.connect(path)
    connection.execute("UPDATE race_results_ultimate SET data=json_set(data,'$.corner_positions','4-1') "
                       "WHERE race_id='201305010101' AND json_extract(data,'$.horse_number')=1")
    connection.commit()
    connection.close()
    _, history, coverage = context.load_db_frames(path, "2025-01", "2025-01")
    selected = history.loc[history.race_id.eq("201305010101") & history.horse_number.eq(1)]
    assert selected[context._CORNER_COLUMN].iloc[0] is None
    assert coverage["context_corner_audit"]["identity_or_alias_conflicts"] == 1


@pytest.mark.parametrize("going", [None, "**", "荒れている", "晴", "unknown_local"])
def test_unknown_going_is_not_turf_damage_or_zero_condition_support(going):
    source = fixture()
    target = source.loc[source.race_date.eq("20250105")].copy()
    target["field_condition"] = going
    features = context.build_training_features(target, source)
    assert features.ctx_condition_starts.isna().all()
    assert features.ctx_condition_rank_mean.isna().all()
    assert features.ctx_sire_condition_starts.isna().all()
    assert features.ctx_gate_prior_count.isna().all()


def test_loaded_module_identity_is_bound(monkeypatch):
    monkeypatch.setattr(context, "_OWN_HASH", "0"*64)
    with pytest.raises(ValueError, match="changed-since-import"):
        context.feature_source_hashes()


def _serving_fixture(tmp_path):
    from services.prediction_source_revision import initialize_source_revision
    source = fixture()
    path = write_db(tmp_path / "source.db", source)
    assert initialize_source_revision(path.resolve(), local_enabled=True)
    current = source.loc[source.race_date.eq("20250105")].drop(columns=context._CORNER_COLUMN).reset_index(drop=True)
    return path, current.iloc[0].to_dict(), current


def test_same_day_state_reused_without_global_history_replay(tmp_path, monkeypatch):
    path, info, current = _serving_fixture(tmp_path)
    original = context.load_db_frames
    loads = []
    def tracked(*args, **kwargs):
        loads.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(context, "load_db_frames", tracked)
    first, audit1 = context.build_serving_features(path, info, current)
    other = {**info, "race_id": "202505010402"}
    current2 = current.assign(race_id=other["race_id"], finish=999, time_seconds=999,
                              running_style_num=999, corner_positions="4-1")
    second, audit2 = context.build_serving_features(path, other, current2)
    assert len(loads) == 1
    assert audit1["history_state_cache_hit"] is False
    assert audit2["history_state_cache_hit"] is True
    assert audit2["history_state_source_tracking"] is True
    pd.testing.assert_frame_equal(first[list(context.EXTRA_COLUMNS)], second[list(context.EXTRA_COLUMNS)], check_exact=True)
    assert len(context._SERVING_STATES) == 1


def test_source_update_invalidates_state_but_prediction_log_does_not(tmp_path, monkeypatch):
    path, info, current = _serving_fixture(tmp_path)
    first, before = context.build_serving_features(path, info, current)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE prediction_log(id INTEGER)")
    connection.execute("INSERT INTO prediction_log VALUES(1)")
    connection.commit()
    connection.close()
    logged, unchanged = context.build_serving_features(path, info, current)
    assert unchanged["history_state_cache_hit"] is True
    assert unchanged["history_state_data_revision"] == before["history_state_data_revision"]
    pd.testing.assert_frame_equal(first, logged, check_exact=True)
    connection = sqlite3.connect(path)
    connection.execute("UPDATE race_results_ultimate SET data=json_set(data,'$.horse_weight',500) "
                       "WHERE race_id='202405010301' AND json_extract(data,'$.horse_number')=1")
    connection.commit()
    connection.close()
    updated, after = context.build_serving_features(path, info, current)
    assert after["history_state_cache_hit"] is False
    assert after["history_state_data_revision"] != before["history_state_data_revision"]
    assert updated.ctx_weight_delta_last.iloc[0] != first.ctx_weight_delta_last.iloc[0]
    assert len(context._SERVING_STATES) == 1


def test_quality_only_exclusion_invalidates_context_population(tmp_path):
    path, info, current = _serving_fixture(tmp_path)
    first, before = context.build_serving_features(path, info, current)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE scrape_race_acquisition_state(race_id TEXT,lifecycle TEXT,quality_status TEXT,excluded_from_standard INTEGER)")
    connection.execute("INSERT INTO scrape_race_acquisition_state VALUES('202405010301','settled','incomplete',1)")
    connection.commit()
    connection.close()
    second, after = context.build_serving_features(path, info, current)
    assert after["history_state_data_revision"] == before["history_state_data_revision"]
    assert after["history_state_cache_hit"] is False
    assert second.ctx_sire_surface_starts.iloc[0] == first.ctx_sire_surface_starts.iloc[0] - 4


def test_concurrent_serving_requests_share_single_preparation(tmp_path, monkeypatch):
    path, info, current = _serving_fixture(tmp_path)
    original = context.load_db_frames
    loads = []
    def tracked(*args, **kwargs):
        loads.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(context, "load_db_frames", tracked)
    with ThreadPoolExecutor(max_workers=4) as workers:
        responses = list(workers.map(lambda _: context.build_serving_features(path, info, current), range(8)))
    assert len(loads) == 1
    assert sum(not audit["history_state_cache_hit"] for _, audit in responses) == 1
    for result, _ in responses:
        pd.testing.assert_frame_equal(result, responses[0][0], check_exact=True)
    snapshot = next(iter(context._SERVING_STATES.values()))
    with pytest.raises(TypeError):
        snapshot["context"]["states"][("malicious",)] = (1, 1, 1)


def test_caller_audit_mutation_does_not_change_cached_state(tmp_path):
    path, info, current = _serving_fixture(tmp_path)
    first, audit = context.build_serving_features(path, info, current)
    audit["history_audit"]["context_caveats"]["going_is_not_turf_damage"] = False
    audit["context_audit"]["target_corners_or_outcomes_consumed"] = True
    second, next_audit = context.build_serving_features(path, info, current)
    assert next_audit["history_audit"]["context_caveats"]["going_is_not_turf_damage"] is True
    assert next_audit["context_audit"]["target_corners_or_outcomes_consumed"] is False
    pd.testing.assert_frame_equal(first, second, check_exact=True)


def test_source_changes_during_preparation_are_not_cached(tmp_path, monkeypatch):
    path, info, current = _serving_fixture(tmp_path)
    original = context.load_db_frames
    def mutating(*args, **kwargs):
        result = original(*args, **kwargs)
        connection = sqlite3.connect(path)
        connection.execute("UPDATE race_results_ultimate SET data=json_set(data,'$.horse_weight',501) "
                           "WHERE race_id='202405010301' AND json_extract(data,'$.horse_number')=1")
        connection.commit()
        connection.close()
        return result
    monkeypatch.setattr(context, "load_db_frames", mutating)
    with pytest.raises(ValueError, match="changed-during-preparation"):
        context.build_serving_features(path, info, current)
    assert not context._SERVING_STATES


def test_cached_baseline_parity_with_missing_inputs_and_unseen_horse(tmp_path):
    path, info, current = _serving_fixture(tmp_path)
    current.loc[0, "horse_id"] = "new-horse"
    current.loc[1, "horse_id"] = ""
    current.loc[2, ["horse_weight", "horse_weight_change", "age"]] = np.nan
    current.loc[3, ["sire", "damsire"]] = None
    _, history, _ = context.load_db_frames(path, "2013-01", "2025-01-05")
    past = history.loc[history.race_date.lt("20250105")]
    canonical_current = shared._current_card(info, current)
    expected = context.build_training_features(canonical_current, past)
    actual, _ = context.build_serving_features(path, info, current)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)


def test_source_changes_after_state_read_are_rejected(tmp_path, monkeypatch):
    path, info, current = _serving_fixture(tmp_path)
    context.build_serving_features(path, info, current)
    original = context._current_baseline
    def mutating(*args, **kwargs):
        result = original(*args, **kwargs)
        connection = sqlite3.connect(path)
        connection.execute("UPDATE race_results_ultimate SET data=json_set(data,'$.horse_weight',502) "
                           "WHERE race_id='202405010301' AND json_extract(data,'$.horse_number')=1")
        connection.commit()
        connection.close()
        return result
    monkeypatch.setattr(context, "_current_baseline", mutating)
    with pytest.raises(ValueError, match="changed-during-prediction"):
        context.build_serving_features(path, info, current)


def test_cached_baseline_uses_race_date_not_venue_race_id_order(tmp_path):
    from services.prediction_source_revision import initialize_source_revision
    source = fixture()
    earlier = source.race_date.eq("20240101")
    source.loc[earlier, ["race_id", "venue"]] = ["202408010201", "京都"]
    path = write_db(tmp_path / "source.db", source)
    assert initialize_source_revision(path.resolve(), local_enabled=True)
    current = source.loc[source.race_date.eq("20250105")].drop(columns=context._CORNER_COLUMN).reset_index(drop=True)
    _, history, _ = context.load_db_frames(path, "2013-01", "2025-01-05")
    past = history.loc[history.race_date.lt("20250105")]
    # The loader uses race_id-oriented SQL batches: the later Tokyo race is
    # before the earlier Kyoto race. This is not chronological horse history.
    h1_days = past.loc[past.horse_id.eq("h1"), "race_date"].tolist()
    assert h1_days == ["20130101", "20240103", "20240101"]
    expected = context.build_training_features(current, past)
    actual, audit = context.build_serving_features(path, current.iloc[0].to_dict(), current)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert actual.safe_prev_time_seconds.iloc[0] == 93.0
    assert actual.safe_prev2_time_seconds.iloc[0] == 92.0
    assert audit["same_day_or_future_history_used"] is False

"""New DB contract parity, PIT and RO checks using isolated SQLite fixtures."""
from copy import deepcopy
import hashlib
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from experiments import dataset as canonical
from experiments import features as frozen
from forward_adapters import feature_state
from training import db_shared_features as shared


def rows():
    result = []
    for index, day in enumerate(("20130101", "20240101", "20240103", "20250105")):
        for number in range(1, 5):
            result.append({
                "race_id": f"{day[:4]}0501{index + 1:02d}01", "race_date": day,
                "horse_id": f"horse-{number}", "horse_number": number,
                "distance": 1600, "surface": "turf", "venue": "東京", "num_horses": 4,
                "finish": number, "time_seconds": 90.0 + index + number,
                "last_3f_time": 34.0 + number,
                "bracket_number": number, "age": 4, "sex": "牡", "burden_weight": 57.0,
                "horse_weight": 470.0 + number, "horse_weight_change": float(number - 2),
                "field_condition": "良", "weather": "晴", "race_class": "未勝利",
                "odds": 2.0 + number, "popularity": number,
                "jockey_id": "j1", "trainer_id": "t1", "sire": "父", "damsire": "母父",
            })
    return pd.DataFrame(result, columns=canonical.SOURCE_COLUMNS)


def write_db(path, frame=None, *, indexed=True):
    source = rows() if frame is None else frame
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT)")
    connection.execute("CREATE TABLE race_results_ultimate(race_id TEXT,data TEXT)")
    connection.execute("CREATE INDEX runner_race ON race_results_ultimate(race_id)")
    for rid, group in source.groupby("race_id", sort=False):
        info = group.iloc[0].to_dict()
        clean = lambda raw: {name: None if pd.isna(value) else value for name, value in raw.items()}
        connection.execute("INSERT INTO races_ultimate VALUES (?,?)", (rid, json.dumps(clean(info), ensure_ascii=False, allow_nan=False)))
        for raw in group.to_dict("records"):
            # The current scraper spelling is deliberately only weight_diff.
            raw["weight_diff"] = raw.pop("horse_weight_change")
            connection.execute("INSERT INTO race_results_ultimate VALUES (?,?)", (rid, json.dumps(clean(raw), ensure_ascii=False, allow_nan=False)))
    if indexed:
        connection.execute("CREATE INDEX runner_horse ON race_results_ultimate(json_extract(data,'$.horse_id'))")
    connection.commit()
    connection.close()
    return path


def test_contract_is_exact_raw47_and_dependencies_are_bound():
    assert shared.FEATURE_VERSION == "local-db-shared-v1"
    assert shared.FEATURE_COLUMNS == list(frozen.FEATURE_GROUPS["base"]) + list(frozen.HISTORY_COLUMNS)
    assert len(shared.FEATURE_COLUMNS) == 47
    assert len(shared.CATEGORICAL_COLUMNS) == 6
    hashes = shared.source_hashes()
    assert "training.db_shared_features" in hashes
    assert {"experiments.dataset", "experiments.features", "experiments.training", "training.eligibility", "forward_adapters.feature_state"}.issubset(hashes)
    assert all(len(value) == 64 for value in hashes.values())
    assert not {"odds", "popularity", "finish", "time_seconds", "last_3f_time"}.intersection(shared.FEATURE_COLUMNS)


def test_loaded_formula_cannot_attest_newly_changed_disk_hash(monkeypatch):
    modified = {**shared._IMPORTED_SOURCE_HASHES, "training.db_shared_features": "0" * 64}
    monkeypatch.setattr(shared, "_source_module_hashes", lambda: modified)
    with pytest.raises(ValueError, match="changed-since-import"):
        shared.source_hashes()


def test_one_pass_features_equal_frozen_training_and_current_state():
    source = rows()
    target = source.loc[source.race_date.ge("20240101")].copy()
    expected, _ = frozen.engineer_features(source)
    expected = expected.loc[expected.race_date.ge("20240101"), shared.FEATURE_COLUMNS].copy()
    expected.index = target.index
    actual = shared.build_training_features(target, source)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    day = "20240103"
    current = source.loc[source.race_date.eq(day)]
    past = source.loc[source.race_date.lt(day)]
    state = feature_state.build_history_state(past, target_date=day)
    forward = feature_state.build_current_features(state, current)[shared.FEATURE_COLUMNS]
    forward.index = current.index
    pd.testing.assert_frame_equal(shared.build_training_features(current, past), forward, check_exact=True)


def test_target_labels_market_and_profile_claims_are_not_inputs():
    source = rows()
    target = source.loc[source.race_date.ge("20240101")].copy()
    expected = shared.build_training_features(target, source)
    for name in ("finish", "finish_position", "time_seconds", "finish_time", "last_3f_time", "odds", "popularity", "speed_deviation", "prev_finish", "safe_prev_finish"):
        target[name] = 999999.0
    pd.testing.assert_frame_equal(shared.build_training_features(target, source), expected, check_exact=True)
    assert expected.attrs["current_outcomes_consumed"] is False


def test_order_and_index_are_preserved_even_nonmonotonic_index():
    source = rows()
    target = source.loc[source.race_date.ge("20240101")].sample(frac=1, random_state=3)
    target.index = pd.Index([7] * len(target), name="original")
    actual = shared.build_training_features(target, source)
    expected, _ = frozen.engineer_features(source)
    expected = expected.set_index(["race_id", "horse_number"]).loc[pd.MultiIndex.from_frame(target[["race_id", "horse_number"]]), shared.FEATURE_COLUMNS]
    expected.index = target.index
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)


def test_same_day_results_never_enter_history_but_later_day_does():
    source = rows()
    another = source.loc[source.race_date.eq("20240103")].copy()
    another["race_id"] = "202405010312"
    another["time_seconds"] = 999.0
    another["finish"] = [4, 3, 2, 1]
    both = pd.concat([source, another], ignore_index=True)
    target = source.loc[source.race_date.ge("20240103")]
    actual = shared.build_training_features(target, both)
    current = actual.loc[target.race_date.eq("20240103")]
    assert current.safe_prior_starts.eq(2).all()
    assert current.safe_prev_time_seconds.tolist() == [92.0, 93.0, 94.0, 95.0]
    assert actual.loc[target.race_date.eq("20250105"), "safe_prior_starts"].eq(4).all()
    assert actual.attrs["same_day_or_future_history_used"] is False


@pytest.mark.parametrize("mutation", ["future", "before2013", "partial", "duplicate", "venue", "unsettled", "missing_intermediate"])
def test_unsafe_history_or_cohort_fails_closed(mutation):
    source = rows()
    target = source.loc[source.race_date.ge("20240101")].copy()
    history = source.copy()
    if mutation == "future":
        history.loc[history.race_date.eq("20250105"), ["race_date", "race_id"]] = ["20260105", "202605010401"]
    elif mutation == "before2013":
        history.loc[history.race_date.eq("20130101"), ["race_date", "race_id"]] = ["20120101", "201205010101"]
    elif mutation == "partial":
        history = history.iloc[1:]
    elif mutation == "duplicate":
        history.loc[1, "horse_number"] = 1
    elif mutation == "venue":
        target["venue"] = "京都"
    elif mutation == "unsettled":
        history.loc[0, "finish"] = np.nan
    else:
        history = history.loc[history.race_date.ne("20240101")]
    with pytest.raises(ValueError):
        shared.build_training_features(target, history)


def test_missing_history_remains_nan_not_zero_and_no_id_is_not_fabricated():
    current = rows().loc[lambda value: value.race_date.eq("20250105")].copy()
    current["horse_weight_change"] = np.nan
    current["horse_id"] = ""
    current["weather"] = None
    actual = shared.build_training_features(current, pd.DataFrame(columns=canonical.SOURCE_COLUMNS))
    assert actual.safe_prior_starts.eq(0).all()
    assert actual.safe_past3_count.eq(0).all()
    assert actual.safe_past3_observed_coverage.eq(0).all()
    for name in ("safe_weight_change", "safe_prev_finish", "safe_prev_time_seconds", "safe_past3_avg_speed_mps", "safe_weather"):
        assert actual[name].isna().all()


def test_missing_ids_do_not_drop_verified_intermediate_target_cohorts(tmp_path):
    source = rows()
    source["horse_id"] = ""
    path = write_db(tmp_path / "source.sqlite", source)
    target, history, coverage = shared.load_db_frames(path, "202401", "202401")
    assert len(target) == len(history) == 8
    assert coverage["history"]["selected_target_races_retained"] == 2
    actual = shared.build_training_features(target, history)
    assert actual.safe_prior_starts.eq(0).all()
    assert actual.safe_prev_finish.isna().all()


@pytest.mark.parametrize("indexed", [True, False])
def test_db_period_loading_and_train_serve_exact_parity_are_readonly(tmp_path, indexed):
    path = write_db(tmp_path / "source.sqlite", indexed=indexed)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    target, history, coverage = shared.load_db_frames(path, "2024-01", "2024-01")
    assert len(target) == 8
    assert len(history) == 12
    assert target.race_date.min() == "20240101"
    assert target.race_date.max() == "20240103"
    assert coverage["target"]["eligible_races"] == 2
    assert coverage["history"]["eligible_races"] == 3
    assert coverage["selected_target_races_reused_without_runner_reload"] == 2
    assert coverage["runner_rows_loaded"] == 12  # no second decode of the 8 targets
    assert coverage["horse_lookup"] == ("indexed_batched_json_horse_id" if indexed else "single_compact_stream_no_horse_expression_index")
    features = shared.build_training_features(target, history)
    source = rows()
    current = source.loc[source.race_date.eq("20240103")].iloc[[2, 0, 3, 1]].copy()
    current["weight_diff"] = current.pop("horse_weight_change")
    info = current.iloc[0].to_dict()
    actual, diag = shared.build_serving_features(path, info, current)
    expected = features.loc[target.race_date.eq("20240103")].iloc[[2, 0, 3, 1]].copy()
    expected.index = current.index
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert diag["history_max_source_date"] == "20240101"
    assert diag["history_rows"] == 8
    assert diag["status"] == "unverified"
    assert diag["semantic_parity_verified"] is True
    assert diag["source_hash_matches"] is None
    assert diag["live_capture_hash_bound"] is False
    assert diag["card_capture_time_verified"] is False
    assert diag["calibration_verified"] is False
    assert diag["profitability_verified"] is False
    assert diag["missing_columns"] == diag["uncomputed_columns"] == []
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_complete_related_races_kept_even_other_runner_is_not_target_horse(tmp_path):
    source = rows()
    source.loc[source.race_date.eq("20130101") & source.horse_number.eq(4), "horse_id"] = "not-current-horse"
    path = write_db(tmp_path / "source.sqlite", source)
    target, history, _ = shared.load_db_frames(path, "20250105", "20250105")
    assert len(history.loc[history.race_date.eq("20130101")]) == 4
    assert "not-current-horse" in set(history.horse_id)
    actual = shared.build_training_features(target, history)
    assert actual.safe_prior_starts.tolist() == [3, 3, 3, 2]


@pytest.mark.parametrize("bad", ["missing_time", "missing_market", "duplicate", "metadata_duplicate", "ledger", "alias_conflict"])
def test_bad_historical_race_is_quarantined_as_complete_unit(tmp_path, bad):
    source = rows()
    day = source.race_date.eq("20240101")
    if bad == "missing_time":
        source.loc[day & source.horse_number.eq(2), "time_seconds"] = np.nan
    elif bad == "missing_market":
        source.loc[day, "odds"] = np.nan
    elif bad == "duplicate":
        source.loc[day & source.horse_number.eq(2), "horse_number"] = 1
    path = write_db(tmp_path / "source.sqlite", source)
    connection = sqlite3.connect(path)
    rid = source.loc[day, "race_id"].iloc[0]
    if bad == "metadata_duplicate":
        # Missing metadata has the same refusal policy as a duplicate source.
        connection.execute("DELETE FROM races_ultimate WHERE race_id=?", (rid,))
    elif bad == "ledger":
        connection.execute("CREATE TABLE scrape_race_acquisition_state(race_id TEXT,lifecycle TEXT,quality_status TEXT,excluded_from_standard INTEGER)")
        connection.execute("INSERT INTO scrape_race_acquisition_state VALUES (?,?,?,?)", (rid, "settled", "incomplete", 0))
    elif bad == "alias_conflict":
        raw = json.loads(connection.execute("SELECT data FROM race_results_ultimate WHERE race_id=? LIMIT 1", (rid,)).fetchone()[0])
        raw["horse_weight_change"] = 123.0
        connection.execute("UPDATE race_results_ultimate SET data=? WHERE race_id=? AND json_extract(data,'$.horse_number')=1", (json.dumps(raw), rid))
    connection.commit()
    connection.close()
    current = source.loc[source.race_date.eq("20250105")]
    _, diag = shared.build_serving_features(path, current.iloc[0].to_dict(), current)
    assert diag["history_rows"] == 8


@pytest.mark.parametrize("date_from,date_to", [("202413", "2025"), ("20240132", "20250101"), ("20250101", "20240101"), ("2012-01", "2024-01")])
def test_invalid_period_fails_before_source_scan(tmp_path, date_from, date_to):
    with pytest.raises(ValueError):
        shared.load_db_frames(tmp_path / "not-present.sqlite", date_from, date_to)


def test_unsupported_sidecar_fails_without_source_change(tmp_path):
    path = write_db(tmp_path / "source.sqlite")
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE official_history_entries(value INTEGER)")
    connection.execute("INSERT INTO official_history_entries VALUES (1)")
    connection.commit()
    connection.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="sidecar"):
        shared.load_db_frames(path, "202401", "202401")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_fitted_categories_are_not_refit_for_serving_unknowns():
    source = rows()
    raw = shared.build_training_features(source, source)
    raw.loc[0, "safe_weather"] = "雨"
    encoder = shared.FoldEncoder(shared.FEATURE_COLUMNS, shared.CATEGORICAL_COLUMNS)
    encoder.fit_transform(raw.iloc[:8])
    before = deepcopy(encoder.categories)
    raw = raw.copy()
    raw["safe_weather"] = "unseen-weather"
    encoded = encoder.transform(raw)
    assert encoder.categories == before
    assert encoded.safe_weather.isna().all()


def test_client_prestart_timestamps_do_not_create_immutable_capture_proof(tmp_path):
    path = write_db(tmp_path / "source.sqlite")
    current = rows().loc[lambda value: value.race_date.eq("20250105")].copy()
    current["data_observed_at"] = "2025-01-05T09:00:00+09:00"
    info = {**current.iloc[0].to_dict(), "post_time": "10:00", "data_observed_at": "2025-01-05T09:00:00+09:00"}
    _, diag = shared.build_serving_features(path, info, current)
    assert diag["status"] == "unverified"
    assert diag["issue_codes"] == ["card_capture_provenance_unverified"]
    assert diag["semantic_parity_verified"] is True
    assert diag["live_capture_hash_bound"] is False


def test_current_weight_alias_conflict_or_partial_card_is_rejected(tmp_path):
    path = write_db(tmp_path / "source.sqlite")
    current = rows().loc[lambda value: value.race_date.eq("20250105")].copy()
    info = current.iloc[0].to_dict()
    current["weight_diff"] = 999.0
    with pytest.raises(ValueError, match="alias"):
        shared.build_serving_features(path, info, current)
    with pytest.raises(ValueError, match="partial"):
        shared.build_serving_features(path, info, current.drop(columns="weight_diff").iloc[1:])

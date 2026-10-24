import copy
import json
import sqlite3

import joblib
import numpy as np
import pandas as pd
import pytest

from experiments.features import FEATURE_GROUPS, engineer_features
from experiments.search_features import SEARCH_FEATURES, derive_search_features
from market_quality.lineage import (_ScopedConnection, prepare_event_history, replay_event_features,
                                    replay_fold_predictions, sha256, validate_fold_contract)

FEATURES = [c for group in FEATURE_GROUPS.values() for c in group] + list(SEARCH_FEATURES)
FEATURES = FEATURES[:99]


class FrozenEncoder:
    feature_columns = FEATURES
    requested_columns = FEATURES

    def transform(self, frame):
        assert list(frame) == FEATURES
        assert "finish" not in frame
        return frame.fillna(0)

    def fit_transform(self, _):
        raise AssertionError("The auditor must not fit")


class FrozenModel:
    def predict(self, frame, num_threads):
        assert num_threads == 2
        return frame.iloc[:, 0].to_numpy(float) / 1000


def fold_fixture(tmp_path):
    contract = {"evaluation_year": 2021, "train_period": ["20160101", "20201231"],
                "dataset_sha256": "d" * 64, "feature_columns": FEATURES,
                "target": "rank_regression", "loss": "huber", "task": "ranking",
                "score_is_probability": False, "validation_used_for_fit_or_early_stopping": False,
                "market_features": False}
    artifact = tmp_path / "fold_2021.joblib"
    joblib.dump({"model": FrozenModel(), "encoder": FrozenEncoder(), "feature_columns": FEATURES,
                 "contract": contract}, artifact)
    fold = {"year": 2021, "contract": contract, "artifact": str(artifact), "sha256": sha256(artifact),
            "validation_rows": 2, "validation_races": 1}
    inputs = pd.DataFrame({c: [1., 2.] for c in FEATURES})
    inputs["race_id"], inputs["horse_number"], inputs["race_date"] = "202105010101", [1, 2], "20210105"
    inputs["finish"] = [2, 1]
    saved = inputs[["race_id", "horse_number", "race_date"]].copy()
    saved["fold_year"], saved["prediction"] = 2021, [.001, .002]
    return fold, inputs, saved


def test_fold_reinference_transform_only_and_exact(tmp_path):
    fold, inputs, saved = fold_fixture(tmp_path)
    calls = []
    result = replay_fold_predictions(fold, inputs, saved, feature_columns=FEATURES,
                                    dataset_sha256="d" * 64, checkpoint=calls.append, chunk_size=1)
    assert result["pass"] and result["max_absolute_score_difference"] == 0
    assert not result["fit_called"] and len(calls) == 3
    inputs["finish"] = [1, 2]
    assert replay_fold_predictions(fold, inputs, saved, feature_columns=FEATURES,
                                   dataset_sha256="d" * 64)["pass"]


def test_hash_mismatch_rejected_before_pickle_load(tmp_path, monkeypatch):
    fold, inputs, saved = fold_fixture(tmp_path)
    fold["sha256"] = "0" * 64
    monkeypatch.setattr(joblib, "load", lambda _: pytest.fail("Deserialized unverified pickle"))
    with pytest.raises(ValueError, match="hash mismatch"):
        replay_fold_predictions(fold, inputs, saved, feature_columns=FEATURES, dataset_sha256="d" * 64)


@pytest.mark.parametrize("key,value", [("train_period", ["20160101", "20211231"]),
                                      ("market_features", True), ("score_is_probability", True),
                                      ("target", "speed_deviation"),
                                      ("validation_used_for_fit_or_early_stopping", True)])
def test_bad_fold_contract_fails(tmp_path, key, value):
    fold, _, _ = fold_fixture(tmp_path)
    fold["contract"][key] = value
    with pytest.raises(ValueError, match="contract invalid"):
        validate_fold_contract(fold, FEATURES, "d" * 64)


def test_wrong_score_count_and_runner_identity_fail(tmp_path):
    fold, inputs, saved = fold_fixture(tmp_path)
    saved.loc[0, "prediction"] += .01
    result = replay_fold_predictions(fold, inputs, saved, feature_columns=FEATURES, dataset_sha256="d" * 64)
    assert not result["pass"] and result["mismatched_scores"] == 1
    saved.loc[0, "horse_number"] = 4
    with pytest.raises(ValueError, match="cohort mismatch"):
        replay_fold_predictions(fold, inputs, saved, feature_columns=FEATURES, dataset_sha256="d" * 64)


def history_fixture():
    rows = []
    for year, day in [(2013, "0105"), (2016, "0105"), (2021, "0105"), (2021, "0105"), (2022, "0105")]:
        r = len(rows) // 2 + 1
        for h in (1, 2):
            rows.append(dict(race_id=f"{year}0501{r:02d}01", race_date=f"{year}{day}", horse_id=f"h{h}",
                             horse_number=h, finish=h, time_seconds=100 + h, distance=1600.,
                             surface="turf", venue="東京", num_horses=2, jockey_id=f"j{h}", trainer_id="t1",
                             sire="s1", damsire="ds1", last_3f_time=34 + h,
                             horse_total_wins=999999, prev_race_date="20990101"))
    return pd.DataFrame(rows)


def expected_fixture(history):
    engineered, _ = engineer_features(history)
    engineered = engineered.loc[engineered.race_date.ge("20160101")].reset_index(drop=True)
    return pd.concat([engineered, derive_search_features(engineered)], axis=1)


def test_actual_event_replay_all99_and_prior_same_day_boundary():
    history = history_fixture()
    expected = expected_fixture(history)
    same_day = expected.loc[expected.race_date.eq("20210105")]
    assert same_day.safe_prior_starts.eq(2).all()
    assert same_day.safe_prev_finish.tolist() == [1., 2., 1., 2.]
    result = replay_event_features(history, expected, FEATURES)
    assert result["pass"] and result["features_checked"] == 99
    assert result["rows_checked"] == 8
    assert result["same_day_future_references"] == 0
    assert not result["historical_t5_availability_verified"]


def test_profile_totals_and_latest_scraped_history_never_influence_inputs():
    history = history_fixture()
    expected = expected_fixture(history)
    history["horse_total_wins"] = -987654
    history["prev_race_date"] = "20000101"
    assert replay_event_features(history, expected, FEATURES)["pass"]


def test_changed_feature_is_a_failed_gate_not_silent_imputation():
    history = history_fixture()
    expected = expected_fixture(history)
    expected.loc[0, "safe_prior_starts"] = 100
    result = replay_event_features(history, expected, FEATURES)
    assert not result["pass"] and result["mismatched_feature_rows"] == 1


def test_current_race_result_not_own_input_but_later_history_changes():
    history = history_fixture()
    expected = expected_fixture(history)
    history.loc[history.race_date.eq("20220105"), "finish"] = [2, 1]
    assert replay_event_features(history, expected, FEATURES)["pass"]
    history.loc[history.race_date.eq("20160105"), "finish"] = [2, 1]
    assert not replay_event_features(history, expected, FEATURES)["pass"]


def test_future_scope_and_unknown_feature_rejected():
    history = history_fixture()
    expected = expected_fixture(history)
    with pytest.raises(ValueError, match="99-feature"):
        replay_event_features(history, expected, FEATURES[:-1] + ["safe_current_result_finish"])
    history.loc[0, "race_date"] = "20230101"
    with pytest.raises(ValueError, match="Future/unscoped"):
        replay_event_features(history, expected, FEATURES)


def test_whitelisted_sql_cannot_read_later_rows_or_write():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE races_ultimate(race_id TEXT,data TEXT)")
    db.executemany("INSERT INTO races_ultimate VALUES(?,?)", [("201305010101", "old"), ("202205010101", "old"),
                                                               ("202305010101", "do not decode")])
    scoped = _ScopedConnection(db, lambda _: None)
    assert len(list(scoped.execute("SELECT race_id,data FROM races_ultimate"))) == 2
    for query in ["DELETE FROM races_ultimate", "SELECT * FROM races_ultimate", "PRAGMA writable_schema=ON"]:
        with pytest.raises(ValueError, match="Unapproved"):
            scoped.execute(query)


def test_snapshot_hash_rejected_before_connect(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.db"
    path.write_bytes(b"untrusted")
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("Opened unverified source"))
    with pytest.raises(ValueError, match="Snapshot hash mismatch"):
        prepare_event_history(path, expected_snapshot_sha256="0" * 64)


def test_snapshot_replays_real_loader_but_never_decodes_post2022(tmp_path):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE races_ultimate(race_id TEXT,data TEXT)")
        db.execute("CREATE TABLE race_results_ultimate(race_id TEXT,data TEXT)")
        for year in [2013, 2016, 2021, 2022]:
            rid = f"{year}05010101"
            metadata = dict(race_id=rid, date=f"{year}0105", venue="東京", distance=1600,
                            track_type="芝", num_horses=2)
            db.execute("INSERT INTO races_ultimate VALUES(?,?)", (rid, json.dumps(metadata)))
            for horse in [1, 2]:
                row = dict(race_id=rid, horse_id=f"h{horse}", horse_number=horse, finish=horse,
                           time_seconds=101 + horse, odds=2 + horse, popularity=horse)
                db.execute("INSERT INTO race_results_ultimate VALUES(?,?)", (rid, json.dumps(row)))
        db.execute("INSERT INTO races_ultimate VALUES('202305010101','not JSON; forbidden to decode')")
        db.execute("INSERT INTO race_results_ultimate VALUES('202305010101','not JSON; forbidden to decode')")
    before = sha256(path)
    frame, receipt = prepare_event_history(path, expected_snapshot_sha256=before)
    assert len(frame) == 8
    assert receipt["source_audit"]["source_races"] == 4
    assert receipt["source_audit"]["streamed_jra_runner_rows"] == 8
    assert receipt["scope"] == ["20130101", "20221231"]
    assert not receipt["post_2022_source_rows_decoded"]
    assert sha256(path) == before

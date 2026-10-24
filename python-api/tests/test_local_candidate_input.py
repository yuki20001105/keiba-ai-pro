"""Frozen candidate parity and fail-closed provenance, no fitting or HTTP."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import json

import numpy as np
import pandas as pd
import pytest

from experiments import features
from services import local_candidate_input as candidate


def rows():
    result = []
    for index, day in enumerate(("20130101", "20240901", "20251201", "20260101")):
        for number in range(1, 5):
            result.append({
                "race_id": f"{day[:4]}0501{index + 1:02d}01", "race_date": day,
                "horse_id": f"horse-{number}", "horse_number": number,
                "distance": 1600, "surface": "turf", "venue": "東京", "num_horses": 4,
                "finish": number, "time_seconds": 90.0 + number, "last_3f_time": 35.0 + number,
                "bracket_number": number, "age": 4, "sex": "牡", "burden_weight": 57,
                "horse_weight": 470 + number, "horse_weight_change": np.nan,
                "field_condition": "良", "weather": "晴", "race_class": "未勝利",
                "odds": 2.0 + number, "popularity": number,
                "post_time": "10:05", "data_observed_at": "2026-01-01T00:00:00+00:00",
            })
    return pd.DataFrame(result)


@pytest.fixture(scope="module", params=candidate.candidate_specs(), ids=lambda spec: spec["model_id"])
def bundle(request):
    return candidate.load_candidate_bundle(request.param["source_path"], model_id=request.param["model_id"])


def card_history():
    source = rows()
    return source.loc[source.race_date.eq("20260101")].reset_index(drop=True), source.loc[source.race_date.lt("20260101")].reset_index(drop=True)


def test_frozen_encoder_exact_parity_no_current_outcomes_consumed(bundle):
    current, history = card_history()
    expected, _ = features.engineer_features(rows())
    expected = bundle["encoder"].transform(expected.loc[expected.race_date.eq("20260101")]).reset_index(drop=True)
    encoded, audit = candidate.build_candidate_input(bundle, current, historical_frame=history)
    pd.testing.assert_frame_equal(encoded, expected, check_exact=True)
    assert list(encoded) == bundle["feature_columns"]
    assert audit["semantic_parity_verified"] is True
    assert audit["current_outcomes_consumed"] is False
    assert audit["history_max_source_date"] == "20251201"
    assert audit["same_day_or_future_history_used"] is False
    assert audit["score_is_probability"] is False
    assert audit["calibration_verified"] is False
    assert audit["profitability_verified"] is False
    assert bundle["model_id"] == bundle["_local_candidate"]["model_id"]
    poisoned = current.copy()
    for name in ("finish", "finish_position", "time_seconds", "finish_time", "last_3f_time", "odds", "popularity", "speed_deviation", "safe_prev_finish"):
        poisoned[name] = 999999
    second, _ = candidate.build_candidate_input(bundle, poisoned, historical_frame=history)
    pd.testing.assert_frame_equal(encoded, second, check_exact=True)


def test_current_response_order_preserved(bundle):
    current, history = card_history()
    expected, _ = candidate.build_candidate_input(bundle, current, historical_frame=history)
    shuffled = current.iloc[[3, 0, 2, 1]].reset_index(drop=True)
    encoded, _ = candidate.build_candidate_input(bundle, shuffled, historical_frame=history)
    pd.testing.assert_frame_equal(encoded, expected.iloc[[3, 0, 2, 1]].reset_index(drop=True), check_exact=True)


@pytest.mark.parametrize("mutation", ["venue", "partial", "duplicate_number", "duplicate_horse", "same_day", "future"])
def test_incomplete_card_or_future_history_rejected(bundle, mutation):
    current, history = card_history()
    if mutation == "venue":
        current["venue"] = "京都"
    elif mutation == "partial":
        current = current.iloc[1:]
    elif mutation == "duplicate_number":
        current.loc[1, "horse_number"] = 1
    elif mutation == "duplicate_horse":
        current.loc[1, "horse_id"] = "horse-1"
    else:
        changed_day = "20260101" if mutation == "same_day" else "20270101"
        history.loc[history.race_date.eq("20251201"), "race_date"] = changed_day
    with pytest.raises(ValueError):
        candidate.build_candidate_input(bundle, current, historical_frame=history)


@pytest.mark.parametrize("observed,start", [(None, "10:05"), ("2026-01-01T01:05:00Z", "10:05"), ("2026-01-02T00:00:00Z", "10:05"), ("2026-01-01T00:00:00", "10:05"), ("2026-01-01T00:00:00Z", None)])
def test_unknown_or_late_card_capture_never_authorizes_live_input(bundle, observed, start):
    current, history = card_history()
    current["data_observed_at"] = observed
    current["post_time"] = start
    _, audit = candidate.build_candidate_input(bundle, current, historical_frame=history)
    assert audit["status"] == "unverified"
    assert audit["card_capture_time_verified"] is False
    assert audit["issue_codes"] == ["card_capture_provenance_unverified", "card_capture_time_unverified"]
    assert audit["semantic_parity_verified"] is True  # formula, not as-of or win calibration


def test_known_pre_start_capture_is_separate_from_probability_and_profit(bundle):
    current, history = card_history()
    _, audit = candidate.build_candidate_input(bundle, current, historical_frame=history)
    assert audit["status"] == "unverified"
    assert audit["issue_codes"] == ["card_capture_provenance_unverified"]
    assert audit["card_capture_time_verified"] is True
    assert audit["live_capture_hash_bound"] is False
    assert audit["calibration_verified"] is False
    assert audit["profitability_verified"] is False


def test_missing_history_is_nan_but_observed_counts_keep_original_definition(bundle):
    current, _ = card_history()
    encoded, audit = candidate.build_candidate_input(bundle, current, historical_frame=pd.DataFrame())
    assert encoded.safe_weight_change.isna().all()
    if bundle["_local_candidate"]["candidate_id"] == "M02":
        assert encoded.safe_prior_starts.eq(0).all()
        assert encoded.safe_prev_finish.isna().all()
        assert encoded.safe_prev_speed_mps.isna().all()
        assert encoded.safe_past3_count.eq(0).all()
    assert audit["history_max_source_date"] is None
    assert audit["missing_columns"] == []


def test_fixed_prediction_and_shap_same_matrix(bundle):
    current, history = card_history()
    encoded, _ = candidate.build_candidate_input(bundle, current, historical_frame=history)
    before = encoded.copy(deep=True)
    raw, weights = candidate.predict_candidate_scores(bundle, encoded)
    explanations = candidate.explain_candidate_scores(bundle, encoded, top_n=len(encoded.columns))
    assert np.allclose(raw, bundle["model"].predict(encoded, num_threads=2), atol=0, rtol=0)
    assert np.isclose(weights.sum(), 1.0)
    assert np.isfinite(weights).all()
    assert all(np.isclose(value, item["base_value"] + sum(feature["contribution"] for feature in item["features"])) for value, item in zip(raw, explanations))
    assert all(item["method"] == "tree_shap" for item in explanations)
    assert all({"feature", "label", "description", "value", "contribution", "impact_pct", "direction"}.issubset(feature) for item in explanations for feature in item["features"])
    assert all(0 <= feature["impact_pct"] <= 100 for item in explanations for feature in item["features"])
    assert all(item["contribution_scale"] == "速度スコアへの寄与" for item in explanations)
    physical = candidate.candidate_speed_mps(bundle, raw, current)
    assert np.isfinite(physical).all()
    pd.testing.assert_frame_equal(encoded, before, check_exact=True)


def test_unknown_categories_remain_nan_no_encoder_fit(bundle):
    current, history = card_history()
    saved_categories = deepcopy(bundle["encoder"].categories)
    current["weather"] = "unknown-new-weather"
    encoded, _ = candidate.build_candidate_input(bundle, current, historical_frame=history)
    assert encoded.safe_weather.isna().all()
    assert bundle["encoder"].categories == saved_categories


def test_artifact_hash_checked_before_deserialization(tmp_path, monkeypatch):
    path = tmp_path / "unknown.joblib"
    path.write_bytes(b"not-a-trusted-pickle")
    monkeypatch.setattr(candidate.joblib, "load", lambda *args: pytest.fail("untrusted input deserialized"))
    with pytest.raises(ValueError, match="untrusted-artifact"):
        candidate.load_candidate_bundle(path)


def test_source_change_fails_closed(bundle, monkeypatch):
    current, history = card_history()
    monkeypatch.setattr(candidate, "_modules_verified", lambda: False)
    with pytest.raises(ValueError, match="feature-source-changed"):
        candidate.build_candidate_input(bundle, current, historical_frame=history)


def test_valid_pre_start_timestamp_is_not_live_purchase_input(bundle):
    from betting.value_policy import verified_prediction_input
    current, history = card_history()
    _, audit = candidate.build_candidate_input(bundle, current, historical_frame=history)
    assert audit["card_capture_time_verified"] is True
    assert audit["semantic_parity_verified"] is True
    assert audit["source_hash_matches"] is True
    assert audit["live_capture_hash_bound"] is False
    assert verified_prediction_input(audit) is False


def test_lightweight_source_validator_never_deserializes(bundle, monkeypatch):
    spec = bundle["_local_candidate"]
    monkeypatch.setattr(candidate.joblib, "load", lambda *args: pytest.fail("cache integrity check deserialized a model"))
    validated = candidate.validate_candidate_sources(spec["source_path"])
    assert validated["model_id"] == spec["model_id"]
    validated["sha256"] = "changed"
    assert candidate.validate_candidate_sources(spec["source_path"])["sha256"] == spec["sha256"]


def test_source_validator_checks_artifact_even_when_stat_identity_is_preserved(bundle, tmp_path):
    import os
    spec = bundle["_local_candidate"]
    path = tmp_path / "copied.joblib"
    body = Path(spec["source_path"]).read_bytes()
    path.write_bytes(body)
    before = path.stat()
    mutated = bytearray(body)
    mutated[-1] ^= 1
    path.write_bytes(mutated)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert (path.stat().st_size, path.stat().st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    with pytest.raises(ValueError, match="untrusted-artifact"):
        candidate.validate_candidate_sources(path)


def test_changed_frozen_source_rejected_before_deserialization(bundle, monkeypatch):
    monkeypatch.setattr(candidate, "_modules_verified", lambda: False)
    monkeypatch.setattr(candidate.joblib, "load", lambda *args: pytest.fail("changed feature sources deserialized a model"))
    with pytest.raises(ValueError, match="feature-source-changed"):
        candidate.load_candidate_bundle(bundle["_local_candidate"]["source_path"])
    with pytest.raises(ValueError, match="feature-source-changed"):
        candidate.validate_candidate_sources(bundle["_local_candidate"]["source_path"])


def test_model_training_boundary_enforced(bundle):
    current, history = card_history()
    day = bundle["contract"]["train_period"][1]
    current["race_date"] = day
    current["race_id"] = day[:4] + "05010101"
    with pytest.raises(ValueError, match="after-model-training"):
        candidate.build_candidate_input(bundle, current, historical_frame=history)


def test_readonly_related_history_preserves_complete_field_and_skips_today(tmp_path):
    current, history = card_history()
    source = pd.concat([history, current], ignore_index=True)
    path = tmp_path / "history.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("CREATE TABLE races_ultimate(race_id TEXT,data TEXT); CREATE TABLE race_results_ultimate(race_id TEXT,data TEXT);")
        for rid, group in source.groupby("race_id"):
            first = group.iloc[0].to_dict()
            first["date"] = first["race_date"]
            connection.execute("INSERT INTO races_ultimate VALUES (?,?)", (rid, json.dumps(first)))
            for row in group.to_dict("records"):
                connection.execute("INSERT INTO race_results_ultimate VALUES (?,?)", (rid, json.dumps(row)))
    canonical_card, _ = candidate._current_card(current, None)
    before = path.read_bytes()
    selected, audit = candidate.load_candidate_history(path, canonical_card)
    assert len(selected) == 12
    assert selected.race_date.lt("20260101").all()
    assert selected.groupby("race_id").size().eq(4).all()
    assert audit["same_day_or_future_races_excluded"] == 1
    assert path.read_bytes() == before


def test_specs_are_not_mutable_shared_registration():
    specs = candidate.candidate_specs()
    specs[0]["sha256"] = "fake"
    assert candidate.candidate_specs()[0]["sha256"] != "fake"

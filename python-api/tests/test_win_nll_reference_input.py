"""Reference package admission, frozen equations and non-purchase guards."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "scripts"), str(ROOT / "keiba")]
from services import win_nll_reference_input as service
publisher = importlib.import_module("publish_win_nll_reference")
from experiments import dataset as canonical
from forward_adapters import feature_state


@pytest.fixture
def card():
    info = {"race_id": "202605010101", "date": "20260105", "venue": "東京",
            "surface": "turf", "distance": 1600, "num_horses": 3, "race_class": "1勝"}
    rows = pd.DataFrame([{"horse_number": number, "horse_id": f"h{number}", "age": 4,
                         "sex": "牡", "burden_weight": 56, "odds": 3 + number,
                         "horse_weight": 480 + number, "weight_diff": number - 2}
                        for number in (3, 1, 2)], index=[13, 11, 12])
    return info, rows


@pytest.fixture
def history():
    return pd.DataFrame([{"race_id": "202505010101", "race_date": "20250105",
                         "horse_number": number, "horse_id": f"h{number}",
                         "venue": "東京", "surface": "turf", "distance": 1600, "num_horses": 3,
                         "finish": number, "time_seconds": 95 + number}
                        for number in (1, 2, 3)]).reindex(columns=canonical.SOURCE_COLUMNS)


@pytest.fixture
def unchecked_bundle(monkeypatch):
    worker, _features = service.modules()
    monkeypatch.setattr(service, "validate_wrapper", lambda *_: "a" * 64)
    return {"feature_columns": worker.feature_contract("expanding_race_nll")[0]}


def build(card, history, bundle):
    info, rows = card
    return service.build_input(bundle, rows, db_path=None, race_info=info,
                               model_path="unused", historical_frame=history)


def test_order_keys_and_frozen_formula_are_exact_not_zero_filled(card, history, unchecked_bundle):
    frame, evidence = build(card, history, unchecked_bundle)
    assert list(frame.index) == [13, 11, 12]
    assert frame.horse_number.tolist() == [3, 1, 2]
    assert len(frame.columns) == 59
    assert frame.safe_prev2_finish.isna().all()
    assert frame.safe_prev_finish.tolist() == [3, 1, 2]
    assert frame.safe_weight_change.tolist() == [1, -1, 0]
    assert frame.normalized_market_probability.sum() == pytest.approx(1)
    from training import db_shared_features as normal
    current = normal._current_card(card[0], card[1])
    generated = feature_state.build_current_features(feature_state.build_history_state(history, target_date="20260105"), current)
    expected = generated.set_index("horse_number").loc[frame.horse_number]
    _, features = service.modules()
    pd.testing.assert_frame_equal(frame[list(features.BASE46_COLUMNS)].reset_index(drop=True),
                                  expected[list(features.BASE46_COLUMNS)].reset_index(drop=True))
    assert evidence["semantic_parity_verified"] is False
    assert evidence["calibration_verified"] is False
    assert evidence["eligible_for_ev"] is False
    assert evidence["expected_count"] == evidence["actual_count"] == 50


def test_current_results_paid_profile_and_comments_never_reach_input(card, history, unchecked_bundle):
    expected, _ = build(card, history, unchecked_bundle)
    info, rows = card
    poisoned = rows.copy()
    for key in ("finish", "finish_time", "last_3f_time", "speed_figure", "max_index", "comment"):
        poisoned[key] = 999
    actual, evidence = build((info, poisoned), history, unchecked_bundle)
    pd.testing.assert_frame_equal(expected, actual)
    assert evidence["source_odds_kind"] == "historical_or_unverified"
    assert evidence["input_outcomes_consumed"] is False
    assert evidence["live_capture_hash_bound"] is False


@pytest.mark.parametrize("bad", ["missing_odds", "missing_age", "missing_burden", "partial", "duplicate", "jump", "before_cal", "metadata"])
def test_invalid_card_and_trained_future_knowledge_fail_closed(card, history, unchecked_bundle, bad):
    info, rows = deepcopy(card)
    if bad == "missing_odds": rows.loc[11, "odds"] = np.nan
    elif bad == "missing_age": rows.loc[11, "age"] = np.nan
    elif bad == "missing_burden": rows.loc[11, "burden_weight"] = np.nan
    elif bad == "partial": rows = rows.iloc[:2]
    elif bad == "duplicate": rows.loc[11, "horse_number"] = 3
    elif bad == "jump": info["race_class"] = "障害未勝利"
    elif bad == "before_cal": info.update(race_id="202505010101", date="20251231")
    elif bad == "metadata": rows["distance"] = 1800
    with pytest.raises(ValueError):
        build((info, rows), history, unchecked_bundle)


@pytest.mark.parametrize("when", ["20260105", "20261012", "20120105"])
def test_history_same_day_future_before2013_rejected(card, history, unchecked_bundle, when):
    history = history.copy()
    history["race_date"] = when
    history["race_id"] = when[:4] + "05010101"
    with pytest.raises(ValueError): build(card, history, unchecked_bundle)


def test_empty_history_has_explicit_missingness_not_fake_history(card, history, unchecked_bundle):
    frame, evidence = build(card, history.iloc[:0], unchecked_bundle)
    assert frame.safe_prev_finish.isna().all()
    assert evidence["history_rows"] == 0 and evidence["history_max_source_date"] is None
    assert evidence["history_freshness_verified"] is False


def test_predict_only_calls_original_worker_and_detects_mutated_input(card, history, unchecked_bundle, monkeypatch):
    frame, _ = build(card, history, unchecked_bundle)
    worker, _ = service.modules()
    monkeypatch.setattr(service, "_original_bundle", lambda *_: "original")
    unchecked_bundle.update(source_bundle_path="original", source_bundle_sha256="a" * 64)
    monkeypatch.setattr(worker, "predict_components", lambda original, supplied: {
        "probability": np.array([.1, .5, .4]), "p_original": np.array([.2, .45, .35]), "direct_logit": np.ones(3)})
    result = service.predict(unchecked_bundle, frame, model_path="unused")
    assert result["probability"].tolist() == [.1, .5, .4]
    frame.loc[13, "odds"] = 99
    with pytest.raises(ValueError, match="bound-outcome-free"):
        service.predict(unchecked_bundle, frame, model_path="unused")


@pytest.fixture
def published(tmp_path, monkeypatch):
    if not publisher.PLAN.exists(): pytest.skip("saved research artifacts not present")
    import app_config
    monkeypatch.setattr(app_config, "APP_ENV", "test")
    monkeypatch.setattr(app_config, "MODELS_DIR", tmp_path)
    return publisher.publish()


def test_real_saved_package_publication_has_no_fit_activation_or_encoder(published):
    bundle = service.load_published_bundle(published["model_path"])
    assert bundle["target"] == "win" and bundle["model"].num_feature() == 50
    assert "encoder" not in bundle
    assert bundle["probability_validation"] == service.probability_validation()
    assert bundle["purchase_authorized"] is False
    assert published["new_fits"] == 0 and published["model_activated"] is False
    assert bundle["display_name"] == "全履歴・勝率NLL（参考運用）"
    assert bundle["evaluation"]["primary"]["rank_correlation"] == pytest.approx(.538199281557977)
    assert bundle["evaluation"]["primary"]["rmse"] == pytest.approx(np.sqrt(.7882798016114803 * 2178 / 30115))
    assert bundle["evaluation"]["rmse_unit"] == "勝敗0/1"
    assert bundle["evaluation"]["unknown_period_validation"] is False
    assert bundle["evaluation"]["primary"]["win_roi"] is None
    with pytest.raises(FileExistsError): publisher.publish()


def test_actual_research_model_predictions_match_frozen_worker(published, card, history):
    bundle = service.load_published_bundle(published["model_path"])
    frame, _ = service.build_input(bundle, card[1], db_path=None, race_info=card[0],
                                  model_path=published["model_path"], historical_frame=history)
    original = service._original_bundle(bundle["source_bundle_path"], bundle["source_bundle_sha256"])
    worker, _ = service.modules()
    expected = worker.predict_components(original, frame)
    actual = service.predict(bundle, frame, model_path=published["model_path"])
    np.testing.assert_array_equal(expected["probability"], actual["probability"])
    np.testing.assert_array_equal(expected["p_original"], actual["raw_probability"])


@pytest.mark.parametrize("field", ["reference_only", "eligible_for_ev", "validated", "market_edge_verified", "profitability_verified"])
def test_permission_flags_cannot_be_upgraded_even_in_local_wrapper(published, field):
    bundle = service.load_published_bundle(published["model_path"])
    if field == "reference_only": bundle[field] = False
    else: bundle["probability_validation"][field] = True
    with pytest.raises(ValueError): service.validate_wrapper(bundle, published["model_path"])


def test_package_hash_verified_before_unpickling(published, monkeypatch):
    path = Path(published["model_path"])
    path.write_bytes(b"corrupted-test-package")
    import joblib
    monkeypatch.setattr(joblib, "load", lambda *_: pytest.fail("corrupted file reached pickle"))
    with pytest.raises(ValueError): service.load_published_bundle(path)


def test_local_only_admission(published, monkeypatch):
    import app_config
    monkeypatch.setattr(app_config, "APP_ENV", "production")
    with pytest.raises(ValueError, match="local-only"):
        service.load_published_bundle(published["model_path"])


def test_full_db_history_is_readonly_cached_and_excludes_target_day(tmp_path, card, history, unchecked_bundle):
    path = tmp_path / "history.db"
    history = history.copy()
    history["odds"], history["popularity"] = [2., 4., 8.], [1, 2, 3]
    future = history.copy()
    future["race_date"], future["race_id"], future["time_seconds"] = "20260105", "202605010102", 999.
    source = pd.concat([history, future], ignore_index=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY, data TEXT)")
        db.execute("CREATE TABLE race_results_ultimate(race_id TEXT, data TEXT)")
        for rid, group in source.groupby("race_id"):
            def clean(row): return {key: None if pd.isna(value) else value for key, value in row.items()}
            db.execute("INSERT INTO races_ultimate VALUES (?,?)", (rid, json.dumps(clean(group.iloc[0].to_dict()))))
            for row in group.to_dict("records"):
                db.execute("INSERT INTO race_results_ultimate VALUES (?,?)", (rid, json.dumps(clean(row))))
    before = service.file_hash(path)
    first, evidence = service.build_input(unchecked_bundle, card[1], db_path=path, race_info=card[0], model_path="unused")
    second, cached = service.build_input(unchecked_bundle, card[1], db_path=path, race_info=card[0], model_path="unused")
    pd.testing.assert_frame_equal(first, second)
    assert evidence["history_rows"] == 3 and evidence["history_max_source_date"] == "20250105"
    assert not evidence["history_cache_hit"] and cached["history_cache_hit"]
    assert first.safe_prev_time_seconds.tolist() == [98., 96., 97.]
    assert service.file_hash(path) == before
    # Coherent new committed DB snapshot invalidates the previous cached state.
    with sqlite3.connect(path) as db:
        row = json.loads(db.execute("SELECT data FROM race_results_ultimate WHERE race_id=? LIMIT 1", ("202505010101",)).fetchone()[0])
        row["time_seconds"] = 101.
        db.execute("UPDATE race_results_ultimate SET data=? WHERE race_id=? AND json_extract(data,'$.horse_number')=1", (json.dumps(row), "202505010101"))
    refreshed, changed = service.build_input(unchecked_bundle, card[1], db_path=path, race_info=card[0], model_path="unused")
    assert changed["history_cache_hit"] is False
    assert refreshed.loc[11, "safe_prev_time_seconds"] == 101.


def test_manifest_permission_or_artifact_hash_cannot_be_replaced(published, monkeypatch):
    path = service.manifest_path(published["model_path"])
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["purchase_authorized"] = True
    path.write_text(json.dumps(manifest), encoding="utf-8")
    import joblib
    monkeypatch.setattr(joblib, "load", lambda *_: pytest.fail("unauthorized manifest reached pickle"))
    with pytest.raises(ValueError): service.load_published_bundle(published["model_path"])


def test_fixed_original_plan_hash_checked_before_unpickling(published, monkeypatch):
    monkeypatch.setattr(service, "ORIGIN_PLAN_SHA256", "f" * 64)
    import joblib
    monkeypatch.setattr(joblib, "load", lambda *_: pytest.fail("unbound origin reached pickle"))
    with pytest.raises(ValueError, match="fixed-origin-plan"):
        service.load_published_bundle(published["model_path"])


def test_manifest_cannot_substitute_another_original_research_source(published, monkeypatch):
    path = service.manifest_path(published["model_path"])
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["original_artifacts"]["model"]["sha256"] = "f" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")
    import joblib
    monkeypatch.setattr(joblib, "load", lambda *_: pytest.fail("replacement origin reached pickle"))
    with pytest.raises(ValueError, match="fixed-origin-publication"):
        service.load_published_bundle(published["model_path"])

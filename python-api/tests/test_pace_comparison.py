"""Past-only style/field features and isolated retrospective comparison."""
import numpy as np
import pandas as pd
import pytest

from test_db_context_features import fixture
from training import db_context_features as context
from training import db_shared_grouped as grouped


def _cards():
    source = fixture()
    target = source.loc[source.race_date.eq("20250105")].copy().reset_index(drop=True)
    return target, source


def test_features_ignore_target_results_and_same_day_or_future_passages():
    from training.pace_features import build_style_features

    target, history = _cards()
    expected = build_style_features(target, history)
    target["finish"] = 99
    target["time_seconds"] = 1.0
    target["odds"] = 9999.0
    target["lap_sectional"] = [{"200": 1.0}] * len(target)
    target[context._CORNER_COLUMN] = [(4, 1)] * len(target)
    changed = history.copy()
    for index in changed.index[changed.race_date.ge("20250105")]:
        changed.at[index, context._CORNER_COLUMN] = (4, 1)
        changed.at[index, "finish"] = 99
    future = changed.loc[changed.race_date.eq("20250105")].copy()
    future["race_date"] = "20260101"
    actual = build_style_features(target, pd.concat([changed, future], ignore_index=True))
    pd.testing.assert_frame_equal(actual, expected)


def test_field_position_and_confirmed_competitors_use_past_styles():
    from training.pace_features import build_style_features

    target, history = _cards()
    result = build_style_features(target, history)
    np.testing.assert_allclose(result.pace_expected_position_rank, [0, 1 / 3, 2 / 3, 1])
    np.testing.assert_array_equal(result.pace_front_rival_count, [1, 1, 2, 2])
    np.testing.assert_array_equal(result.pace_lone_front, [0, 0, 0, 0])
    assert result.pace_pressure_coverage.eq(1).all()
    expected = context._extra_features(target, history)[context.FEATURE_GROUPS["style_pace"]]
    pd.testing.assert_frame_equal(result[expected.columns], expected)


def test_missing_style_is_unknown_not_evidence_of_an_uncontested_leader():
    from training.pace_features import build_style_features

    target, history = _cards()
    for index in history.index[history.horse_number.eq(4)]:
        history.at[index, context._CORNER_COLUMN] = None
    result = build_style_features(target, history)
    assert result.pace_pressure_coverage.eq(.75).all()
    assert result.pace_lone_front.isna().all()
    assert np.isnan(result.loc[3, "pace_expected_position_rank"])
    assert np.isnan(result.loc[3, "pace_front_rival_count"])
    empty = build_style_features(target, history.iloc[:0])
    assert empty.ctx_style_count5.eq(0).all()
    assert empty.pace_pressure_coverage.eq(0).all()
    assert empty.pace_front_rival_count.isna().all()


def test_tied_positions_are_order_independent():
    from training.pace_features import build_style_features

    target, history = _cards()
    for index in history.index[history.horse_number.eq(2)]:
        history.at[index, context._CORNER_COLUMN] = (1, 1)
    expected = build_style_features(target, history)
    actual = build_style_features(target.iloc[::-1], history.iloc[::-1]).sort_index()
    pd.testing.assert_frame_equal(actual, expected)
    np.testing.assert_allclose(expected.pace_expected_position_rank[:2], [1 / 6, 1 / 6])


def test_relative_position_rejects_an_incomplete_field():
    from training.pace_features import build_style_features

    target, history = _cards()
    with pytest.raises(ValueError, match="complete"):
        build_style_features(target.iloc[:-1], history)


def test_training_reuses_conditioned_baseline_and_preserves_production_modules():
    from training.pace_comparison import fit_candidate
    from training.pace_features import FEATURE_COLUMNS, build_style_features
    from test_db_context_fit import sample
    from training import db_context_fit as original
    from training import db_shared_fit as legacy

    target, old = sample()
    styles = build_style_features(target, target.assign(**{
        context._CORNER_COLUMN: [(number, number) for number in target.horse_number],
    }))
    inputs = pd.concat([old[list(grouped.FEATURE_COLUMNS)], styles], axis=1)[list(FEATURE_COLUMNS)]
    splits = legacy.date_partitions(target)
    before = grouped.source_hashes()
    old_baseline_function = legacy.fit_speed_deviation_baseline
    bundle, result, output = fit_candidate(target, inputs, splits, "style")
    expected = grouped.grouping.fit_group_baseline(target.iloc[splits["TRAIN"]], grouped.ARM)
    assert grouped._baseline_hash(bundle["baseline"]) == grouped._baseline_hash(expected)
    assert result["partitions"]["TEST"]["race_count"] == output["target"].race_id.nunique()
    assert len(bundle["feature_columns"]) == 55
    assert bundle["reference_only"] is True
    assert bundle["probability_validation"]["eligible_for_ev"] is False
    assert legacy.fit_speed_deviation_baseline is old_baseline_function
    assert grouped.source_hashes() == before
    with pytest.raises(ValueError):
        original._validate_prediction_bundle(bundle)


def test_axis_forecasts_require_matching_base_model_and_strictly_previous_dates():
    from betting import adaptive_strategy as ai
    from training.pace_comparison import predict_axis

    frame = pd.DataFrame([{**dict.fromkeys(ai.NUMERIC, 0.0),
        **dict.fromkeys(ai.CATEGORICAL, "unknown"), "race_date": "20260102"}])
    bundle = {"base_model_sha256": "new-base", "fitted_through": "20251231",
              "model": {"constant_probability": .7}}
    assert predict_axis(bundle, frame, "new-base")[0] == .7
    with pytest.raises(ValueError, match="base"):
        predict_axis(bundle, frame, "old-base")
    frame["race_date"] = "20251231"
    with pytest.raises(ValueError, match="past"):
        predict_axis(bundle, frame, "new-base")


def test_unknown_trifecta_prices_cannot_be_reported_as_zero_returns():
    from training.pace_comparison import settle_book

    with pytest.raises(ValueError, match="official"):
        settle_book({1: .1, 2: .2}, {"ワイド": {}}, selected=True)


def test_task_input_tampering_invalidates_approval(tmp_path):
    from training.pace_comparison import check_approval, sha

    source = tmp_path / "features.parquet"
    source.write_bytes(b"frozen")
    task = {"inputs": {"features": {"path": str(source), "sha256": sha(source)}}}
    approval = {"task_sha256": "hash", "production_approval": False, "acquisition_approved": False}
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="input"):
        check_approval(task, approval, "hash")


def test_independent_style_rebuild_matches_strict_prior_passages():
    from training.pace_verification import rebuild_styles

    target, history = _cards()
    actual = rebuild_styles(target, history)
    expected = context._extra_features(target, history)[context.FEATURE_GROUPS["style_pace"]]
    pd.testing.assert_frame_equal(actual, expected)


def test_independent_book_uses_h2_h3_after_axis_exclusion():
    from training.pace_verification import book_money

    field = pd.DataFrame({
        "horse_number": [1, 2, 3, 4, 5, 6],
        "probability": [.30, .25, .20, .15, .06, .04],
        "odds": [2, 3, 4, 7, 15, 20],
    })
    dividends = {"ワイド": {"1-3": 200}, "三連単": {"5-1-3": 1000}}
    result = book_money(field, dividends, True)
    assert result["cost_yen"] == 1500
    assert result["return_yen"] == 6000
    assert result["hit_ticket_count"] == 2
    assert set(result["combinations"]) == {"1-3", "5-1-3", "3-1-5"}


def test_replay_summary_exposes_the_existing_gross_return_rate(monkeypatch):
    from experiments.common import REPO_ROOT
    from training.pace_comparison import _risk

    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts"))
    rows = pd.DataFrame([{"race_id": "race1", "race_date": "20260102", "cost_yen": 1500,
                          "return_yen": 6000, "ticket_count": 3, "hit_ticket_count": 2}])
    result = _risk(rows)
    assert result["roi_percent"] == 400
    assert result["net_yen"] == 4500


def test_frozen_task_survives_real_json_serialization(tmp_path):
    from training import pace_comparison as comparison

    path = tmp_path / "features.parquet"
    path.write_bytes(b"approved")
    task = {"protocol": comparison.PROTOCOL, "sources": comparison._sources(),
            "protected": comparison._protected(), "inputs": {
                "features.parquet": {"path": str(path), "sha256": comparison.sha(path)}}}
    comparison.write(tmp_path / "task-ledger.json", task)
    assert comparison._task(tmp_path)["protocol"] == comparison.PROTOCOL


def test_relocated_task_cannot_read_unbound_root_features(tmp_path, monkeypatch):
    import json
    from training import pace_comparison as comparison

    monkeypatch.setattr(comparison, "PROTOCOL", json.loads(json.dumps(comparison.PROTOCOL)))
    approved = tmp_path / "original" / "features.parquet"
    approved.parent.mkdir()
    approved.write_bytes(b"approved")
    copied = tmp_path / "copied"
    copied.mkdir()
    (copied / "features.parquet").write_bytes(b"not-approved")
    task = {"protocol": comparison.PROTOCOL, "sources": comparison._sources(),
            "protected": comparison._protected(), "inputs": {
                "features.parquet": {"path": str(approved), "sha256": comparison.sha(approved)}}}
    comparison.write(copied / "task-ledger.json", task)
    with pytest.raises(ValueError, match="approved-feature-path"):
        comparison._task(copied)


def test_last_axis_fit_checks_deadline_before_publishing(tmp_path):
    from training.pace_comparison import _forecast

    rows = []
    for index, day in enumerate(("20240831", "20240901", "20250102")):
        for number, probability in enumerate((.30, .25, .20, .15, .06, .04), start=1):
            rows.append({"race_id": f"race{index}", "race_date": day, "horse_number": number,
                "num_horses": 6, "probability": probability, "odds": number + 1,
                "finish": number, "distance": 1600, "surface": "turf", "venue": "Tokyo",
                "field_condition": "good", "race_class": "maiden"})
    calls = 0

    def deadline():
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise TimeoutError("budget-exhausted")

    with pytest.raises(TimeoutError):
        _forecast(tmp_path, "candidate", pd.DataFrame(rows), "base", deadline)
    assert not list(tmp_path.rglob("axis_models\\*.json"))


def test_versioned_verifier_handles_groupby_without_changing_frozen_source(monkeypatch):
    from experiments.common import REPO_ROOT
    from training import pace_comparison as comparison

    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts"))
    from verify_pace_goal_loop import _grouping_dict

    before = comparison._sources()
    rows = pd.DataFrame({"race_id": ["a", "a", "b"], "value": [1, 2, 3]})
    groups = _grouping_dict(rows.groupby("race_id"))
    assert groups["a"].value.tolist() == [1, 2]
    assert groups["b"].value.tolist() == [3]
    assert comparison._sources() == before

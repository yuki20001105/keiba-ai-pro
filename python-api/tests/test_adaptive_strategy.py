import importlib
import json
from pathlib import Path
import runpy

import numpy as np
import pandas as pd
import pytest


def api():
    return importlib.import_module("betting.adaptive_strategy")


def field():
    values = [(1, .36, 2), (2, .22, 4), (3, .16, 8),
              (4, .12, 15), (5, .09, 30), (6, .05, 60)]
    return pd.DataFrame([
        {"horse_number": n, "probability": p, "odds": odds, "num_horses": 6,
         "distance": 1600, "surface": "芝", "venue": "東京",
         "field_condition": "良", "race_class": None, "finish": n}
        for n, p, odds in values
    ])


def training():
    m = api()
    rows = []
    for i in range(60):
        source = field()
        source["distance"] += i * 10
        rows.append({
            **m.race_features(source, partners=True), "race_date": f"202401{i % 28 + 1:02d}",
            "upset": i % 3, "axis": i % 2, "axis_win": (i // 2) % 2,
            "f1": int(i % 7 == 0), "wide": int(i % 3 != 0),
            "trio": int(i % 4 == 0), "f2": int(i % 9 == 0),
        })
    return pd.DataFrame(rows)


def test_current_results_cannot_change_forecast_features():
    m = api()
    source = field()
    before = m.race_features(source, partners=True)
    source["finish"] = [6, 5, 4, 3, 2, 1]
    source["time_seconds"] = 9999
    source["return_yen"] = 10000000
    assert m.race_features(source, partners=True) == before
    assert before["b4_probability"] == .09
    assert before["h3_probability"] == .12


def test_partial_declared_field_cannot_be_used_for_inference():
    m = api()
    incomplete = field().iloc[:5].copy()
    incomplete.loc[incomplete.index[0], "probability"] += .05
    with pytest.raises(ValueError):
        m.race_features(incomplete)
    mixed = field().assign(race_id=["race-a", "race-b", "race-a", "race-a", "race-a", "race-a"])
    with pytest.raises(ValueError):
        m.race_features(mixed)


def test_event_labels_use_legal_order_and_reject_unverified_trios():
    m = api()
    source = field()
    source["finish"] = [1, 2, 4, 5, 3, 6]
    target = m.race_outcomes(source, {"1-2-5": 1000}, {"1-2": 200, "1-5": 300, "2-5": 400})
    assert target["f1"] == 1 and target["axis_win"] == 1
    assert target["wide"] == 0 and target["trio"] == 0 and target["f2"] == 0
    with pytest.raises(ValueError):
        m.race_outcomes(source, {"1-2-3": 1000}, {"1-2": 200})


def test_dead_heat_preserves_both_legal_trifecta_orders():
    m = api()
    source = field()
    source["finish"] = [1, 2, 4, 5, 2, 6]
    target = m.race_outcomes(source, {"1-2-5": 1000}, {"1-2": 200, "1-5": 300, "2-5": 400})
    assert target["f1"] == 1 and target["axis"] == 1


def test_numeric_gate_checks_best_funded_candidate_not_any_matching_horse():
    m = api()
    source = field()
    source.loc[source.horse_number.eq(2), "odds"] = 14
    assert m.numeric_priority(source)
    source.loc[source.horse_number.eq(6), "odds"] = 300
    assert not m.numeric_priority(source)


def test_numerical_json_roundtrip_predicts_three_classes_and_fails_closed():
    m = api()
    bundle = m.fit_bundle(training(), "20240201")
    restored = json.loads(json.dumps(bundle))
    features = m.race_features(field(), partners=True)
    first = m.predict_bundle(bundle, features, "20240201", m.BASE_MODEL_SHA)
    second = m.predict_bundle(restored, features, "20240201", m.BASE_MODEL_SHA)
    assert first == second
    assert np.isclose(sum(first["upset"]), 1)
    assert all(0 <= first[key] <= 1 for key in ("axis", "axis_win", "f1", "wide", "trio", "f2"))
    with pytest.raises(ValueError):
        m.predict_bundle(bundle, features, bundle["fitted_through"], m.BASE_MODEL_SHA)
    with pytest.raises(ValueError):
        m.predict_bundle(bundle, features, "20240201", "another-model")
    restored["models"]["upset"]["intercept"][0] += 1
    with pytest.raises(ValueError):
        m.predict_bundle(restored, features, "20240201", m.BASE_MODEL_SHA)
    with pytest.raises(ValueError):
        m.fit_bundle(training(), "20240115")


def history():
    return pd.DataFrame([
        {"race_id": str(i), "race_date": day.strftime("%Y%m%d"), "forecast_class": 0,
         "payout_f1": 10000 if i < 3 else 0, "payout_wide": 200,
         "payout_trio": 0, "payout_f2": 0}
        for i, day in enumerate(pd.date_range("20250101", periods=40))
    ])


def test_severity_policy_requires_supported_payoffs_and_strict_past():
    m = api()
    predictions = {"f1": .2, "wide": .8, "trio": .1, "f2": .01}
    policy = m.fit_severity(history(), conservative=False)
    chosen = m.choose_reference(predictions, policy, 0, "20260101")
    assert chosen["action"] == "candidate1"
    with pytest.raises(ValueError):
        m.choose_reference(predictions, policy, 0, policy["fitted_through"])
    poor = history()
    poor.loc[poor.index[1:], "payout_f1"] = 0
    assert m.choose_reference(predictions, m.fit_severity(poor), 0, "20260101")["action"] == "skip"


def test_preview_never_licenses_purchase_and_names_forecast_not_actual_upset():
    m = api()
    bundle = m.fit_bundle(training(), "20240201")
    result = m.preview(field(), bundle, "20240201", m.BASE_MODEL_SHA)
    assert set(result["upset_probabilities"]) == {"calm", "medium", "large"}
    assert result["upset_label"] in {"順当", "中荒れ", "大荒れ"}
    assert result["reference_only"] is True and result["purchase_eligible"] is False
    assert result["action"] == "skip"
    assert "policy_missing" in result["skip_reasons"]
    with pytest.raises(ValueError):
        m.preview(field().assign(race_date="20240202"), bundle, "20240201", m.BASE_MODEL_SHA)


def replay_api():
    return runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "reproduce_adaptive_betting.py"))


def test_risk_summary_counts_profitable_not_merely_winning_tickets():
    describe = replay_api()["risk_summary"]
    frame = pd.DataFrame([
        {"race_id": str(i), "race_date": f"202601{i + 1:02d}", "cost_yen": cost,
         "return_yen": paid, "ticket_count": int(cost > 0), "hit_ticket_count": int(paid > 0)}
        for i, (cost, paid) in enumerate([(100, 0), (100, 50), (0, 0),
                                        (100, 300), (100, 0), (100, 0)])
    ])
    result = describe(frame)
    assert result["profitable_races"] == 1 and result["hit_races"] == 2
    assert result["max_consecutive_negative_betting_dates"] == 2
    assert result["daily_max_drawdown_yen"] == 200
    assert result["negative_months"] == 1


def test_positive_synthetic_history_does_not_override_missing_forward_evidence():
    assess = replay_api()["assess_switch"]
    frame = pd.DataFrame([
        {"race_id": str(i), "race_date": day.strftime("%Y%m%d"), "cost_yen": 100,
         "return_yen": 200, "ticket_count": 1, "hit_ticket_count": 1}
        for i, day in enumerate(pd.date_range("20260101", periods=60))
    ])
    reference = frame.assign(return_yen=150)
    result = assess(frame, reference)
    assert all(result["historical_criteria"].values())
    assert result["live_effectiveness_confirmed"] is False
    skipped = frame.assign(cost_yen=0, return_yen=0, ticket_count=0, hit_ticket_count=0)
    assert not all(assess(skipped, reference)["historical_criteria"].values())
    with pytest.raises(ValueError):
        assess(frame, reference.iloc[:-1])


def test_replay_does_not_overwrite_existing_output(tmp_path):
    replay = replay_api()["replay"]
    with pytest.raises(FileExistsError):
        replay(tmp_path / "missing", tmp_path)

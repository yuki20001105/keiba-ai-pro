"""Only temporary ledgers / frozen fixtures; no live acquisition or purchases."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from betting.performance import list_performance
from betting.selection_diagnostics import summarize_selection_diagnostics
from betting.value_policy import POLICY_VERSION
from test_betting_performance import NOW, save, snapshot, ticket, result_db


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def observation(*, key="race1", day="2026-10-10", probability=.4, odds=4., hit=True,
                model="model-v1", policy=POLICY_VERSION, kind="単勝", mode="paper", owner="alice"):
    numbers = {"単勝": [1], "ワイド": [1, 2], "三連複": [1, 2, 3]}[kind]
    frozen = ticket(kind=kind, numbers=numbers, stake=100)
    frozen.update(probability=probability, odds=odds)
    payload = snapshot([frozen], rid=key, day=day.replace("-", ""))
    payload["model_id"] = model
    rec = payload["recommendation"]
    selected_at = datetime.fromisoformat(day + "T05:00:00+00:00")
    rec.update(policy_version=policy, evaluated_at=selected_at.isoformat(),
               valid_until=(selected_at + timedelta(minutes=5)).isoformat())
    returned = 340 if hit else 0
    outcome = {"actual_return": returned, "is_hit": hit, "source_sha256": "a" * 64,
               "tickets": [{**frozen, "actual_return": returned, "is_hit": hit, "status": "settled"}]}
    row = {"id": key + "-" + kind, "owner": owner, "snapshot_id": key + "-snapshot-" + kind,
           "race_id": key, "race_date": day, "mode": mode, "payload_json": encoded(payload),
           "tickets_json": encoded([frozen]), "result_json": encoded(outcome),
           "status": "settled", "created_at": selected_at.isoformat(), "settled_at": None}
    saved = {"id": row["snapshot_id"], "owner": owner, "payload_json": encoded(payload),
             "payload_sha256": hashlib.sha256(encoded(payload).encode("utf-8")).hexdigest()}
    return row, saved


def diagnostics(pairs, **kwargs):
    return summarize_selection_diagnostics(
        [pair[0] for pair in pairs], {pair[1]["id"]: pair[1] for pair in pairs},
        mode=kwargs.pop("mode", "paper"), repetitions=200, **kwargs,
    )


def rewrite(pair, *, payload=None, tickets=None, outcome=None, rebind=True):
    row, saved = pair
    if payload is not None:
        row["payload_json"] = encoded(payload)
        if rebind:
            saved["payload_json"] = encoded(payload)
            saved["payload_sha256"] = hashlib.sha256(encoded(payload).encode("utf-8")).hexdigest()
    if tickets is not None:
        row["tickets_json"] = encoded(tickets)
    if outcome is not None:
        row["result_json"] = encoded(outcome)


def test_empty_observation_is_not_zero_probability_roi_or_unknown_holdout():
    result = diagnostics([])
    assert result["groups"] == []
    assert result["sample"] == {"eligible_tickets": 0, "eligible_races": 0, "eligible_dates": 0,
                                "excluded_records": 0, "excluded_tickets": 0}
    assert result["observational"] is True and result["unknown_holdout"] is False
    assert result["purchase_authority"] is False
    assert result["bootstrap"]["simultaneous_intervals"] is False
    assert "roi" not in result
    json.dumps(result, allow_nan=False)


def test_entire_date_paired_bootstrap_not_independent_tickets_or_races():
    pairs = [observation(key=f"day1-{i}", probability=p, hit=hit) for i, (p, hit) in enumerate(
        ((.1, False), (.2, True), (.3, True)))]
    pairs += [observation(key="day2", day="2026-10-11", probability=.9, hit=False)]
    result = diagnostics(pairs)
    group = result["groups"][0]
    assert group["ticket_count"] == group["race_count"] == 4 and group["date_count"] == 2
    assert group["mean_probability"] == pytest.approx(.375)
    assert group["hit_rate"] == .5 and group["bias_predicted_minus_actual"] == pytest.approx(-.125)
    assert group["mean_probability_ci95"] == pytest.approx([.2, .9])
    assert group["hit_rate_ci95"] == pytest.approx([0., 2 / 3])
    assert group["bias_ci95"] == pytest.approx([.2 - 2 / 3, .9])
    assert group["bootstrap_valid_samples"] == 200
    assert diagnostics(deepcopy(pairs)) == result
    assert result["bootstrap"]["method"] == "paired_whole_race_date_percentile"


def test_single_date_has_points_but_no_spurious_precision_or_purchase_authority():
    result = diagnostics([observation()])["groups"][0]
    assert result["mean_probability"] == .4 and result["hit_rate"] == 1.
    assert result["bias_predicted_minus_actual"] == pytest.approx(-.6)
    assert result["bias_ci95"] is None and result["bootstrap_valid_samples"] == 0
    assert result["interval_status"] == "insufficient_distinct_dates"


def test_model_policy_and_bet_type_are_never_mixed():
    result = diagnostics([observation(key="first"), observation(key="new-model", model="model-v2"),
        observation(key="new-policy", policy="future-policy-v3"), observation(key="wide", kind="ワイド")])
    assert len(result["groups"]) == 4
    assert len({(g["model_id"], g["policy_version"], g["bet_type"]) for g in result["groups"]}) == 4
    assert all(g["ticket_count"] == 1 for g in result["groups"])


def test_odds_bands_are_fixed_left_inclusive_and_empty_cells_remain_null():
    values = [1., 2.99, 3., 5., 10., 20., 30., 100.]
    result = diagnostics([observation(key=f"r{i}", odds=odds) for i, odds in enumerate(values)])
    bands = result["groups"][0]["odds_bands"]
    assert [b["ticket_count"] for b in bands] == [2, 1, 1, 1, 1, 2]
    assert bands[-1]["upper"] is None
    empty = diagnostics([observation()])["groups"][0]["odds_bands"][0]
    assert empty["ticket_count"] == 0
    for name in ("mean_probability", "hit_rate", "bias_predicted_minus_actual", "bias_ci95"):
        assert empty[name] is None


@pytest.mark.parametrize("status", ["pending", "payout_pending", "review", "skipped", "unknown"])
def test_unsettled_or_unavailable_outcomes_are_not_losses(status):
    pair = observation()
    pair[0]["status"] = status
    pair[0]["result_json"] = None
    result = diagnostics([pair])
    assert result["groups"] == [] and result["sample"]["eligible_tickets"] == 0
    assert result["sample"]["excluded_tickets"] == 1
    assert result["status_counts"][status if status != "unknown" else "other"] == 1


@pytest.mark.parametrize("change,reason", [
    ("reference", "reference_only"), ("uncalibrated", "probability_unverified"),
    ("input_flag", "input_unverified"), ("input_contract", "input_unverified"),
    ("no_post_time", "timing_unverified"), ("same_post_time", "timing_unverified"),
    ("after_post", "timing_unverified"), ("after_expiry", "timing_unverified"),
    ("future_prediction", "timing_unverified"), ("date_mismatch", "timing_unverified"),
    ("model_unknown", "group_identity_missing"), ("policy_unknown", "group_identity_missing"),
    ("snapshot_owner", "snapshot_unverified"), ("snapshot_hash", "snapshot_unverified"),
    ("snapshot_payload", "snapshot_unverified"),
])
def test_frozen_evidence_and_pre_post_timing_fail_closed(change, reason):
    pair = observation()
    payload = json.loads(pair[0]["payload_json"])
    rec = payload["recommendation"]
    if change == "reference": rec["decision"] = "skip"
    elif change == "uncalibrated": rec["probability_basis"] = "uncalibrated_score"
    elif change == "input_flag": rec["input_verified"] = False
    elif change == "input_contract": payload["input_diagnostics"]["semantic_parity_verified"] = False
    elif change == "no_post_time": payload["race_info"].pop("post_time")
    elif change in {"same_post_time", "after_post"}: pair[0]["created_at"] = "2026-10-10T06:00:00+00:00"
    elif change == "after_expiry": pair[0]["created_at"] = "2026-10-10T05:05:00+00:00"
    elif change == "future_prediction": rec["evaluated_at"] = "2026-10-10T05:01:00+00:00"
    elif change == "date_mismatch": payload["race_info"]["date"] = "20261011"
    elif change == "model_unknown": payload.pop("model_id")
    elif change == "policy_unknown": rec["policy_version"] = ""
    rewrite(pair, payload=payload)
    if change == "snapshot_owner": pair[1]["owner"] = "bob"
    elif change == "snapshot_hash": pair[1]["payload_sha256"] = "0" * 64
    elif change == "snapshot_payload":
        payload["model_id"] = "tampered"
        rewrite(pair, payload=payload, rebind=False)
    result = diagnostics([pair])
    assert result["groups"] == [] and result["excluded_counts"][reason] == 1


@pytest.mark.parametrize("field,value,reason", [
    ("probability", None, "invalid_probability"), ("probability", ".4", "invalid_probability"),
    ("probability", True, "invalid_probability"), ("probability", -.1, "invalid_probability"),
    ("probability", 1.1, "invalid_probability"), ("odds", None, "invalid_odds"),
    ("odds", "4", "invalid_odds"), ("odds", True, "invalid_odds"), ("odds", .9, "invalid_odds"),
])
def test_missing_invalid_probability_or_odds_never_imputed(field, value, reason):
    pair = observation()
    payload = json.loads(pair[0]["payload_json"])
    frozen = payload["recommendation"]["tickets"][0]
    frozen[field] = value
    rewrite(pair, payload=payload, tickets=[frozen])
    result = diagnostics([pair])
    assert result["groups"] == [] and result["excluded_ticket_counts"][reason] == 1


def test_outcome_probability_and_current_predictions_cannot_replace_frozen_selection():
    pair = observation(probability=.4, odds=4.)
    payload = json.loads(pair[0]["payload_json"])
    payload["predictions"][0].update(p_norm=.99, odds=123.)
    outcome = json.loads(pair[0]["result_json"])
    outcome["tickets"][0].update(probability=.99, odds=123.)
    rewrite(pair, payload=payload, outcome=outcome)
    group = diagnostics([pair])["groups"][0]
    assert group["mean_probability"] == .4
    assert group["odds_bands"][1]["ticket_count"] == 1
    assert group["odds_bands"][-1]["ticket_count"] == 0


@pytest.mark.parametrize("change", ["missing_return", "missing_hit", "nonbool_hit", "contradictory_hit", "wrong_stake",
                                   "missing_tickets", "wrong_key", "duplicate_key"])
def test_partial_or_inconsistent_settlement_is_not_a_false_loss(change):
    pair = observation()
    outcome = json.loads(pair[0]["result_json"])
    actual = outcome["tickets"][0]
    if change == "missing_return": actual.pop("actual_return")
    elif change == "missing_hit": actual.pop("is_hit")
    elif change == "nonbool_hit": actual["is_hit"] = 1
    elif change == "contradictory_hit": actual["is_hit"] = False
    elif change == "wrong_stake": actual["stake"] = 200
    elif change == "missing_tickets": outcome.pop("tickets")
    elif change == "wrong_key": actual.update(horse_numbers=[2], combination="2")
    else: outcome["tickets"].append(deepcopy(actual))
    rewrite(pair, outcome=outcome)
    assert diagnostics([pair])["sample"]["eligible_tickets"] == 0


def test_legitimate_recorded_zero_return_is_a_loss_not_missing_payout():
    group = diagnostics([observation(hit=False)])["groups"][0]
    assert group["hit_rate"] == 0 and group["mean_probability"] == .4
    assert group["bias_predicted_minus_actual"] == .4


def test_malformed_ticket_and_invented_selection_do_not_break_other_rows():
    malformed = observation(key="malformed")
    frozen = json.loads(malformed[0]["tickets_json"])
    frozen[0]["bet_type"] = []
    rewrite(malformed, tickets=frozen)
    invented = observation(key="invented")
    frozen = json.loads(invented[0]["tickets_json"])
    frozen[0]["probability"] = .99
    rewrite(invented, tickets=frozen)
    result = diagnostics([malformed, invented, observation(key="real")])
    assert result["sample"]["eligible_tickets"] == 1
    assert result["excluded_counts"]["invalid_ticket"] == 1
    assert result["excluded_ticket_counts"]["frozen_ticket_mismatch"] == 1


def test_list_performance_full_filtered_set_owner_mode_and_dates_not_page(tmp_path):
    ledger, source = tmp_path / "ledger.db", tmp_path / "results.db"
    save(ledger)
    save(ledger, snapshot(rid="second-race"))
    save(ledger, owner="bob")
    save(ledger, mode="actual")
    result_db(source)
    result_db(source, rid="second-race")
    result = list_performance(ledger, source, "alice", "paper", limit=1,
                              date_from="20261010", date_to="20261010")
    assert len(result["records"]) == 1 and result["total_count"] == 2
    diagnostic = result["selection_diagnostics"]
    assert diagnostic["scope"] == {"mode": "paper", "date_from": "2026-10-10", "date_to": "2026-10-10",
                                  "record_count": 2, "includes_full_filtered_set": True}
    assert diagnostic["sample"]["eligible_tickets"] == 2
    assert diagnostic["groups"][0]["ticket_count"] == 2
    assert list_performance(ledger, source, "alice", "actual")["selection_diagnostics"]["sample"]["eligible_tickets"] == 1
    assert list_performance(ledger, source, "bob", "paper")["selection_diagnostics"]["sample"]["eligible_tickets"] == 1
    assert list_performance(ledger, source, "alice", "paper", date_to="20261009")["selection_diagnostics"]["groups"] == []


def test_existing_summary_remains_compatible_and_statuses_are_separate(tmp_path):
    ledger, source = tmp_path / "ledger.db", tmp_path / "results.db"
    save(ledger)
    save(ledger, snapshot(rid="pending"))
    save(ledger, snapshot(rid="payout"))
    save(ledger, snapshot(rid="review"))
    save(ledger, snapshot(rid="skip", skip=True))
    result_db(source)
    result_db(source, rid="payout", dividends=[])
    result_db(source, rid="review", finishes=(1, 2, 2, 4))
    result = list_performance(ledger, source, "alice", "paper")
    assert result["summary"]["pending_races"] == 3
    assert result["selection_diagnostics"]["status_counts"] == {
        "settled": 1, "pending": 1, "payout_pending": 1, "review": 1, "skipped": 1, "other": 0}
    assert result["selection_diagnostics"]["sample"]["eligible_tickets"] == 1


def test_full_105_record_denominator_not_100_default_page(tmp_path):
    ledger, source = tmp_path / "ledger.db", tmp_path / "absent-results.db"
    save(ledger)
    with sqlite3.connect(ledger) as db:
        first = db.execute("SELECT * FROM betting_performance_records").fetchone()
        columns = [row[1] for row in db.execute("PRAGMA table_info(betting_performance_records)")]
        original = dict(zip(columns, first))
        for index in range(105):
            pair = observation(key=f"r{index}")
            row, saved = pair
            db.execute("INSERT INTO betting_performance_snapshots VALUES (?,?,?,?,?)",
                       (saved["id"], saved["owner"], saved["payload_json"], saved["payload_sha256"], row["created_at"]))
            values = {**original, **row, "snapshot_id": saved["id"]}
            db.execute("INSERT INTO betting_performance_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       [values[column] for column in columns])
    result = list_performance(ledger, source, "alice", "paper")
    assert len(result["records"]) == 100 and result["total_count"] == 106
    assert result["selection_diagnostics"]["sample"]["eligible_tickets"] == 105
    assert result["selection_diagnostics"]["sample"]["excluded_tickets"] == 1
    assert not source.exists()


def test_bootstrap_diagnostic_never_holds_ledger_write_transaction(tmp_path, monkeypatch):
    import betting.performance as performance
    ledger, source = tmp_path / "ledger.db", tmp_path / "results.db"
    save(ledger)
    result_db(source)
    original = performance.summarize_selection_diagnostics

    def inspect(*args, **kwargs):
        with sqlite3.connect(ledger, timeout=.1) as db:
            db.execute("BEGIN IMMEDIATE")
            db.rollback()
        return original(*args, **kwargs)

    monkeypatch.setattr(performance, "summarize_selection_diagnostics", inspect)
    assert list_performance(ledger, source, "alice", "paper")["selection_diagnostics"]["sample"]["eligible_tickets"] == 1


@pytest.mark.parametrize("change", ["snapshot_json", "snapshot_nonfinite", "payload_json", "tickets_json",
                                   "missing_recommendation", "missing_info", "list_decision", "list_kind"])
def test_corrupted_internal_json_is_excluded_without_breaking_other_diagnostics(change):
    pair = observation(key="damaged")
    if change == "snapshot_json": pair[1]["payload_json"] = "{broken"
    elif change == "snapshot_nonfinite": pair[1]["payload_json"] = '{"probability":NaN}'
    elif change == "payload_json": pair[0]["payload_json"] = "{broken"
    elif change == "tickets_json": pair[0]["tickets_json"] = "null"
    else:
        payload = json.loads(pair[0]["payload_json"])
        if change == "missing_recommendation": payload.pop("recommendation")
        elif change == "missing_info": payload.pop("race_info")
        elif change == "list_decision": payload["recommendation"]["decision"] = []
        else: payload["recommendation"]["tickets"][0]["bet_type"] = []
        rewrite(pair, payload=payload)
    result = diagnostics([pair, observation(key="intact")])
    assert result["sample"]["eligible_tickets"] == 1 and result["sample"]["excluded_records"] == 1
    json.dumps(result, allow_nan=False)


def test_partially_eligible_record_preserves_ticket_level_exclusion_count():
    pair = observation()
    payload, frozen, outcome = (json.loads(pair[0][field]) for field in ("payload_json", "tickets_json", "result_json"))
    second = {**frozen[0], "horse_numbers": [2], "combination": "2", "probability": None}
    payload["recommendation"]["tickets"].append(second)
    frozen.append(second)
    outcome["tickets"].append({**second, "actual_return": 0, "is_hit": False, "status": "settled"})
    rewrite(pair, payload=payload, tickets=frozen, outcome=outcome)
    result = diagnostics([pair])
    assert result["sample"]["eligible_tickets"] == 1
    assert result["sample"]["excluded_tickets"] == 1
    assert result["sample"]["excluded_records"] == 0
    assert result["excluded_ticket_counts"]["invalid_probability"] == 1
    assert result["groups"][0]["mean_probability"] == .4


def test_actual_promotion_keeps_paper_values_but_uses_its_own_selection_time(tmp_path):
    ledger, source = tmp_path / "ledger.db", tmp_path / "results.db"
    original = snapshot()
    paper = save(ledger, original)
    newer = deepcopy(original)
    newer["recommendation"].update(evaluated_at=(NOW + timedelta(minutes=6)).isoformat(),
                                  valid_until=(NOW + timedelta(minutes=11)).isoformat())
    newer["recommendation"]["tickets"][0].update(probability=.9, odds=9.)
    actual = save(ledger, newer, mode="actual", now=NOW + timedelta(minutes=6))
    assert actual["tickets"] == paper["tickets"]
    result_db(source)
    p = list_performance(ledger, source, "alice", "paper")["selection_diagnostics"]
    a = list_performance(ledger, source, "alice", "actual")["selection_diagnostics"]
    assert p["groups"][0]["mean_probability"] == .4
    assert a["groups"] == [] and a["excluded_counts"]["timing_unverified"] == 1
    # Even a previously stored legacy after-post confirmation cannot become a
    # pre-race calibration observation merely by copying the paper probability.
    with sqlite3.connect(ledger) as db:
        db.execute("UPDATE betting_performance_records SET created_at=? WHERE mode='actual'",
                   ("2026-10-10T06:01:00+00:00",))
    a = list_performance(ledger, source, "alice", "actual")["selection_diagnostics"]
    assert a["groups"] == [] and a["excluded_counts"]["timing_unverified"] == 1

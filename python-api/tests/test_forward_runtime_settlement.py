"""Synthetic paper decisions; no execution, real odds or actual payout claims."""
from copy import deepcopy

import pytest

from forward_runtime.schedule import ForwardError, sha256
from forward_runtime.settlement import (aggregate_forward, settle_win_decision,
                                        validate_capture_and_decision)
from test_forward_runtime_ledger import capture, contract, decision, official
from test_forward_runtime_schedule import race

NOW = "2026-09-20T05:54:40Z"
FINAL = "2026-09-20T06:11:00Z"


def record(*, selected=1):
    snap = capture()
    d = decision()
    if selected == 2:
        snap["snapshot"]["runners"][1]["odds"] = 6.
        snap["snapshot_sha256"] = sha256(snap["snapshot"])
        d.update(horse_number=2, win_probability=.4, indicative_odds=6.)
    return {**race(), "race_date": "20260920", "status": "captured", "snapshot": snap,
            "snapshot_sha256": snap["snapshot_sha256"], "decision": d, "decision_sha256": sha256(d),
            "eligible": 1, "reason": None, "settlement": None, "settlement_sha256": None}


def settled_record(*, selected=1, evidence=None):
    row = record(selected=selected)
    row["settlement"] = settle_win_decision(row, evidence or official(), now=FINAL)
    row["settlement_sha256"] = sha256(row["settlement"])
    row["status"] = row["settlement"]["status"]
    return row


def summarize(rows, days=None, as_of=FINAL):
    return aggregate_forward(days or [{"date": "20260920", "status": "complete"}], rows, as_of=as_of, contract=contract())


def test_valid_capture_and_fixed_policy():
    _, eligible = validate_capture_and_decision(race(), capture(), decision(), contract(), now=NOW)
    assert eligible


@pytest.mark.parametrize("change", [
    {"source_at": "2026-09-20T05:52:59Z"}, {"received_at": "2026-09-20T05:54:30Z"},
    {"features_generated_at": "2026-09-20T05:54:31Z"}, {"prediction_at": "2026-09-20T05:55:01Z"},
    {"stored_at": "2026-09-20T05:54:29Z"}, {"prediction_at": None},
    {"source_at": "2026-09-20T05:54:31"}, {"expected_runner_count": 3},
    {"protocol_sha256": "1" * 64}, {"artifact_hashes": {"model": "2" * 64}},
    {"post_time": "2026-09-20T06:00:59Z"}, {"capture_kind": "historical_db"},
    {"paper_only": False}, {"automatic_purchase": True},
    {"inference_verified": False}, {"raw_features_recomputed": False},
])
def test_malformed_stale_unverified_capture_cannot_bet(change):
    envelope = capture(); envelope["snapshot"].update(change)
    envelope["snapshot_sha256"] = sha256(envelope["snapshot"])
    with pytest.raises(ForwardError):
        validate_capture_and_decision(race(), envelope, decision(), contract(), now=NOW)


@pytest.mark.parametrize("change", [
    {"stake_yen": 200}, {"horse_number": True}, {"horse_number": 2},
    {"automatic_purchase": True}, {"win_probability": .9}, {"indicative_odds": 4.},
    {"odds_multiplier": 1.},
])
def test_ticket_cannot_change_frozen_policy(change):
    with pytest.raises(ForwardError):
        validate_capture_and_decision(race(), capture(), {**decision(), **change}, contract(), now=NOW)


def test_snapshot_hash_and_after_cutoff_commit_rejected():
    c = capture(); c["snapshot"]["runners"][0]["odds"] = 4.
    with pytest.raises(ForwardError, match="hash-mismatch"):
        validate_capture_and_decision(race(), c, decision(), contract(), now=NOW)
    with pytest.raises(ForwardError, match="cutoff"):
        validate_capture_and_decision(race(), capture(), decision(), contract(), now="2026-09-20T05:55:01Z")


def test_skip_only_when_no_eligible_ticket_or_unverified():
    skip = {"decision": "skip", "reason": "edge below fixed threshold"}
    assert validate_capture_and_decision(race(), capture(odds=(1.5, 2.)), skip, contract(), now=NOW)[1]
    assert not validate_capture_and_decision(race(), capture(ready=False), skip, contract(), now=NOW)[1]
    with pytest.raises(ForwardError, match="eligible-skip"):
        validate_capture_and_decision(race(), capture(), skip, contract(), now=NOW)


def test_complete_official_winner_and_loser_not_indicative_odds():
    winner = settled_record()
    assert winner["settlement"]["payout_yen"] == 270  # NOT 3.0 * 100
    loser = settled_record(selected=2)
    assert loser["settlement"]["payout_yen"] == 0


def test_deadheat_uses_each_winner_dividend():
    e = official(winners=[1, 2], win_payouts={"1": 170, "2": 320})
    assert settled_record(evidence=e)["settlement"]["payout_yen"] == 170
    assert settled_record(selected=2, evidence=e)["settlement"]["payout_yen"] == 320


@pytest.mark.parametrize("change", [
    {"status": "pending"}, {"complete": False}, {"runner_numbers": [1]},
    {"win_payouts": {}}, {"winners": [1, 2], "win_payouts": {"1": 170}},
    {"winners": []},
])
def test_unknown_or_partial_payout_never_zero_even_if_apparent_loser(change):
    row = settled_record(selected=2, evidence=official(**change))
    assert row["settlement"]["status"] == "payout_pending"
    assert row["settlement"]["payout_yen"] is None


def test_cancelled_refund_requires_complete_explicit_refund_proof():
    e = official(status="cancelled", winners=[], win_payouts={}, refunded_numbers=[1, 2])
    assert settled_record(evidence=e)["settlement"]["payout_yen"] == 100
    e["refunded_numbers"] = [2]
    assert settled_record(evidence=e)["settlement"]["payout_yen"] is None


@pytest.mark.parametrize("change", [
    {"race_id": "202606040602"}, {"observed_at": "2026-09-20T05:59:59Z"},
    {"received_at": "2026-09-20T06:12:00Z"}, {"runner_numbers": [1, 1, 2]},
    {"winners": [1], "refunded_numbers": [1]}, {"win_payouts": {"1": 0}},
    {"win_payouts": {"1": 271}}, {"source_sha256": "bad"},
])
def test_bad_official_attribution_timestamps_units_rejected(change):
    with pytest.raises(ForwardError):
        settled_record(evidence=official(**change))


def test_scheduled_denominator_retains_failed_missed_skipped_unknown():
    r = record()
    rows = [r]
    for n, status in enumerate(("failed", "missed", "skipped", "unknown", "queued"), 2):
        rows.append({**race(n), "race_date": "20260920", "status": status, "reason": status,
                     "snapshot": None, "decision": None, "eligible": 0})
    a = summarize(rows)
    assert a["scheduled_races_known"] == 6 and a["captured_races"] == 1
    assert a["known_schedule_capture_rate"] == 1 / 6 and a["full_calendar_capture_rate"] == 1 / 6
    assert a["pending_tickets"] == 1 and a["gross_return_percent"] is None
    assert a["maximum_end_of_day_drawdown_yen"] is None
    assert a["daily_curve"][0]["cumulative_net_yen"] is None


def test_incomplete_calendar_full_coverage_rate_unknown_but_known_subset_explicit():
    a = summarize([settled_record()], [{"date": "20260920", "status": "partial"}])
    assert a["full_calendar_capture_rate"] is None
    assert a["known_schedule_capture_rate"] == 1.
    assert a["unknown_day_count"] == 1
    assert a["gross_return_percent"] == 270. and a["net_roi_percent"] == 170.


def test_untouched_future_is_not_an_observation():
    a = summarize([settled_record()], as_of="2026-09-19T12:00:00Z")
    assert a["scheduled_races_known"] == 0 and a["elapsed_calendar_days"] == 0
    assert a["full_calendar_capture_rate"] is None


def test_unknown_ticket_does_not_silently_become_known_subset_roi():
    known = settled_record(selected=2)
    pending = record(); pending["race_id"] = race(2)["race_id"]
    # Identifiers stay bound when represented as a separate synthetic race.
    pending["snapshot"]["snapshot"]["race_id"] = pending["race_id"]
    pending["snapshot"]["snapshot_sha256"] = sha256(pending["snapshot"]["snapshot"])
    pending["snapshot_sha256"] = pending["snapshot"]["snapshot_sha256"]
    a = summarize([known, pending])
    assert a["stake_yen"] == 200 and a["pending_tickets"] == 1
    assert a["payout_yen"] is None and a["net_yen"] is None
    assert a["known_subset"]["tickets"] == 1 and a["known_subset"]["payout_yen"] == 0


def test_complete_loser_drawdown_is_100_yen_not_zero():
    a = summarize([settled_record(selected=2)])
    assert a["maximum_end_of_day_drawdown_yen"] == 100
    assert a["gross_return_percent"] == 0 and a["net_roi_percent"] == -100
    assert a["complete_races"] == 1 and a["complete_race_days"] == 1


def test_missing_elapsed_day_is_unknown_not_silently_omitted():
    a = summarize([settled_record()], as_of="2026-09-21T12:00:00Z")
    assert a["elapsed_calendar_days"] == 2 and a["unknown_day_count"] == 1
    assert a["full_calendar_capture_rate"] is None


def test_duplicate_denominator_rejected():
    row = settled_record()
    with pytest.raises(ForwardError, match="duplicate-race"):
        summarize([row, row])


def test_current_projection_cannot_impersonate_historical_asof_results():
    with pytest.raises(ForwardError, match="historical-as-of"):
        summarize([settled_record()], as_of="2026-09-20T06:01:00Z")


@pytest.mark.parametrize("kind", ["snapshot", "decision", "settlement", "retrospective"])
def test_tampering_and_retrospective_mix_rejected(kind):
    row = settled_record()
    if kind == "snapshot": row["snapshot"]["snapshot"]["runners"][0]["odds"] = 100.
    if kind == "decision": row["decision"]["stake_yen"] = 200
    if kind == "settlement": row["settlement"]["payout_yen"] = 9999
    if kind == "retrospective": row["settlement"]["mode"] = "retrospective_market_proxy"
    with pytest.raises(ForwardError): summarize([row])

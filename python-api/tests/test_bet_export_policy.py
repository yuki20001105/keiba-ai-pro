"""Selected-ticket export never manufactures stakes or revives skipped races."""
from __future__ import annotations

import csv
import io
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from routers.bet_export import _build_bet_rows, router
from betting.value_policy import POLICY_VERSION


NOW = datetime(2026, 10, 10, 5, tzinfo=timezone.utc)


def request(path, payload):
    async def send():
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.post(path, json=payload)
    return asyncio.run(send())


def result(*, tickets=None, now=NOW):
    return {
        "success": True,
        "input_diagnostics": {"schema": "prediction-input-contract-v1", "status": "aligned",
            "expected_count": 3, "actual_count": 3, "missing_columns": [], "blocked_columns": [],
            "issue_codes": [], "source_hash_matches": True, "history_available": True,
            "semantic_parity_verified": True},
        "race_info": {"race_id": "202605050111", "race_name": "テスト", "venue": "東京"},
        "predictions": [{"horse_number": 5, "horse_name": "五番"}],
        "race_level": "normal",
        "best_bet_type": "単勝",
        # These high-EV candidates must not independently become orders.
        "bet_types": {"単勝": [{"combination": "9", "probability": .5, "odds": 100, "expected_value": 50}]},
        "recommendation": {
            "policy_version": POLICY_VERSION,
            "input_verified": True,
            "probability_basis": "calibrated",
            "decision": "buy",
            "action": "購入候補",
            "evaluated_at": now.isoformat(),
            "valid_until": (now + timedelta(minutes=5)).isoformat(),
            "unit_price": 1000,
            "purchase_count": 9,
            "tickets": tickets if tickets is not None else [
                {"bet_type": "単勝", "combination": "5", "probability": .30,
                 "odds": 5., "expected_value": 1.5, "conservative_expected_value": 1.3, "stake": 300},
            ],
        },
    }


def rows(data, *, min_ev=1.2, min_prob=0., max_bets=5, now=NOW):
    return _build_bet_rows([data], bankroll=10000, min_ev=min_ev,
                           min_prob=min_prob, max_bets_per_race=max_bets, now=now)


@pytest.mark.parametrize("change", ["legacy", "no_policy", "unknown_policy", "old_v1_policy", "skip", "skip_action", "skip_level", "no_tickets", "uncalibrated", "no_decision"])
def test_skipped_or_legacy_response_never_exports_candidates(change):
    data = result()
    recommendation = data["recommendation"]
    if change == "legacy":
        data.pop("recommendation")
    elif change == "no_policy":
        recommendation.pop("policy_version")
    elif change == "unknown_policy":
        recommendation["policy_version"] = "unrecognized-v2"
    elif change == "old_v1_policy":
        recommendation["policy_version"] = "value-selection-v1"
    elif change == "skip":
        recommendation["decision"] = "skip"
    elif change == "skip_action":
        recommendation["action"] = "見送り"
    elif change == "skip_level":
        data["race_level"] = "skip"
    elif change == "uncalibrated":
        recommendation["probability_basis"] = "score_only"
    elif change == "no_decision":
        recommendation.pop("decision")
    else:
        recommendation.pop("tickets")
    assert rows(data) == []


def test_export_preserves_individual_selected_stakes_not_candidate_order_or_global_unit():
    data = result(tickets=[
        {"bet_type": "単勝", "combination": "5", "probability": .30,
         "odds": 5., "expected_value": 1.5, "conservative_expected_value": 1.3, "stake": 300},
        {"bet_type": "単勝", "combination": "2", "probability": .20,
         "odds": 7., "expected_value": 1.4, "conservative_expected_value": 1.25, "stake": 100},
    ])
    exported = rows(data)
    assert [r["combination"] for r in exported] == ["5", "2"]
    assert [r["total_cost"] for r in exported] == [300, 100]
    assert [r["units"] for r in exported] == [3, 1]
    assert all(r["unit_price"] == 100 for r in exported)
    assert exported[0]["horse_names"] == ["五番"]
    assert len(rows(data, max_bets=1)) == 1
    assert rows(data, max_bets=0) == []


@pytest.mark.parametrize("stake", [None, True, 0, 50, 150, 199.9, -100, float("nan"), float("inf")])
def test_invalid_stake_never_defaults_to_a_purchase(stake):
    data = result()
    data["recommendation"]["tickets"][0]["stake"] = stake
    assert rows(data) == []


@pytest.mark.parametrize("field,value", [
    ("probability", float("nan")), ("probability", 1.1), ("probability", 0),
    ("odds", None), ("odds", 1), ("expected_value", float("inf")),
    ("conservative_expected_value", float("nan")), ("conservative_expected_value", 1.6),
])
def test_invalid_probability_odds_or_ev_is_not_exported(field, value):
    data = result()
    data["recommendation"]["tickets"][0][field] = value
    assert rows(data) == []


def test_ev_and_probability_filters_use_full_precision_before_rounding():
    data = result()
    data["recommendation"]["tickets"][0]["conservative_expected_value"] = 1.1999
    assert rows(data, min_ev=1.2) == []
    assert len(rows(data, min_ev=1.1999)) == 1
    assert rows(data, min_ev=1.0, min_prob=.30001) == []


def test_priority_is_exportable_but_conservative_ev_cannot_be_relaxed_below_break_even():
    data = result()
    data["recommendation"]["decision"] = "priority"
    assert len(rows(data)) == 1
    data["recommendation"]["tickets"][0]["conservative_expected_value"] = .99
    assert rows(data, min_ev=.5) == []


def test_json_and_csv_keep_existing_schema_and_use_selected_ticket_stake():
    payload = {"results": [result(now=datetime.now(timezone.utc))], "bankroll": 10000}

    response = request("/api/export/bet-list", payload)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["summary"] == {"total_cost": 300, "races": 1, "bets": 1, "expected_return": 450.}
    assert body["bets"][0]["bet_type_code"] == "tan"
    assert body["bets"][0]["units"] == 3

    csv_response = request("/api/export/bet-list/csv", payload)
    assert csv_response.status_code == 200
    assert "attachment; filename=" in csv_response.headers["content-disposition"]
    csv_rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert len(csv_rows) == 1
    assert csv_rows[0]["combination"] == "5"
    assert csv_rows[0]["total_cost"] == "300"
    assert csv_rows[0]["unit_price"] == "100"
    assert csv_rows[0]["horse_names"] == "五番"


def test_json_and_csv_return_no_purchase_for_skip_even_with_high_ev_candidates():
    data = result(now=datetime.now(timezone.utc))
    data["recommendation"]["decision"] = "skip"
    payload = {"results": [data]}
    body = request("/api/export/bet-list", payload).json()
    assert body["bets"] == []
    assert body["summary"]["total_cost"] == 0
    csv_response = request("/api/export/bet-list/csv", payload)
    assert list(csv.DictReader(io.StringIO(csv_response.text))) == []


@pytest.mark.parametrize("change", ["missing_evaluated", "missing_expiry", "naive_evaluated", "naive_expiry", "reversed", "future", "expired", "five_minute_limit"])
def test_expired_or_unverifiable_decision_never_exports(change):
    data = result()
    rec = data["recommendation"]
    current = NOW
    if change == "missing_evaluated":
        rec.pop("evaluated_at")
    elif change == "missing_expiry":
        rec.pop("valid_until")
    elif change == "naive_evaluated":
        rec["evaluated_at"] = NOW.replace(tzinfo=None).isoformat()
    elif change == "naive_expiry":
        rec["valid_until"] = (NOW + timedelta(minutes=5)).replace(tzinfo=None).isoformat()
    elif change == "reversed":
        rec["valid_until"] = (NOW - timedelta(seconds=1)).isoformat()
    elif change == "future":
        current = NOW - timedelta(seconds=1)
    elif change == "expired":
        rec["valid_until"] = (NOW + timedelta(seconds=30)).isoformat()
        current = NOW + timedelta(seconds=30)
    else:
        rec["valid_until"] = (NOW + timedelta(hours=1)).isoformat()
        current = NOW + timedelta(minutes=5)
    assert rows(data, now=current) == []


def test_aware_offsets_are_compared_as_instants_and_expiry_is_exclusive():
    data = result()
    rec = data["recommendation"]
    rec["evaluated_at"] = "2026-10-10T14:00:00+09:00"
    rec["valid_until"] = "2026-10-10T14:02:00+09:00"
    assert len(rows(data, now=NOW + timedelta(seconds=119))) == 1
    assert rows(data, now=NOW + timedelta(seconds=120)) == []


def test_json_and_csv_cannot_reactivate_an_expired_buy_decision():
    data = result(now=datetime.now(timezone.utc) - timedelta(minutes=6))
    payload = {"results": [data]}
    body = request("/api/export/bet-list", payload).json()
    assert body["bets"] == []
    assert body["summary"]["total_cost"] == 0
    response = request("/api/export/bet-list/csv", payload)
    assert list(csv.DictReader(io.StringIO(response.text))) == []

from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from betting.performance import list_performance, record_selection, save_snapshot
from betting.value_policy import POLICY_VERSION

NOW = datetime(2026, 10, 10, 5, tzinfo=timezone.utc)
RACE_ID = "202605040111"


def ticket(kind="単勝", numbers=None, stake=200):
    numbers = numbers or [1]
    return {"bet_type": kind, "horse_numbers": numbers, "combination": "-".join(map(str, numbers)),
            "stake": stake, "probability": .4, "odds": 4.0, "expected_value": 1.6,
            "conservative_expected_value": 1.368}


def snapshot(tickets=None, *, rid=RACE_ID, day="20261010", skip=False):
    tickets = [ticket()] if tickets is None else tickets
    return {"model_id": "frozen-test-model", "race_info": {
        "race_id": rid, "date": day, "venue": "東京", "race_name": "テスト", "race_no": 11,
        "num_horses": 4, "post_time": "15:00"},
        "predictions": [{"horse_number": number} for number in range(1, 5)],
        "input_diagnostics": {"schema": "prediction-input-contract-v1", "status": "aligned",
            "expected_count": 3, "actual_count": 3, "missing_columns": [], "blocked_columns": [],
            "issue_codes": [], "source_hash_matches": True, "history_available": True,
            "semantic_parity_verified": True},
        "recommendation": {"policy_version": POLICY_VERSION, "probability_basis": "calibrated",
            "input_verified": not skip,
            "decision": "skip" if skip else "buy", "action": "見送り" if skip else "購入候補",
            "reason": "勝率の検証が未完了" if skip else "期待値の条件を満たしました",
            "reason_codes": ["probability_unvalidated"] if skip else [],
            "tickets": [] if skip else tickets, "budget": 10000, "per_race_limit": 10000,
            "evaluated_at": NOW.isoformat(), "valid_until": (NOW + timedelta(minutes=5)).isoformat()}}


def selected(t):
    return {key: t[key] for key in ("bet_type", "horse_numbers", "combination", "stake")}


def save(db, payload=None, owner="alice", mode="paper", chosen=None, now=NOW):
    payload = snapshot() if payload is None else payload
    sid = save_snapshot(db, owner, payload)
    chosen = [selected(t) for t in payload["recommendation"]["tickets"]] if chosen is None else chosen
    return record_selection(db, owner, sid, chosen, mode, now=now)


def result_db(path, *, rid=RACE_ID, finishes=(1, 2, 3, 4), dividends=None, info=None):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS races_ultimate (race_id TEXT PRIMARY KEY,data TEXT);
    CREATE TABLE IF NOT EXISTS race_results_ultimate (race_id TEXT,data TEXT);
    CREATE TABLE IF NOT EXISTS return_tables_ultimate (race_id TEXT,bet_type TEXT,combinations TEXT,payout INTEGER);
    """)
    con.execute("INSERT OR REPLACE INTO races_ultimate VALUES (?,?)", (rid, json.dumps(info or {"date": "20261010", "num_horses": 4})))
    con.execute("DELETE FROM race_results_ultimate WHERE race_id=?", (rid,))
    con.execute("DELETE FROM return_tables_ultimate WHERE race_id=?", (rid,))
    for number, finish in enumerate(finishes, 1):
        con.execute("INSERT INTO race_results_ultimate VALUES (?,?)", (rid, json.dumps({"horse_number": number, "finish_position": finish})))
    for kind, combination, payout in ([('単勝', '1', 340)] if dividends is None else dividends):
        con.execute("INSERT INTO return_tables_ultimate VALUES (?,?,?,?)", (rid, kind, combination, payout))
    con.commit()
    con.close()


def read(db, results, owner="alice", mode="paper", **kwargs):
    return list_performance(db, results, owner, mode, **kwargs)


def test_snapshot_and_race_first_selection_idempotency(tmp_path):
    db = tmp_path / "ledger.db"
    payload = snapshot()
    first_id = save_snapshot(db, "alice", payload)
    assert save_snapshot(db, "alice", deepcopy(payload)) == first_id
    first = record_selection(db, "alice", first_id, [selected(ticket())], now=NOW)
    second = record_selection(db, "alice", first_id, [], now=NOW)
    assert first["id"] == second["id"]
    assert second["existing"] is True and second["total_cost"] == 200
    changed = snapshot([ticket(stake=500)])
    assert save(db, changed)["id"] == first["id"]
    assert read(db, tmp_path / "absent.db")["total_count"] == 1


def test_snapshot_owner_isolation_and_no_legacy_mutation(tmp_path):
    db = tmp_path / "ledger.db"
    sid = save_snapshot(db, "alice", snapshot())
    with pytest.raises(PermissionError):
        record_selection(db, "bob", sid, [selected(ticket())], now=NOW)
    a, b = save(db), save(db, owner="bob")
    assert a["id"] != b["id"]
    assert read(db, tmp_path / "absent.db", "alice")["total_count"] == 1
    assert read(db, tmp_path / "absent.db", "charlie")["total_count"] == 0
    with sqlite3.connect(db) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "purchase_history" not in tables and "prediction_log" not in tables


def test_old_v1_snapshot_cannot_create_new_purchase_record(tmp_path):
    db = tmp_path / "ledger.db"
    old = snapshot()
    old["recommendation"]["policy_version"] = "value-selection-v1"
    with pytest.raises(ValueError):
        save_snapshot(db, "alice", old)
    # Simulate a persisted offer from the old version, without rewriting it.
    sid = save_snapshot(db, "alice", snapshot())
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT payload_json FROM betting_performance_snapshots WHERE id=?", (sid,)).fetchone()
        payload = json.loads(row[0])
        payload["recommendation"]["policy_version"] = "value-selection-v1"
        from betting.performance import _digest
        con.execute("UPDATE betting_performance_snapshots SET payload_json=?,payload_sha256=? WHERE id=?",
            (json.dumps(payload), _digest(payload), sid))
    with pytest.raises(ValueError, match="再判定"):
        record_selection(db, "alice", sid, [selected(ticket())], "actual", now=NOW)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM betting_performance_records").fetchone()[0] == 0


@pytest.mark.parametrize("mutate", [
    lambda p: p.pop("input_diagnostics"),
    lambda p: p["input_diagnostics"].update(semantic_parity_verified=False),
    lambda p: p["input_diagnostics"].update(actual_count=4),
    lambda p: p["recommendation"].pop("input_verified"),
])
def test_unverified_snapshot_cannot_supply_actual_tickets_even_if_hash_is_consistent(tmp_path, mutate):
    from betting.performance import _digest
    db = tmp_path / "ledger.db"
    sid = save_snapshot(db, "alice", snapshot())
    bad = snapshot()
    mutate(bad)
    with pytest.raises(ValueError, match="入力互換性"):
        save_snapshot(db, "alice", bad)
    # An old persisted offer must pass the same check, not just a content hash.
    with sqlite3.connect(db) as con:
        con.execute("UPDATE betting_performance_snapshots SET payload_json=?,payload_sha256=? WHERE id=?",
            (json.dumps(bad), _digest(bad), sid))
    with pytest.raises(ValueError):
        record_selection(db, "alice", sid, [selected(ticket())], "actual", now=NOW)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM betting_performance_records").fetchone()[0] == 0


def test_client_cannot_forge_metadata_or_raise_stakes(tmp_path):
    db = tmp_path / "ledger.db"
    sid = save_snapshot(db, "alice", snapshot())
    invalid = [
        {**selected(ticket()), "probability": .9}, {**selected(ticket()), "odds": 100},
        {**selected(ticket()), "model_id": "other"}, selected(ticket(stake=300)),
        selected(ticket(stake=150)), selected(ticket(numbers=[2])),
        {**selected(ticket()), "stake": True}, {**selected(ticket()), "combination": "01"},
    ]
    for chosen in invalid:
        with pytest.raises(ValueError):
            record_selection(db, "alice", sid, [chosen], now=NOW)
    with pytest.raises(ValueError):
        record_selection(db, "alice", sid, [selected(ticket()), selected(ticket())], now=NOW)


def test_reduced_day_budget_stake_and_zero_pass(tmp_path):
    db = tmp_path / "ledger.db"
    reduced = save(db, snapshot([ticket(stake=500)]), chosen=[selected(ticket(stake=100))])
    assert reduced["total_cost"] == 100
    assert reduced["tickets"][0]["odds"] == 4 and reduced["tickets"][0]["probability"] == .4
    skipped = save(db, snapshot(rid="another-race"), chosen=[])
    assert skipped["status"] == "skipped" and skipped["reason_codes"] == ["day_budget_limit"]
    summary = read(db, tmp_path / "absent.db")["summary"]
    assert summary["pending_cost"] == 100 and summary["skipped_races"] == 1 and summary["roi"] is None


def test_reject_expired_or_uncalibrated_purchase_but_allow_skip(tmp_path):
    db = tmp_path / "ledger.db"
    with pytest.raises(ValueError, match="期限切れ"):
        save(db, now=NOW + timedelta(minutes=5))
    payload = snapshot()
    payload["recommendation"]["probability_basis"] = "uncalibrated_score"
    with pytest.raises(ValueError):
        save(db, payload)
    assert save(db, payload, chosen=[], now=NOW + timedelta(days=1))["status"] == "skipped"


def test_skips_need_no_probability_calibration_or_post_time(tmp_path):
    payload = snapshot(skip=True)
    payload["race_info"].pop("post_time")
    payload["recommendation"]["probability_basis"] = "uncalibrated_score"
    row = save(tmp_path / "ledger.db", payload, now=NOW + timedelta(days=1))
    assert row["status"] == "skipped" and row["reason"] == "勝率の検証が未完了"
    with pytest.raises(ValueError):
        save(tmp_path / "actual.db", payload, mode="actual")


def test_actual_is_separate_and_promotion_reuses_frozen_paper(tmp_path):
    db = tmp_path / "ledger.db"
    original = snapshot()
    paper = save(db, original)
    latest = deepcopy(original)
    latest["recommendation"]["tickets"][0].update(odds=4.2, expected_value=1.68)
    actual = save(db, latest, mode="actual")
    assert actual["id"] != paper["id"] and actual["snapshot_id"] == paper["snapshot_id"]
    assert actual["tickets"] == paper["tickets"]
    assert read(db, tmp_path / "absent.db", mode="actual")["total_count"] == 1
    assert read(db, tmp_path / "absent.db", mode="paper")["total_count"] == 1


def test_promotion_cannot_rewrite_saved_selection(tmp_path):
    db = tmp_path / "ledger.db"
    save(db)
    with pytest.raises(ValueError, match="一致"):
        save(db, snapshot([ticket(numbers=[2])]), mode="actual")
    with pytest.raises(ValueError, match="一致"):
        save(db, snapshot([ticket(stake=100)]), mode="actual")
    with pytest.raises(ValueError, match="期限切れ"):
        save(db, mode="actual", now=NOW + timedelta(hours=1))


@pytest.mark.parametrize("kind,numbers,dividends", [
    ("単勝", [1], [("単勝", "1", 340)]),
    ("馬連", [1, 2], [("馬連", "2-1", 700)]),
    ("馬単", [1, 2], [("馬単", "1→2", 1300)]),
    ("ワイド", [1, 2], [("ワイド", "1-2", 200), ("ワイド", "1-3", 400), ("ワイド", "2-3", 500)]),
    ("三連複", [1, 2, 3], [("三連複", "1-2-3", 1100)]),
    ("三連単", [1, 2, 3], [("三連単", "1-2-3", 3300)]),
])
def test_all_six_types_use_official_dividends_not_prediction_odds(tmp_path, kind, numbers, dividends):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    save(db, snapshot([ticket(kind, numbers, stake=300)]))
    result_db(results, dividends=dividends)
    row = read(db, results)["records"][0]
    assert row["status"] == "settled" and row["actual_return"] == dividends[0][2] * 3
    assert row["tickets"][0]["actual_return"] == dividends[0][2] * 3
    assert row["is_hit"] is True


def test_multiple_tickets_have_individual_stakes_and_returns(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    save(db, snapshot([ticket(stake=300), ticket(numbers=[2], stake=100)]))
    result_db(results)
    row = read(db, results)["records"][0]
    assert row["total_cost"] == 400 and row["actual_return"] == 1020
    assert [t["actual_return"] for t in row["tickets"]] == [1020, 0]
    assert row["profit"] == 620


def test_confirmed_zero_loss_stays_settled_on_reload(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    save(db, snapshot([ticket(numbers=[4])]))
    result_db(results)
    first = read(db, results)
    assert first["records"][0]["status"] == "settled"
    assert first["summary"]["roi"] == 0 and first["summary"]["profit"] == -200
    result_db(results, dividends=[])
    assert read(db, results)["records"][0]["status"] == "settled"
    assert read(db, results)["records"][0]["actual_return"] == 0


@pytest.mark.parametrize("finishes,status", [((1, 2), "pending"), ((1, 2, None, None), "pending"),
    ((1, 2, 2, 4), "review"), ((1, 2, "取消", 3), "review"), ((1, 2, 3, 5), "review")])
def test_incomplete_or_exceptional_result_never_fake_loss(tmp_path, finishes, status):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    save(db, snapshot([ticket(numbers=[4])]))
    result_db(results, finishes=finishes)
    response = read(db, results)
    assert response["records"][0]["status"] == status
    assert response["records"][0]["actual_return"] is None
    assert response["summary"]["settled_races"] == 0 and response["summary"]["pending_cost"] == 200


@pytest.mark.parametrize("dividends,status", [([], "payout_pending"),
    ([("単勝", "1", 340), ("単勝", "1", 350)], "review"),
    ([("単勝", "2", 340)], "review"), ([("単勝", "1", 0)], "review")])
def test_missing_or_conflicting_official_payouts_never_fake_zero(tmp_path, dividends, status):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    save(db, snapshot([ticket(numbers=[4])]))
    result_db(results, dividends=dividends)
    assert read(db, results)["records"][0]["status"] == status


def test_wide_requires_all_three_winners_and_identical_duplicates_collapse(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    save(db, snapshot([ticket("ワイド", [1, 2])]))
    result_db(results, dividends=[("ワイド", "1-2", 70), ("ワイド", "1-2", 70)])
    assert read(db, results)["records"][0]["status"] == "payout_pending"
    result_db(results, dividends=[("ワイド", "1-2", 70), ("ワイド", "1-2", 70),
        ("ワイド", "1-3", 80), ("ワイド", "2-3", 100)])
    row = read(db, results)["records"][0]
    assert row["status"] == "settled" and row["actual_return"] == 140


def test_refund_flag_duplicate_runners_and_changed_numbers_review(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    save(db)
    result_db(results, info={"refund": True})
    assert read(db, results)["records"][0]["status"] == "review"
    result_db(results)
    with sqlite3.connect(results) as con:
        con.execute("INSERT INTO race_results_ultimate VALUES (?,?)", (RACE_ID, json.dumps({"horse_number": 1, "finish_position": 1})))
    assert read(db, results)["records"][0]["status"] == "review"
    result_db(results)
    with sqlite3.connect(results) as con:
        con.execute("UPDATE race_results_ultimate SET data=? WHERE json_extract(data,'$.horse_number')=4", (json.dumps({"horse_number": 5, "finish_position": 4}),))
    assert read(db, results)["records"][0]["status"] == "review"


def test_summary_entire_filtered_set_not_page_and_pending_excluded(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    save(db)
    save(db, snapshot(rid="race-2"))
    save(db, snapshot(rid="race-3", day="20261009", skip=True))
    result_db(results)
    response = read(db, results, limit=1)
    assert len(response["records"]) == 1 and response["total_count"] == 3
    assert response["summary"] == {"settled_cost": 200, "settled_return": 680, "profit": 480,
        "roi": 340.0, "settled_races": 1, "hit_races": 1, "pending_races": 1, "pending_cost": 200, "skipped_races": 1}
    today = read(db, results, date_from="2026-10-10", date_to="2026-10-10", limit=1)
    assert today["total_count"] == 2 and today["summary"]["skipped_races"] == 0
    older = read(db, results, date_to="20261009")
    assert older["total_count"] == 1 and older["summary"]["roi"] is None
    with pytest.raises(ValueError):
        read(db, results, date_from="2026-10-11", date_to="2026-10-10")


def test_missing_result_db_is_read_only_and_settlement_can_resume(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "missing.db"
    save(db)
    assert read(db, results)["records"][0]["status"] == "pending"
    assert not results.exists()
    result_db(results)
    assert read(db, results)["records"][0]["status"] == "settled"


def test_result_date_and_horse_identity_must_match(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    payload = snapshot()
    payload["predictions"][0]["horse_id"] = "original-horse"
    save(db, payload)
    result_db(results, info={"date": "20261011", "num_horses": 4})
    assert read(db, results)["records"][0]["settlement_reason"] == "結果の開催日が一致しません"
    result_db(results)
    assert read(db, results)["records"][0]["settlement_reason"] == "予測時と馬の識別情報が一致しません"
    with sqlite3.connect(results) as con:
        con.execute("UPDATE race_results_ultimate SET data=? WHERE json_extract(data,'$.horse_number')=1",
                    (json.dumps({"horse_number": 1, "finish_position": 1, "horse_id": "original-horse"}),))
    assert read(db, results)["records"][0]["status"] == "settled"


def test_concurrent_submissions_freeze_once(tmp_path):
    db = tmp_path / "ledger.db"
    sid = save_snapshot(db, "alice", snapshot())
    def submit(index):
        return record_selection(db, "alice", sid, [selected(ticket(stake=100 if index % 2 else 200))], now=NOW)
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(submit, range(24)))
    assert len({row["id"] for row in rows}) == 1
    assert len({row["total_cost"] for row in rows}) == 1
    assert sum(not row["existing"] for row in rows) == 1


def test_snapshot_integrity_detects_modified_saved_payload(tmp_path):
    db = tmp_path / "ledger.db"
    sid = save_snapshot(db, "alice", snapshot())
    with sqlite3.connect(db) as con:
        changed = snapshot([ticket(stake=10000)])
        con.execute("UPDATE betting_performance_snapshots SET payload_json=? WHERE id=?", (json.dumps(changed), sid))
    with pytest.raises(ValueError, match="整合性"):
        record_selection(db, "alice", sid, [selected(ticket(stake=10000))], now=NOW)


def test_fractional_source_horse_number_is_not_truncated(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    save(db)
    result_db(results)
    with sqlite3.connect(results) as con:
        con.execute("UPDATE race_results_ultimate SET data=? WHERE json_extract(data,'$.horse_number')=1",
                    (json.dumps({"horse_number": 1.5, "finish_position": 1}),))
    assert read(db, results)["records"][0]["status"] == "review"


def test_expired_empty_offer_freezes_expiry_reason_across_refresh(tmp_path):
    db = tmp_path / "ledger.db"
    row = save(db, chosen=[], now=NOW + timedelta(minutes=6))
    assert row["reason"] == "買い目の期限切れ" and row["reason_codes"] == ["offer_expired"]
    for _ in range(2):
        refreshed = read(db, tmp_path / "absent.db")["records"][0]
        assert refreshed["reason_codes"] == ["offer_expired"]
    day_pass = save(db, snapshot(rid="within-budget-window"), chosen=[])
    assert day_pass["reason_codes"] == ["day_budget_limit"]
    assert read(db, tmp_path / "absent.db")["summary"]["skipped_races"] == 2


@pytest.mark.parametrize("count,kind,numbers,dividends", [
    (2, "馬連", [1, 2], [("馬連", "1-2", 700)]),
    (3, "ワイド", [1, 2], [("ワイド", "1-2", 200), ("ワイド", "1-3", 400), ("ワイド", "2-3", 500)]),
    (3, "三連複", [1, 2, 3], [("三連複", "1-2-3", 700)]),
    (3, "三連単", [1, 2, 3], [("三連単", "1-2-3", 700)]),
])
def test_unavailable_small_field_ticket_requires_review(tmp_path, count, kind, numbers, dividends):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    payload = snapshot([ticket(kind, numbers)])
    payload["race_info"]["num_horses"] = count
    payload["predictions"] = payload["predictions"][:count]
    save(db, payload)
    result_db(results, finishes=tuple(range(1, count + 1)), dividends=dividends)
    assert read(db, results)["records"][0]["status"] == "review"

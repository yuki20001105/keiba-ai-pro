"""Isolated SQLite fixtures; never initializes an app/production database."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3

import pytest

from forward_runtime.ledger import RuntimeLedger
from forward_runtime.schedule import ForwardError, aware, sha256, stamp
from test_forward_runtime_schedule import evidence, race


def contract():
    return {"protocol_sha256": "a" * 64,
            "artifact_hashes": {"model": "b" * 64, "calibrator": "c" * 64, "features": "d" * 64, "rule": "e" * 64}}


@pytest.fixture
def runtime(tmp_path):
    clock = [aware("2026-09-20T05:54:40Z")]
    ledger = RuntimeLedger(tmp_path / "isolated-forward.sqlite", clock=lambda: clock[0])
    ledger.initialize(contract())
    lease = ledger.acquire_owner("fixture", ttl_seconds=300)
    return ledger, lease, clock


def capture(c=None, *, odds=(3., 2.), ready=True):
    c = c or contract()
    snapshot = {"race_id": race()["race_id"], "post_time": stamp(race()["post_time"]),
                "protocol_sha256": c["protocol_sha256"], "artifact_hashes": c["artifact_hashes"],
                "paper_only": True, "automatic_purchase": False, "capture_kind": "direct_fetch",
                "source_at": "2026-09-20T05:54:31Z", "received_at": "2026-09-20T05:54:32Z",
                "features_generated_at": "2026-09-20T05:54:33Z", "prediction_at": "2026-09-20T05:54:34Z",
                "stored_at": "2026-09-20T05:54:35Z", "cutoff_at": "2026-09-20T05:55:00Z",
                "policy_ready": ready, "inference_verified": ready, "raw_features_recomputed": ready,
                "expected_runner_count": 2,
                "runners": [{"horse_number": 1, "win_probability": .6, "odds": odds[0]},
                            {"horse_number": 2, "win_probability": .4, "odds": odds[1]}]}
    return {"snapshot": snapshot, "snapshot_sha256": sha256(snapshot)}


def decision():
    return {"decision": "paper_bet", "horse_number": 1, "stake_yen": 100, "odds_multiplier": .9,
            "win_probability": .6, "indicative_odds": 3.}


def begin(runtime):
    ledger, lease, clock = runtime
    rows = [race()]
    ledger.register_day("20260920", evidence(rows), rows, lease)
    assert ledger.claim_due(lease)["race_id"] == race()["race_id"]
    return ledger, lease, clock


def official(**change):
    return {"race_id": race()["race_id"], "status": "final", "complete": True,
            "runner_numbers": [1, 2], "winners": [1], "win_payouts": {"1": 270}, "refunded_numbers": [],
            "source_url": "https://race.netkeiba.com/fixture-result", "source_sha256": "f" * 64,
            "observed_at": "2026-09-20T06:10:00Z", "received_at": "2026-09-20T06:10:01Z", **change}


def test_readonly_constructor_and_frozen_initialize(tmp_path):
    path = tmp_path / "state.sqlite"
    ledger = RuntimeLedger(path)
    assert not path.exists()
    with pytest.raises(ForwardError): ledger.snapshot()
    c = ledger.initialize(contract())
    assert ledger.initialize(contract()) == c
    assert len(ledger.snapshot()["days"]) == 90
    with pytest.raises(ForwardError, match="already-frozen"):
        ledger.initialize({**contract(), "protocol_sha256": "1" * 64})
    with pytest.raises(ForwardError, match="contract-mismatch"):
        RuntimeLedger(tmp_path / "bad.sqlite").initialize({**contract(), "date_to": "20261219"})


def test_no_production_directory_target():
    from forward_runtime.ledger import REPO
    for directory in ("keiba/data", "python-api/data", "python-api/models"):
        with pytest.raises(ForwardError, match="production"):
            RuntimeLedger(REPO / directory / "fixture.sqlite")


def test_read_reports_use_consistent_query_only_transaction(runtime):
    ledger, _, _ = runtime
    with ledger._db() as db:
        assert db.in_transaction
        assert db.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            db.execute("UPDATE owner SET stop_requested=1")
    assert ledger.snapshot()["owner"]["stop_requested"] == 0


def test_single_owner_heartbeat_fencing_and_crash_no_retry(runtime):
    ledger, lease, clock = begin(runtime)
    with pytest.raises(ForwardError, match="already-owned"):
        ledger.acquire_owner("second")
    clock[0] += timedelta(seconds=301)
    replacement = ledger.acquire_owner("replacement")
    assert replacement.fence == lease.fence + 1
    assert ledger.snapshot()["races"][0]["status"] == "unknown"
    with pytest.raises(ForwardError, match="lease-lost"): ledger.heartbeat(lease)
    assert ledger.claim_due(replacement) is None
    assert ledger.snapshot()["races"][0]["attempts"] == 1


def test_separate_connections_cannot_acquire_two_owners(runtime):
    ledger, lease, clock = runtime
    ledger.release_owner(lease)

    def acquire(n):
        independent = RuntimeLedger(ledger.path, clock=lambda: clock[0])
        try:
            return independent.acquire_owner(f"contender-{n}")
        except ForwardError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, [1, 2]))
    assert sum(not isinstance(r, str) for r in results) == 1
    assert sum(r == "collector-already-owned" for r in results) == 1


def test_cooperative_stop_and_explicit_resume(runtime):
    ledger, lease, _ = runtime
    rows = [race()]; ledger.register_day("20260920", evidence(rows), rows, lease)
    ledger.request_stop()
    assert ledger.heartbeat(lease).stop_requested
    assert ledger.claim_due(lease) is None
    ledger.release_owner(lease)
    with pytest.raises(ForwardError, match="explicit-resume"): ledger.acquire_owner("new")
    restarted = ledger.acquire_owner("new", resume_stopped=True)
    assert ledger.claim_due(restarted)["attempts"] == 1


def test_owner_release_inflight_becomes_unknown(runtime):
    ledger, lease, _ = begin(runtime)
    ledger.release_owner(lease)
    assert ledger.snapshot()["races"][0]["status"] == "unknown"
    lease2 = ledger.acquire_owner("new")
    assert ledger.claim_due(lease2) is None


def test_duplicate_schedule_idempotent_no_denominator_removal(runtime):
    ledger, lease, _ = runtime
    rows = [race()]; e = evidence(rows)
    ledger.register_day("20260920", e, rows, lease)
    count = ledger.snapshot()["event_count"]
    ledger.register_day("20260920", e, rows, lease)
    assert ledger.snapshot()["event_count"] == count
    no = evidence([], status="not_held"); no.update(active_venues=[], venue_race_counts={})
    result = ledger.register_day("20260920", no, [], lease)
    assert result["status"] == "partial" and result["scheduled_races"] == 1


def test_schedule_can_change_only_before_first_attempt(runtime):
    ledger, lease, _ = runtime
    rows = [race(post="2026-09-20T15:10:00+09:00")]
    ledger.register_day("20260920", evidence(rows), rows, lease)
    rows = [race()]; ledger.register_day("20260920", evidence(rows), rows, lease)
    ledger.claim_due(lease)
    rows = [race(post="2026-09-20T15:10:00+09:00")]
    result = ledger.register_day("20260920", evidence(rows), rows, lease)
    assert result["status"] == "partial"
    assert ledger.snapshot()["races"][0]["post_time"] == "2026-09-20T06:00:00Z"


def test_late_scheduled_or_queued_races_record_missed(runtime):
    ledger, lease, clock = runtime
    rows = [race()]; ledger.register_day("20260920", evidence(rows), rows, lease)
    with pytest.raises(ForwardError, match="not-yet-missed"):
        ledger.record_terminal(lease, race()["race_id"], "missed", "not yet")
    clock[0] = aware("2026-09-20T05:55:01Z")
    assert ledger.claim_due(lease) is None
    assert ledger.snapshot()["races"][0]["status"] == "missed"
    second = race(2); ledger.register_day("20260920", evidence([race(), second]), [race(), second], lease)
    assert all(r["status"] == "missed" for r in ledger.snapshot()["races"])


def test_capture_atomic_idempotent_and_final_immutable(runtime):
    ledger, lease, clock = begin(runtime)
    result = ledger.record_capture(lease, race()["race_id"], capture(), decision())
    assert result["status"] == "captured" and result["eligible"]
    assert ledger.record_capture(lease, race()["race_id"], capture(), decision())["duplicate"]
    with pytest.raises(ForwardError, match="immutable-capture"):
        ledger.record_capture(lease, race()["race_id"], capture(), {**decision(), "stake_yen": 200})
    ledger.release_owner(lease)
    clock[0] = aware("2026-09-20T06:11:00Z"); lease = ledger.acquire_owner("settle")
    p = ledger.record_settlement(lease, race()["race_id"], official(complete=False))
    assert p["status"] == "payout_pending" and p["payout_yen"] is None
    settled = ledger.record_settlement(lease, race()["race_id"], official())
    assert settled["payout_yen"] == 270 and settled["net_yen"] == 170
    assert ledger.record_settlement(lease, race()["race_id"], official()) == settled
    with pytest.raises(ForwardError, match="immutable-final"):
        ledger.record_settlement(lease, race()["race_id"], official(win_payouts={"1": 280}))
    assert ledger.verify_events()["verified_events"] == ledger.snapshot()["event_count"]


def test_event_chain_and_denominator_cannot_be_deleted(runtime):
    ledger, lease, _ = begin(runtime)
    for sql in ("DELETE FROM events", "UPDATE events SET event_type='changed'", "DELETE FROM races", "DELETE FROM schedule_days", "UPDATE runtime_meta SET value='changed'"):
        with sqlite3.connect(ledger.path) as db, pytest.raises(sqlite3.IntegrityError):
            db.execute(sql)
    assert ledger.verify_events()["verified_events"] > 1


def test_stopped_or_failed_capture_cannot_be_retried(runtime):
    ledger, lease, _ = begin(runtime)
    ledger.record_terminal(lease, race()["race_id"], "failed", "incomplete runner evidence")
    assert ledger.claim_due(lease) is None
    with pytest.raises(ForwardError, match="already-terminal"):
        ledger.record_capture(lease, race()["race_id"], capture(), decision())


def test_compact_status_keeps_unknown_and_avoids_payload_json(runtime):
    ledger, lease, _ = begin(runtime)
    ledger.release_owner(lease)
    state = ledger.compact_status()
    assert len(state["days"]) == 90
    assert state["races"] == [{"race_id": race()["race_id"], "race_date": "20260920",
                               "post_time": "2026-09-20T06:00:00Z", "status": "unknown", "has_snapshot": False}]
    assert state["owner"]["owner_id"] is None


def test_compact_status_reports_committed_snapshot_without_loading_it(runtime):
    ledger, lease, _ = begin(runtime)
    ledger.record_capture(lease, race()["race_id"], capture(), decision())
    state = ledger.compact_status()
    assert state["races"][0]["has_snapshot"] is True
    assert "snapshot" not in state["races"][0] and "runners" not in str(state)

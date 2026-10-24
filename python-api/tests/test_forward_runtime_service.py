import asyncio
from datetime import datetime, timedelta, timezone
import json
import sys
from types import SimpleNamespace

import pytest

from forward_runtime import service
from forward_runtime.schedule import sha256, stamp
from forward_runtime.ledger import RuntimeLedger
from prospective.policy import RULE


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    clock = [datetime(2026, 9, 20, 0, 54, 30, tzinfo=timezone.utc)]
    objects = {"model": b"test-model", "calibrator": b"test-calibrator", "features": json.dumps({"feature_columns": ["safe_age"]}).encode(),
               "rule": json.dumps(RULE).encode()}
    hashes = {k: service.object_bytes(tmp_path, v) for k, v in objects.items()}
    protocol = {"artifact_hashes": hashes, "source_manifest": service.source_manifest(), "feature_columns": ["safe_age"],
                "nullable_feature_columns": [], "date_from": "20260920", "date_to": "20261218"}
    service.exclusive_json(tmp_path / "protocol.json", {"protocol": protocol, "protocol_sha256": sha256(protocol)})
    service.exclusive_json(tmp_path / "local-config.json", {})
    ledger = RuntimeLedger(tmp_path / "ledger.sqlite3", clock=lambda: clock[0])
    ledger.initialize({"protocol_sha256": sha256(protocol), "artifact_hashes": hashes})
    r = service.Runtime(tmp_path, clock=lambda: clock[0])
    r.daily = {"target_date": "20260920", "history_max_source_date": "20260919", "state_sha256": "1" * 64, "bloodline_sha256": "2" * 64}
    r.lease = ledger.acquire_owner("test-owner")
    race = {"race_id": "202606040601", "race_date": "20260920", "post_time": "2026-09-20T10:00:00+09:00", "venue": "06", "race_type": "flat"}
    discovery = {"status": "complete", "calendar_verified": True, "active_venues": ["06"], "venue_race_counts": {"06": 1},
                 "expected_race_ids": [race["race_id"]], "excluded_races": [], "source_url": "https://race.netkeiba.com/test",
                 "source_sha256": "a" * 64, "observed_at": stamp(clock[0]), "received_at": stamp(clock[0])}
    ledger.register_day("20260920", discovery, [race], r.lease)
    r.ledger.claim_due(r.lease)
    odds = {"status": "middle", "data": {"yy": "26", "jyo": "06", "kai": "04", "nichi": "06", "rno": "01",
        "shusso": "2", "touroku": "2", "official_datetime": "2026-09-20 09:54:30", "odds": {"1": {"01": ["4.0"], "02": ["2.0"]}}}}
    calls = []

    async def fetch(url):
        calls.append(url)
        clock[0] += timedelta(seconds=1)
        return {"url": url, "status": 200, "source": "network", "body": json.dumps(odds).encode() if "/api/" in url else b"card"}

    r.transport = fetch

    def infer(card, row):
        assert card == b"card"
        clock[0] += timedelta(seconds=1)
        return {"runners": [{"horse_number": 1, "horse_id": "2020000001", "features": {"safe_age": 6.0}, "score": .6, "win_probability": .6},
                            {"horse_number": 2, "horse_id": "2020000002", "features": {"safe_age": 6.0}, "score": .4, "win_probability": .4}],
                "features_generated_at": stamp(clock[0]), "prediction_at": stamp(clock[0]), "feature_inference_seconds": 1.0,
                "normalized_inputs": [{"horse_number": 1}, {"horse_number": 2}]}

    monkeypatch.setattr(r, "infer", infer)
    return r, race, clock, calls, odds


def test_complete_capture_commit_only_once(runtime):
    r, race, clock, calls, _ = runtime
    result = asyncio.run(r.capture(race))
    assert result["snapshot"]["policy_ready"] is True
    assert len(calls) == 2
    state = r.ledger.snapshot()
    assert state["races"][0]["decision"]["decision"] == "paper_bet"
    assert state["races"][0]["snapshot"]["snapshot_sha256"] == result["snapshot_sha256"]
    assert r.ledger.claim_due(r.lease) is None
    assert state["races"][0]["attempts"] == 1
    assert (r.root / "captures" / (race["race_id"] + ".json")).exists()


def test_stale_tick_not_relabelled_as_fresh(runtime):
    r, race, _, _, odds = runtime
    odds["data"]["official_datetime"] = "2026-09-20 09:45:00"
    with pytest.raises(ValueError, match="stale"):
        asyncio.run(r.capture(race))
    assert not list((r.root / "captures").glob("*.json"))
    assert r.ledger.snapshot()["races"][0]["snapshot"] is None


def test_cached_transport_is_rejected(runtime):
    r, race, _, _, _ = runtime

    async def cached(url):
        return {"url": url, "status": 200, "source": "cache", "body": b"x"}

    r.transport = cached
    with pytest.raises(ValueError, match="fresh-correct"):
        asyncio.run(r.capture(race))


def test_network_redirect_is_rejected(runtime):
    r, race, _, _, _ = runtime

    async def wrong(url):
        return {"url": "https://race.netkeiba.com/other", "status": 200, "source": "network", "body": b"x"}

    r.transport = wrong
    with pytest.raises(ValueError, match="fresh-correct"):
        asyncio.run(r.capture(race))


def test_slow_inference_never_creates_late_capture(runtime, monkeypatch):
    r, race, clock, _, _ = runtime
    original = r.infer

    def slow(*args):
        value = original(*args)
        clock[0] += timedelta(seconds=40)
        return value

    monkeypatch.setattr(r, "infer", slow)
    with pytest.raises(ValueError, match="deadline"):
        asyncio.run(r.capture(race))
    assert r.ledger.snapshot()["races"][0]["snapshot"] is None


def test_missing_odds_horse_rejects_complete_field(runtime):
    r, race, _, _, odds = runtime
    odds["data"]["odds"]["1"]["03"] = odds["data"]["odds"]["1"].pop("02")
    with pytest.raises(ValueError, match="cohort-mismatch"):
        asyncio.run(r.capture(race))


def test_source_edits_require_new_protocol(runtime, monkeypatch):
    r, race, _, calls, _ = runtime
    monkeypatch.setattr(service, "source_manifest", lambda: {})
    with pytest.raises(ValueError, match="source-changed"):
        asyncio.run(r.capture(race))
    assert calls == []


def test_early_capture_does_not_access_network(runtime):
    r, race, clock, calls, _ = runtime
    clock[0] -= timedelta(seconds=1)
    with pytest.raises(ValueError, match="outside-fixed"):
        asyncio.run(r.capture(race))
    assert calls == []


def test_immutable_raw_content_address(runtime):
    r, _, _, _, _ = runtime
    digest = service.object_bytes(r.root, b"raw")
    assert service.object_bytes(r.root, b"raw") == digest
    assert service.file_hash(r.root / "objects" / digest) == digest


def test_cooperative_stop_prevents_claim(runtime):
    r, _, _, _, _ = runtime
    r.ledger.request_stop()
    assert r.ledger.claim_due(r.lease) is None


def test_expired_owner_cannot_commit(runtime):
    r, race, clock, _, _ = runtime
    clock[0] += timedelta(seconds=70)
    with pytest.raises(ValueError, match="outside-fixed"):
        asyncio.run(r.capture(race))


def _daily_files(directory, day):
    state = {"state": {"target_date": day, "history_max_source_date": "20260919"}}
    blood = {"target_date": day, "horses": {}}
    service.exclusive_json(directory / "history_state.json", state)
    service.exclusive_json(directory / "prior_bloodlines.json", blood)
    manifest = {"target_date": day, "status": "completed", "history_max_source_date": "20260919",
                "state_sha256": service.file_hash(directory / "history_state.json"),
                "bloodline_sha256": service.file_hash(directory / "prior_bloodlines.json")}
    service.exclusive_json(directory / "preparation_manifest.json", manifest)
    return manifest


def _no_heavy_work(runtime, monkeypatch):
    r = runtime[0]
    initial = r.root / "initial-yesterday"
    service.exclusive_json(initial / "preparation_manifest.json", {"target_date": "20260919"})
    source = r.root / "source-app/keiba/data/research.db"
    active = r.root / "source-app/python-api/models/.active_model.json"
    source.parent.mkdir(parents=True)
    active.parent.mkdir(parents=True)
    source.write_bytes(b"read-only-production-db-sentinel")
    active.write_bytes(b'{"model_id":"unchanged-active-model"}')
    original = {path: path.read_bytes() for path in (source, active)}
    r.config = {"initial_daily_dir": str(initial), "source_db": str(source)}
    invoked = []

    def forbidden(*args, **kwargs):
        invoked.append((args, kwargs))
        raise AssertionError("daytime must not snapshot, audit, retrain, or rewrite history")

    from forward_runtime import data_quality
    monkeypatch.setattr(data_quality, "audit_clean_history", forbidden)
    monkeypatch.setattr(data_quality, "prepare_daily_history", forbidden)
    monkeypatch.setitem(sys.modules, "training.local_retrain", SimpleNamespace(create_sqlite_snapshot=forbidden))
    return original, invoked


@pytest.mark.parametrize("hour,minute", [(8, 0), (9, 54), (12, 0), (23, 59)])
def test_daytime_preparation_refuses_before_reading_source_db(runtime, monkeypatch, hour, minute):
    r, _, clock, calls, _ = runtime
    original, invoked = _no_heavy_work(runtime, monkeypatch)
    clock[0] = datetime(2026, 9, 20, hour, minute, tzinfo=service.JST).astimezone(timezone.utc)
    r.daily = None
    with pytest.raises(ValueError, match="not-prepared-before-0800"):
        r.prepare_daily("20260920")
    assert invoked == calls == []
    assert not (r.root / "history-snapshots").exists()
    assert not (r.root / "daily/20260920").exists()
    assert {path: path.read_bytes() for path in original} == original


@pytest.mark.parametrize("use_initial", [True, False])
def test_daytime_restart_can_load_verified_prepared_history_without_rebuild(runtime, monkeypatch, use_initial):
    r, _, _, calls, _ = runtime
    original, invoked = _no_heavy_work(runtime, monkeypatch)
    directory = r.root / ("prepared-initial" if use_initial else "daily/20260920")
    expected = _daily_files(directory, "20260920")
    if use_initial:
        r.config["initial_daily_dir"] = str(directory)
    r.daily = None
    assert r.prepare_daily("20260920") == expected
    assert r.daily["history_max_source_date"] < r.daily["target_date"]
    assert invoked == calls == []
    assert {path: path.read_bytes() for path in original} == original


def test_daytime_resume_skips_due_race_without_http_or_production_mutation(runtime, monkeypatch):
    r, race, clock, calls, _ = runtime
    original, invoked = _no_heavy_work(runtime, monkeypatch)
    next_race = {**race, "race_id": "202606040602", "post_time": "2026-09-20T10:10:00+09:00"}
    discovery = {"status": "complete", "calendar_verified": True, "active_venues": ["06"], "venue_race_counts": {"06": 2},
                 "expected_race_ids": [race["race_id"], next_race["race_id"]], "excluded_races": [],
                 "source_url": "https://race.netkeiba.com/test", "source_sha256": "a" * 64,
                 "observed_at": stamp(clock[0]), "received_at": stamp(clock[0])}
    r.ledger.register_day("20260920", discovery, [race, next_race], r.lease)
    r.ledger.release_owner(r.lease)
    clock[0] = datetime(2026, 9, 20, 1, 4, 30, tzinfo=timezone.utc)
    r.daily = None
    preparation_calls = []
    prepare = r.prepare_daily

    def counted_prepare(day):
        preparation_calls.append(day)
        return prepare(day)

    async def no_network_discovery(day):
        return {"status": "test-existing-schedule", "date": day}

    monkeypatch.setattr(r, "prepare_daily", counted_prepare)
    monkeypatch.setattr(r, "discover", no_network_discovery)
    asyncio.run(r.run(once=True))
    state = r.ledger.snapshot()
    record = next(row for row in state["races"] if row["race_id"] == next_race["race_id"])
    assert record["status"] == "skipped"
    assert record["reason"] == "daily-history-not-ready-no-heavy-preparation-in-capture-window"
    assert record["snapshot"] is None and record["decision"] is None
    assert record["attempts"] == 1
    assert preparation_calls == ["20260920"]
    assert state["owner"]["owner_id"] is None
    assert invoked == calls == []
    assert {path: path.read_bytes() for path in original} == original


def test_rule_content_must_match_even_if_rehashed_consistently(runtime):
    r = runtime[0]
    wrong = {**RULE, "minimum_stressed_expected_return": 1.01}
    digest = service.object_bytes(r.root, json.dumps(wrong).encode())
    r.protocol["artifact_hashes"]["rule"] = digest
    r.envelope["protocol_sha256"] = sha256(r.protocol)
    with pytest.raises(ValueError, match="fixed-rule-or-feature"):
        r.verify()

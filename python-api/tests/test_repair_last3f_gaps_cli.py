from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/repair_last3f_gaps.py"
SPEC = importlib.util.spec_from_file_location("last3f_gap_cli_fixture", SCRIPT)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)

RID = "202101010101"
PROTECTED = {"active_model_id": "model_fixture", "active_pointer_sha256": "a" * 64,
             "active_artifact_sha256": "b" * 64, "forward_protocol_sha256": "c" * 64,
             "forward_source_pins": {"fixture": "d" * 64}, "forward_processes": [{"pid": 1}]}


def race(*, rid=RID, day="20210612", surface="芝", clocks=("**", "**")):
    return {"race_info": {"race_id": rid, "date": day, "venue": cli.VENUES.get(rid[4:6], "地方"),
                         "distance": 1600, "track_type": surface, "num_horses": 2},
            "horses": [{"race_id": rid, "horse_number": index, "horse_id": "201810000" + str(index),
                        "horse_name": "Horse" + str(index), "finish_position": index, "finish_time": "1:34.5",
                        "last_3f": clock, "odds": index + 2, "popularity": index}
                       for index, clock in enumerate(clocks, 1)]}


def source_html(*, rid=RID, day="2021年6月12日", clocks=("34.8", "35.1"), canonical=True):
    url = f"https://db.netkeiba.com/race/{rid}/"
    return f'''<html><head><title>Result | {day}</title>
{"<link rel='canonical' href='" + url + "'>" if canonical else ""}</head>
<body><p class="smalltxt">{day} 2頭</p><table class="race_table_01">
<tr><th>馬番</th><th>馬名</th><th>上り</th></tr>
<tr><td>1</td><td><a href="/horse/2018100001/">Horse1</a></td><td>{clocks[0]}</td></tr>
<tr><td>2</td><td><a href="/horse/2018100002/">Horse2</a></td><td>{clocks[1]}</td></tr>
</table></body></html>'''


def insert(db, payload):
    db.execute("INSERT INTO races_ultimate(race_id,data) VALUES (?,?)", (payload["race_info"]["race_id"], json.dumps(payload["race_info"])))
    for horse in payload["horses"]:
        db.execute("INSERT INTO race_results_ultimate(race_id,data) VALUES (?,?)", (payload["race_info"]["race_id"], json.dumps(horse)))


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "preservation_receipt", lambda: deepcopy(PROTECTED))
    monkeypatch.setattr(cli, "lower_priority", lambda: "fixture")
    db_path, cache_path = tmp_path / "data.db", tmp_path / "cache.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE races_ultimate(id INTEGER PRIMARY KEY,race_id TEXT,data TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        db.execute("CREATE TABLE race_results_ultimate(id INTEGER PRIMARY KEY,race_id TEXT,data TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        db.execute("CREATE INDEX fixture_race_results ON race_results_ultimate(race_id)")
        insert(db, race())
    with sqlite3.connect(cache_path) as db:
        db.execute("CREATE TABLE http_cache(normalized_url TEXT PRIMARY KEY,status INTEGER,body BLOB,fetched_at REAL)")
    return db_path, cache_path, tmp_path / "run"


def cached(path, html, *, rid=RID, status=200):
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO http_cache VALUES (?,?,?,?)", (f"https://db.netkeiba.com/race/{rid}/", status, html.encode(),
                  datetime(2026, 10, 17, 7, 0, tzinfo=timezone.utc).timestamp()))


def plan(stores):
    db, cache, run = stores
    return cli.create_plan(run, db_path=db, cache_path=cache)


def contents(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT * FROM race_results_ultimate ORDER BY id").fetchall()


def test_dry_run_is_read_only_and_seals_only_missing_targets(stores):
    db, cache, run = stores
    before = contents(db)
    result = plan(stores)
    assert result["counts"]["target_races"] == 1
    assert result["counts"]["target_gap_runners"] == 2
    assert contents(db) == before
    assert not (run / "events.jsonl").exists()
    loaded, _, targets = cli.load_plan(run, db_path=db)
    assert loaded == result and targets[0]["race_id"] == RID
    assert cli.read_json(run / "status.json")["network_requests"] == 0


def test_good_races_obstacles_future_local_and_unsettled_are_not_targets(stores):
    db, _, _ = stores
    with sqlite3.connect(db) as connection:
        insert(connection, race(rid="202101010102", clocks=("34.1", "35.0")))
        insert(connection, race(rid="202101010103", surface="障害"))
        insert(connection, race(rid="202601010101", day="20261017"))
        insert(connection, race(rid="202144010101", day="20210612"))
        card = race(rid="202101010104")
        card["horses"][0]["finish_position"] = None
        insert(connection, card)
    assert plan(stores)["counts"]["target_races"] == 1


def test_plan_cannot_be_overwritten_and_target_tampering_is_detected(stores):
    _, _, run = stores
    plan(stores)
    initial = (run / "plan.json").read_bytes()
    with pytest.raises(FileExistsError):
        plan(stores)
    assert (run / "plan.json").read_bytes() == initial
    with (run / "targets.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="plan-binding"):
        cli.load_plan(run)


def test_cache_applies_verified_numeric_only_and_resume_is_idempotent(stores):
    db, cache, run = stores
    cached(cache, source_html(canonical=False))
    plan(stores)
    before = contents(db)
    result = asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    assert result["status"] == "completed"
    assert result["updated_runners_this_run"] == 2
    after = contents(db)
    for old, new in zip(before, after):
        assert old[0] == new[0] and old[-1] == new[-1]
        left, right = json.loads(old[2]), json.loads(new[2])
        assert right["last_3f"] > 0 and right["last_3f_time"] > 0
        assert {key: value for key, value in left.items() if key != "last_3f"} == {
            key: value for key, value in right.items() if key not in {"last_3f", "last_3f_time", "_last3f_gap_repair"}}
    events = (run / "events.jsonl").read_bytes()
    second = asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    assert second["processed_this_run"] == 0
    assert contents(db) == after and (run / "events.jsonl").read_bytes() == events


@pytest.mark.parametrize("values,status", [(("34.8", "-"), "partial"), (("34.8", "**"), "partial"), (("**", "**"), "partial")])
def test_domain_missing_partial_and_numeric_recovery_are_distinct(stores, values, status):
    db, cache, run = stores
    cached(cache, source_html(clocks=values))
    plan(stores)
    result = asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    journal = cli.Journal(run, cli.digest_file(run / "plan.json"))
    assert journal.latest[RID]["status"] == status
    assert result["cache_phase_complete"] is True
    assert json.loads(contents(db)[1][2])["last_3f"] == "**"
    assert (result["status"] == "completed_with_domain_missing") is (status == "domain_missing")


def test_only_legitimate_nonfinisher_missing_clock_is_terminal_domain_missing(stores):
    db, cache, run = stores
    with sqlite3.connect(db) as connection:
        row = json.loads(connection.execute("SELECT data FROM race_results_ultimate WHERE id=2").fetchone()[0])
        row.update(finish_position="取消", finish_time=None)
        connection.execute("UPDATE race_results_ultimate SET data=? WHERE id=2", (json.dumps(row),))
    cached(cache, source_html(clocks=("34.8", "-")))
    plan(stores)
    result = asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    assert result["status"] == "completed_with_domain_missing"
    assert result["race_status_counts"] == {"domain_missing": 1}


@pytest.mark.parametrize("patch", [lambda html: html.replace("2021年6月12日", "2021年6月13日"),
                                  lambda html: html.replace("Horse2", "WrongHorse"),
                                  lambda html: html.replace("上り", "上り指数")])
def test_unverified_sources_remain_pending_and_never_write(stores, patch):
    db, cache, run = stores
    cached(cache, patch(source_html()))
    plan(stores)
    before = contents(db)
    result = asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    assert result["race_status_counts"] == {"pending": 1}
    assert contents(db) == before


def test_network_requires_completed_cache_phase_and_explicit_allow_limit(stores):
    db, _, run = stores
    plan(stores)
    with pytest.raises(ValueError, match="cache-pass-must-complete"):
        asyncio.run(cli.apply_run(run, mode="network", limit=1, allow_network=True, db_path=db))
    for kwargs in ({"limit": 1}, {"allow_network": True}):
        with pytest.raises(ValueError, match="explicit-allow-and-positive-limit"):
            asyncio.run(cli.apply_run(run, mode="network", db_path=db, **kwargs))


def test_stop_request_does_not_mark_unvisited_targets_checked(stores):
    db, _, run = stores
    plan(stores)
    cli.exclusive_json(run / "stop-request.json", {"stop": True})
    before = contents(db)
    result = asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    assert result["status"] == "stopped" and result["processed_this_run"] == 0
    assert result["cache_checked_races"] == 0 and contents(db) == before


def test_protected_model_change_blocks_execution(stores, monkeypatch):
    db, _, run = stores
    plan(stores)
    monkeypatch.setattr(cli, "preservation_receipt", lambda: {**PROTECTED, "active_model_id": "model_other"})
    with pytest.raises(ValueError, match="protected-model"):
        asyncio.run(cli.apply_run(run, mode="cache", db_path=db))


def test_proof_and_event_chain_tampering_block_resume(stores):
    db, cache, run = stores
    cached(cache, source_html())
    plan(stores)
    asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    event = json.loads((run / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    proof = run / event["proof_file"]
    proof.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="proof-changed"):
        cli.Journal(run, cli.digest_file(run / "plan.json"))


def test_event_chain_rejects_a_different_plan_and_changed_status(stores):
    _, _, run = stores
    plan(stores)
    plan_sha = cli.digest_file(run / "plan.json")
    cli.Journal(run, plan_sha).emit(phase="cache", race_id=RID, status="pending")
    with pytest.raises(ValueError, match="event-chain-or-plan"):
        cli.Journal(run, "f" * 64)
    data = (run / "events.jsonl").read_text(encoding="utf-8")
    (run / "events.jsonl").write_text(data.replace('"pending"', '"completed"'), encoding="utf-8")
    with pytest.raises(ValueError, match="event-chain-or-plan"):
        cli.Journal(run, plan_sha)


def fake_monitor(monkeypatch, *, healthy=True):
    from market_edge import compute
    monkeypatch.setattr(compute, "CollectorMonitor", lambda *_: SimpleNamespace(snapshot=lambda: {
        "healthy": healthy, "capture_windows": [], "capturing": [], "protocol_sha256": "c" * 64}))


def test_network_only_after_cache_and_one_safe_attempt_per_race(stores, monkeypatch):
    db, _, run = stores
    plan(stores)
    asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    fake_monitor(monkeypatch)
    from scraping import fetch_pipeline
    calls = []

    async def request(session, url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status=200, body=source_html().encode(), source="injected-fixture", error=None, attempts=1)

    monkeypatch.setattr(fetch_pipeline, "fetch_bytes", request)
    result = asyncio.run(cli.apply_run(run, mode="network", limit=1, allow_network=True, db_path=db))
    assert result["status"] == "completed" and result["updated_runners_this_run"] == 2
    assert calls[0][0] == f"https://race.netkeiba.com/race/result.html?race_id={RID}"
    assert calls[0][1]["max_retries"] == 0
    assert calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["use_cache"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("status,source", [(403, "network"), (429, "network"), (401, "access-blocked")])
def test_provider_block_stops_whole_job_without_next_url_or_retry(stores, monkeypatch, status, source):
    db, _, run = stores
    with sqlite3.connect(db) as connection:
        insert(connection, race(rid="202101010102"))
    plan(stores)
    asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    fake_monitor(monkeypatch)
    from scraping import fetch_pipeline
    calls = []

    async def request(session, url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status=status, body=b"", source=source, error=None, attempts=1)

    monkeypatch.setattr(fetch_pipeline, "fetch_bytes", request)
    before = contents(db)
    result = asyncio.run(cli.apply_run(run, mode="network", limit=2, allow_network=True, db_path=db))
    assert result["status"] == "blocked"
    assert len(calls) == 1 and contents(db) == before


def test_unhealthy_collector_pauses_before_any_network(stores, monkeypatch):
    db, _, run = stores
    plan(stores)
    asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    fake_monitor(monkeypatch, healthy=False)
    from scraping import fetch_pipeline

    async def forbidden(*args, **kwargs):
        raise AssertionError("network must not be called")

    monkeypatch.setattr(fetch_pipeline, "fetch_bytes", forbidden)
    result = asyncio.run(cli.apply_run(run, mode="network", limit=1, allow_network=True, db_path=db))
    assert result["status"] == "paused" and result["processed_this_run"] == 0


def test_network_failure_remains_pending_not_zero_filled(stores, monkeypatch):
    db, _, run = stores
    plan(stores)
    asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    fake_monitor(monkeypatch)
    from scraping import fetch_pipeline

    async def request(*args, **kwargs):
        return SimpleNamespace(status=500, body=b"", source="injected-fixture", error="http500", attempts=1)

    monkeypatch.setattr(fetch_pipeline, "fetch_bytes", request)
    before = contents(db)
    result = asyncio.run(cli.apply_run(run, mode="network", limit=1, allow_network=True, db_path=db))
    assert result["status"] == "partial_pending_sources" and contents(db) == before


def test_cache_good_value_conflict_rolls_back_every_proposal(stores):
    db, cache, run = stores
    with sqlite3.connect(db) as connection:
        raw = json.loads(connection.execute("SELECT data FROM race_results_ultimate WHERE id=1").fetchone()[0])
        raw["last_3f"] = "34.7"
        connection.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", (json.dumps(raw),))
    cached(cache, source_html(clocks=("34.8", "35.1")))
    plan(stores)
    before = contents(db)
    result = asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    assert result["race_status_counts"] == {"pending": 1} and contents(db) == before
    event = json.loads((run / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "last3f-existing-clock-source-conflict" in event["cache_failures"]
    assert event["source_proofs"]


@pytest.mark.parametrize("token", ["**", "未計測", "計不", "---"])
def test_shadowed_valid_alias_is_included_in_the_plan(stores, token):
    db, _, _ = stores
    with sqlite3.connect(db) as connection:
        for row_id, raw in connection.execute("SELECT id,data FROM race_results_ultimate"):
            item = json.loads(raw)
            item.update(last_3f_time=token, last_3f=34.8)
            connection.execute("UPDATE race_results_ultimate SET data=? WHERE id=?", (json.dumps(item), row_id))
    assert plan(stores)["counts"]["target_gap_runners"] == 2


def test_cache_limit_and_resume_progress_counts_do_not_skip_unvisited_races(stores):
    db, _, run = stores
    with sqlite3.connect(db) as connection:
        insert(connection, race(rid="202101010102"))
    plan(stores)
    first = asyncio.run(cli.apply_run(run, mode="cache", limit=1, db_path=db))
    assert first["status"] == "partial_cache_pass" and first["cache_phase_complete"] is False
    assert first["cache_checked_races"] == 1
    second = asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    assert second["cache_phase_complete"] is True and second["processed_this_run"] == 1
    assert second["race_status_counts"] == {"pending": 2}


def test_three_unresolved_masked_public_sources_pause_instead_of_bulk_repetition(stores, monkeypatch):
    db, _, run = stores
    with sqlite3.connect(db) as connection:
        for suffix in (2, 3, 4):
            insert(connection, race(rid=f"2021010101{suffix:02d}"))
    plan(stores)
    asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    fake_monitor(monkeypatch)
    from scraping import fetch_pipeline
    calls = []

    async def request(session, url, **kwargs):
        calls.append(url)
        rid = url.split("race_id=")[1]
        return SimpleNamespace(status=200, body=source_html(rid=rid, clocks=("**", "**")).encode(),
                               source="injected-fixture", error=None, attempts=1)

    monkeypatch.setattr(fetch_pipeline, "fetch_bytes", request)
    result = asyncio.run(cli.apply_run(run, mode="network", limit=4, allow_network=True, db_path=db))
    assert len(calls) == 3
    assert result["status"] == "paused" and result["stop_reason"] == "source-still-masked"
    assert result["updated_runners_this_run"] == 0


def test_explicit_member_gate_with_unchanged_masked_clocks_pauses_immediately(stores, monkeypatch):
    db, _, run = stores
    plan(stores)
    asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    fake_monitor(monkeypatch)
    from scraping import fetch_pipeline

    async def request(session, url, **kwargs):
        html = source_html(clocks=("**", "**")).replace("</body>", "<p>有料会員限定</p></body>")
        return SimpleNamespace(status=200, body=html.encode(), source="injected-fixture", error=None, attempts=1)

    monkeypatch.setattr(fetch_pipeline, "fetch_bytes", request)
    result = asyncio.run(cli.apply_run(run, mode="network", limit=1, allow_network=True, db_path=db))
    assert result["status"] == "paused" and result["stop_reason"] == "source-still-masked-member-gate"


def test_network_stop_request_finishes_current_transaction_then_stops(stores, monkeypatch):
    db, _, run = stores
    with sqlite3.connect(db) as connection:
        insert(connection, race(rid="202101010102"))
    plan(stores)
    asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    fake_monitor(monkeypatch)
    from scraping import fetch_pipeline
    calls = []

    async def request(session, url, **kwargs):
        calls.append(url)
        cli.exclusive_json(run / "stop-request.json", {"stop": True})
        return SimpleNamespace(status=200, body=source_html().encode(), source="injected-fixture", error=None, attempts=1)

    monkeypatch.setattr(fetch_pipeline, "fetch_bytes", request)
    result = asyncio.run(cli.apply_run(run, mode="network", limit=2, allow_network=True, db_path=db))
    assert result["status"] == "stopped" and result["updated_runners_this_run"] == 2 and len(calls) == 1
    assert json.loads(contents(db)[2][2])["last_3f"] == "**"


def test_invalid_finish_time_is_not_a_plan_target(stores):
    db, _, _ = stores
    with sqlite3.connect(db) as connection:
        item = json.loads(connection.execute("SELECT data FROM race_results_ultimate WHERE id=2").fetchone()[0])
        item["finish_time"] = None
        connection.execute("UPDATE race_results_ultimate SET data=? WHERE id=2", (json.dumps(item),))
    result = plan(stores)
    assert result["counts"].get("target_races", 0) == 0


def test_alias_conflict_races_are_excluded_not_silently_repaired(stores):
    db, _, _ = stores
    with sqlite3.connect(db) as connection:
        item = json.loads(connection.execute("SELECT data FROM race_results_ultimate WHERE id=1").fetchone()[0])
        item.update(last_3f_time=34.9, last_3f=34.8)
        connection.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", (json.dumps(item),))
    result = plan(stores)
    assert result["counts"].get("target_races", 0) == 0
    assert result["excluded"]["gap_races_with_conflicting_aliases"] == 1


def test_cache_rejected_body_is_retained_with_no_write_authority(stores):
    db, cache, run = stores
    cached(cache, source_html().replace("Horse2", "WrongHorse"))
    plan(stores)
    asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    event = json.loads((run / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    proof = cli.read_json(run / event["source_proofs"][0]["proof_file"])
    assert proof["source_verified"] is False and proof["database_write_authority"] is False
    assert cli.digest_file(run / proof["raw_body_path"]) == proof["raw_body_sha256"]

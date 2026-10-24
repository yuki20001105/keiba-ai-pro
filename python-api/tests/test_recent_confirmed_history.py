"""Small offline schedules and fake HTTP; never call a provider or live DB."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/refresh_recent_confirmed_history.py"
spec = importlib.util.spec_from_file_location("recent_confirmed_history_under_test", SCRIPT)
cli = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cli
spec.loader.exec_module(cli)
RID, DAY = "202605040601", "20261017"
NOW = datetime(2026, 10, 17, 8, tzinfo=timezone.utc)


def candidate():
    return {"race_info": {"race_id": RID, "date": DAY, "venue": "東京", "distance": 1600,
        "track_type": "芝", "num_horses": 3, "field_condition": "良"},
        "horses": [{"race_id": RID, "horse_id": f"202000000{i}", "horse_number": i,
            "horse_name": f"Horse {i}", "finish_position": i, "finish_time": "1:34.5",
            "odds": 2 + i, "popularity": i, "weight_kg": 470 + i} for i in (1, 2, 3)],
        "return_tables": [{"race_id": RID, "bet_type": "単勝", "combinations": "1", "payout": 300, "popularity": 1}]}


def html_for(payload):
    rows = "".join(f'<tr><td>{h["finish_position"]}</td><td>1</td><td>{h["horse_number"]}</td>'
        f'<td><a href="/horse/{h["horse_id"]}/">{h["horse_name"]}</a></td>'
        '<td>牡3</td><td>57</td><td>Jockey</td><td>1:34.5</td></tr>' for h in payload["horses"])
    return '<html><head><title>Result 2026年10月17日</title></head><body><p class="smalltxt">2026年10月17日 3頭</p><table class="race_table_01"><tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>性齢</th><th>斤量</th><th>騎手</th><th>タイム</th></tr>' + rows + '</table></body></html>'


def set_schedule(runtime, *, state="captured", status="complete", excluded=None):
    target = {"race_id": RID, "race_date": DAY, "post_time": "2026-10-17T07:30:00Z", "venue": "05", "race_type": "flat"}
    excluded = excluded or []
    discovery = {"status": status, "expected_race_ids": [RID] + [r["race_id"] for r in excluded], "excluded_races": excluded}
    path = runtime / "ledger.sqlite3"
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE IF NOT EXISTS schedule_days(date TEXT,status TEXT,discovery_json TEXT,discovery_sha256 TEXT); CREATE TABLE IF NOT EXISTS races(race_id TEXT,race_date TEXT,post_time TEXT,venue TEXT,race_json TEXT,status TEXT)")
    db.execute("DELETE FROM schedule_days"); db.execute("DELETE FROM races")
    db.execute("INSERT INTO schedule_days VALUES (?,?,?,?)", (DAY, status, json.dumps(discovery), cli.preparation.object_hash(discovery)))
    db.execute("INSERT INTO races VALUES (?,?,?,?,?,?)", (RID, DAY, target["post_time"], "05", json.dumps(target), state))
    db.commit(); db.close()


@pytest.fixture
def harness(tmp_path, monkeypatch):
    from scraping.storage import _init_sqlite_db
    from scraping.quality import init_acquisition_quality_db
    from services.prediction_source_revision import initialize_source_revision
    from scraping import race
    from scraping.fetch_pipeline import FetchResult
    db = tmp_path / "source.db"
    _init_sqlite_db(db); init_acquisition_quality_db(db)
    assert initialize_source_revision(db.resolve(), True)
    runtime = tmp_path / "runtime"; runtime.mkdir()
    set_schedule(runtime)
    protected = {"active": "unchanged", "daily_bindings": {DAY + ".json": "unchanged"}}
    monkeypatch.setattr(cli.preparation, "protection_receipt", lambda **kw: deepcopy(protected))
    original_new = cli.new_output
    monkeypatch.setattr(cli, "new_output", lambda p: original_new(p, output_root=tmp_path))
    payload, requests, scrapes = candidate(), [], []
    async def transport(session, url, **kwargs):
        requests.append((url, kwargs))
        html = html_for(payload)
        return FetchResult(url, url, 200, html.encode(), "network", 1), html
    async def scrape(session, rid, **kwargs):
        scrapes.append((rid, kwargs))
        fetched, html = await race.fetch_text(session, f"https://db.netkeiba.com/race/{rid}/", max_retries=3)
        value = deepcopy(payload)
        value["_source_evidence"] = race._result_source_evidence(fetched, html, rid, value["horses"])
        return value
    monkeypatch.setattr(race, "fetch_text", transport)
    monkeypatch.setattr(race, "scrape_race_full", scrape)
    run = tmp_path / "run"
    plan = cli.make_plan(run, now=NOW, runtime_root=runtime, source_db=db)
    return {"db": db, "runtime": runtime, "run": run, "plan": plan, "payload": payload,
        "requests": requests, "scrapes": scrapes, "transport": transport, "scrape": scrape}


def execute(h, **kwargs):
    return asyncio.run(cli.execute(h["run"], allow_network=True, now=NOW,
        runtime_root=h["runtime"], source_db=h["db"], **kwargs))


def test_default_plan_is_new_readonly_and_preserves_expected_jump_denominator(harness):
    h = harness
    assert h["requests"] == [] and h["scrapes"] == []
    assert h["plan"]["worker_count"] == 1 and h["plan"]["network_performed"] is False
    set_schedule(h["runtime"], excluded=[{"race_id": "202605040604", "reason": "jump"}])
    schedule = cli.read_schedule(h["runtime"], now=NOW)
    assert len(schedule["targets"]) == 1 and len(schedule["expected_race_ids"]) == 2


@pytest.mark.parametrize("state", ["queued", "capturing"])
def test_still_active_capture_prevents_network(harness, state):
    h = harness; set_schedule(h["runtime"], state=state)
    with pytest.raises(ValueError, match="natural-captures"):
        execute(h)
    assert not h["requests"]


def test_post_five_minute_guard(harness):
    h = harness
    with pytest.raises(ValueError, match="plus-five-minutes"):
        asyncio.run(cli.execute(h["run"], allow_network=True, now=datetime(2026, 10, 17, 7, 34, tzinfo=timezone.utc), runtime_root=h["runtime"], source_db=h["db"]))
    assert not h["requests"]


def test_execute_requires_explicit_network(harness):
    h = harness
    with pytest.raises(ValueError, match="explicit-allow-network"):
        asyncio.run(cli.execute(h["run"], now=NOW, runtime_root=h["runtime"], source_db=h["db"]))


def test_verified_result_is_atomic_journaled_and_forward_ledger_unchanged(harness):
    h = harness
    ledger_sha = cli.preparation.file_hash(h["runtime"] / "ledger.sqlite3")
    report = execute(h)
    assert report["outcome_counts"] == {"saved": 1}
    assert len(h["requests"]) == 1 and report["request_counts_by_race"] == {RID: 1}
    kw = h["requests"][0][1]
    assert kw["max_retries"] == 0 and kw["use_cache"] is False and kw["allow_redirects"] is False
    assert kw["force_refresh"] is True and kw["resume_key"] is None
    assert h["scrapes"][0][1]["results_required"] is True and h["scrapes"][0][1]["enrich_horse_details"] is False
    db = sqlite3.connect(h["db"])
    assert db.execute("SELECT count(*) FROM race_results_ultimate").fetchone()[0] == 3
    evidence = db.execute("SELECT evidence_json FROM data_quality_repair_journal_v1").fetchone()[0]
    assert json.loads(evidence)["not_prestart_capture"] is True
    db.close()
    assert cli.preparation.file_hash(h["runtime"] / "ledger.sqlite3") == ledger_sha


def test_same_batch_is_never_retried(harness):
    h = harness; execute(h)
    with pytest.raises(FileExistsError):
        execute(h)
    assert len(h["requests"]) == 1


def test_normal_saved_result_is_not_requested_or_rewritten(harness):
    from scraping.storage import _save_race_sqlite_only
    h = harness
    assert _save_race_sqlite_only(h["payload"], h["db"])
    db = sqlite3.connect(h["db"])
    before = db.execute("SELECT * FROM race_results_ultimate ORDER BY id").fetchall(); db.close()
    report = execute(h)
    assert report["outcome_counts"] == {"normal_complete_preserved": 1}
    assert h["requests"] == []
    db = sqlite3.connect(h["db"])
    assert db.execute("SELECT * FROM race_results_ultimate ORDER BY id").fetchall() == before
    db.close()


def test_missing_result_is_pending_not_synthetic(harness, monkeypatch):
    from scraping import race
    h = harness
    async def no_result(*args, **kwargs):
        return None
    monkeypatch.setattr(race, "scrape_race_full", no_result)
    report = execute(h)
    assert report["outcome_counts"] == {"pending": 1}
    db = sqlite3.connect(h["db"])
    assert db.execute("SELECT count(*) FROM races_ultimate").fetchone()[0] == 0
    db.close()


def test_second_endpoint_attempt_never_sends_second_request(harness, monkeypatch):
    from scraping import race
    h = harness
    async def repeated(session, rid, **kwargs):
        await race.fetch_text(session, f"https://db.netkeiba.com/race/{rid}/")
        await race.fetch_text(session, f"https://db.sp.netkeiba.com/race/{rid}/")
    monkeypatch.setattr(race, "scrape_race_full", repeated)
    report = execute(h)
    assert report["outcome_counts"] == {"pending": 1}
    assert len(h["requests"]) == 1


def test_existing_provider_block_starts_no_requests(harness, monkeypatch):
    from scraping import fetch_pipeline
    h = harness
    monkeypatch.setattr(fetch_pipeline, "get_fetch_control_status", lambda: {"blocked_reason": "restriction"})
    report = execute(h)
    assert report["outcome_counts"] == {"deferred": 1} and not h["requests"]


def test_wrong_source_identity_is_rejected_by_original_strict_verifier(harness, monkeypatch):
    from scraping import race
    h = harness
    async def mismatched(session, rid, **kwargs):
        value = await h["scrape"](session, rid, **kwargs)
        value["horses"][0]["horse_name"] = "Different horse"
        return value
    monkeypatch.setattr(race, "scrape_race_full", mismatched)
    report = execute(h)
    assert report["outcome_counts"] == {"pending": 1}
    assert "identity-mismatch" in report["outcomes"][0]["reason"]


def test_changed_plan_seal_blocks_execution(harness):
    h = harness
    with (h["run"] / "plan.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="plan-seal"):
        execute(h)
    assert not h["requests"]


def test_schedule_incomplete_or_date_changed_rejects(harness):
    h = harness; set_schedule(h["runtime"], status="partial")
    with pytest.raises(ValueError, match="complete-today"):
        execute(h)


@pytest.mark.parametrize("url", ["https://race.netkeiba.com/race/shutuba.html?race_id=" + RID, "https://db.netkeiba.com/horse/2020000001/"])
def test_no_entry_or_profile_requests(harness, url):
    observer = cli.OneResultObserver(harness["transport"], harness["run"])
    observer.active_race_id = RID
    with pytest.raises(cli.RepairConflict, match="result-only-endpoint"):
        asyncio.run(observer.fetch(None, url))
    assert not harness["requests"]

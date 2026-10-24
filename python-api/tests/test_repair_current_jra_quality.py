"""CLI safety tests: tiny synthetic SQLite and fake transport, no live DB/HTTP."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from scraping import race
from scraping.acquisition_scope import init_scoped_date_ledger, record_scoped_expectation
from scraping.fetch_pipeline import FetchResult
from scraping.quality import init_acquisition_quality_db
from scraping.storage import _init_sqlite_db


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("test_target_repair_current_jra_quality", ROOT / "scripts/repair_current_jra_quality.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
RID = "202605010101"
DAY = "20260104"
PROTECTED = {"active_model_id": "fixture-model", "active_pointer_sha256": "fixture-pointer",
             "forward_source_pins": {"fixture.py": "unchanged"}, "forward_processes": []}


def result(rid=RID, day=DAY):
    return {"race_info": {"race_id": rid, "date": day, "venue": "東京", "distance": 1600,
                          "track_type": "芝", "num_horses": 3, "field_condition": "良"},
            "horses": [{"race_id": rid, "horse_id": f"202000000{i}", "horse_number": i,
                        "horse_name": f"Horse {i}", "finish_position": i, "finish_time": "1:34.5",
                        "odds": 2 + i, "popularity": i, "weight_kg": 470 + i} for i in (1, 2, 3)],
            "return_tables": [{"race_id": rid, "bet_type": "単勝", "combinations": "1", "payout": 300, "popularity": 1}]}


def html_for(payload):
    info = payload["race_info"]
    day = info["date"]
    url = f"https://db.netkeiba.com/race/{info['race_id']}/"
    rows = "".join(f'<tr><td>{h["finish_position"]}</td><td>1</td><td>{h["horse_number"]}</td>'
                   f'<td><a href="/horse/{h["horse_id"]}/">{h["horse_name"]}</a></td>'
                   '<td>牡3</td><td>57</td><td>Jockey</td><td>1:34.5</td></tr>' for h in payload["horses"])
    return (f'<html><head><title>Result | {day[:4]}年{int(day[4:6])}月{int(day[6:])}日</title>'
            f'<link rel="canonical" href="{url}"><meta property="og:url" content="{url}"></head>'
            f'<body><p class="smalltxt">{day[:4]}年{int(day[4:6])}月{int(day[6:])}日</p>'
            '<table class="race_table_01"><tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th>'
            '<th>性齢</th><th>斤量</th><th>騎手</th><th>タイム</th></tr>' + rows + '</table></body></html>')


@pytest.fixture
def harness(tmp_path, monkeypatch):
    from scraping import jra_listing

    db_path = tmp_path / "fixture.sqlite3"
    _init_sqlite_db(db_path)
    init_acquisition_quality_db(db_path)
    init_scoped_date_ledger(db_path)
    record_scoped_expectation(db_path, DAY, [RID])
    audit_dir = tmp_path / "synthetic-audit"
    audit_dir.mkdir()
    (audit_dir / "acquisition_coverage.json").write_text(json.dumps({
        "jra_expected_id_audit_201601_202609": {"raw_absent_expected_race_ids": [RID]},
        "declared_runner_count_mismatches": []}), encoding="utf-8")
    (audit_dir / "acquisition_result_status_clarification.json").write_text(json.dumps({
        "historical_23_race_details": []}), encoding="utf-8")
    run_dir = tmp_path / "fixture-run"
    run_dir.mkdir()
    monkeypatch.setattr(cli, "DB_PATH", db_path)
    monkeypatch.setattr(cli, "preservation_receipt", lambda: deepcopy(PROTECTED))
    monkeypatch.setattr(cli, "record_race_quality", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "invalidate_scoped_no_race_dates", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(cli, "invalidate_verified_no_race_dates", lambda *_args, **_kwargs: 0)
    date_audits = []

    def date_audit(_db, day, ids):
        date_audits.append((day, sorted(ids)))
        return {"race_date": day, "expected_race_count": len(ids)}

    monkeypatch.setattr(cli, "run_scoped_date_repair_audit", date_audit)
    listings = []

    async def empty_listing(day, **kwargs):
        listings.append((day, kwargs))
        return [], "db.sp.netkeiba.com/month-search:verified-empty"

    monkeypatch.setattr(jra_listing, "fetch_jra_race_ids", empty_listing)
    fetches, scrapes = [], []
    payload = result()
    source = html_for(payload)

    async def transport(_session, url, **kwargs):
        fetches.append((url, kwargs))
        return FetchResult(url, url, 200, source.encode(), "network", 1), source

    async def scrape(session, rid, **kwargs):
        scrapes.append((rid, kwargs))
        response, decoded = await race.fetch_text(session, f"https://db.netkeiba.com/race/{rid}/", force_refresh=True)
        candidate = deepcopy(payload)
        candidate["_source_evidence"] = race._result_source_evidence(response, decoded, rid, candidate["horses"])
        return candidate

    monkeypatch.setattr(race, "fetch_text", transport)
    monkeypatch.setattr(race, "scrape_race_full", scrape)
    data = cli.plan(audit_dir, run_dir)
    return {"db": db_path, "run": run_dir, "audit": audit_dir, "plan": data,
            "payload": payload, "source": source, "scrapes": scrapes, "fetches": fetches,
            "listings": listings, "date_audits": date_audits, "original_scrape": scrape,
            "original_transport": transport}


def events(harness):
    path = harness["run"] / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def runner_count(harness):
    with sqlite3.connect(harness["db"]) as connection:
        return connection.execute("SELECT count(*) FROM race_results_ultimate").fetchone()[0]


def run(harness):
    return asyncio.run(cli.execute(harness["plan"], harness["run"]))


def test_plan_is_readonly_and_bounded_to_explicit_jra_candidates(harness):
    assert runner_count(harness) == 0
    assert harness["fetches"] == harness["scrapes"] == harness["listings"] == []
    assert harness["plan"]["normal_data_overwrite"] is False
    assert harness["plan"]["targets"] == [{"race_id": RID, "race_date": DAY, "reasons": ["expected_id_missing"]}]
    assert harness["plan"]["http_minimum_interval_seconds"] >= 1


def test_verified_result_is_journaled_and_saved_resume_does_not_scrape_twice(harness):
    run(harness)
    assert runner_count(harness) == 3
    assert len(harness["scrapes"]) == 1
    assert harness["scrapes"][0][1]["enrich_horse_details"] is False
    assert harness["scrapes"][0][1]["force_refresh"] is True
    first = len(events(harness))
    run(harness)
    assert runner_count(harness) == 3 and len(harness["scrapes"]) == 1
    assert len([event for event in events(harness) if event.get("status") == "saved"]) == 1
    assert len(events(harness)) >= first
    with sqlite3.connect(harness["db"]) as connection:
        rows = connection.execute("SELECT evidence_json FROM data_quality_repair_journal_v1").fetchall()
    assert rows and all(json.loads(row[0])["not_prestart_capture"] for row in rows)


def test_pending_record_is_retried_on_next_explicit_execution(harness, monkeypatch):
    calls = []

    async def scrape(session, rid, **kwargs):
        calls.append(rid)
        if len(calls) == 1:
            raise TimeoutError("synthetic-pending")
        return await harness["original_scrape"](session, rid, **kwargs)

    monkeypatch.setattr(race, "scrape_race_full", scrape)
    run(harness)
    assert runner_count(harness) == 0
    assert events(harness)[-1]["status"] == "pending"
    run(harness)
    assert calls == [RID, RID] and runner_count(harness) == 3


def test_independently_observed_wrong_source_date_is_pending_not_saved(harness, monkeypatch):
    wrong = html_for(result(day="20260105"))

    async def transport(_session, url, **kwargs):
        return FetchResult(url, url, 200, wrong.encode(), "network", 1), wrong

    monkeypatch.setattr(race, "fetch_text", transport)
    run(harness)
    assert runner_count(harness) == 0
    assert events(harness)[-1]["status"] == "pending"


@pytest.mark.parametrize("mismatch", ["body_sha", "horse_id", "horse_name", "horse_number", "finish_position", "canonical_race_id"])
def test_candidate_must_match_the_exact_original_html_not_just_requested_date(harness, monkeypatch, mismatch):
    original = harness["original_scrape"]

    async def scrape(session, rid, **kwargs):
        candidate = await original(session, rid, **kwargs)
        if mismatch == "body_sha":
            candidate["_source_evidence"]["body_sha256"] = "0" * 64
        elif mismatch in {"horse_id", "horse_name", "horse_number"}:
            candidate["horses"][0][mismatch] = {"horse_id": "2099999999", "horse_name": "Other horse", "horse_number": 9}[mismatch]
        elif mismatch == "finish_position":
            candidate["horses"][0]["finish_position"] = 2
        return candidate

    if mismatch == "canonical_race_id":
        wrong = harness["source"].replace(f"/race/{RID}/", "/race/202605010102/")

        async def transport(_session, url, **kwargs):
            return FetchResult(url, url, 200, wrong.encode(), "network", 1), wrong

        monkeypatch.setattr(race, "fetch_text", transport)
    monkeypatch.setattr(race, "scrape_race_full", scrape)
    run(harness)
    assert runner_count(harness) == 0
    assert events(harness)[-1]["status"] == "pending"


def test_quarantined_existing_race_is_not_fetched_or_reset(harness):
    with sqlite3.connect(harness["db"]) as connection:
        connection.execute("INSERT INTO scrape_race_acquisition_state (race_id,race_date,lifecycle,quality_status,excluded_from_standard,first_seen_at,last_attempt_at) VALUES (?,?,'entry','quarantined',1,'fixture','fixture')", (RID, DAY))
    run(harness)
    assert harness["scrapes"] == [] and runner_count(harness) == 0
    assert events(harness)[-1]["status"] == "quarantined_not_reset"


def test_protected_source_change_blocks_before_any_normalization_or_network(harness, monkeypatch):
    changed = deepcopy(PROTECTED)
    changed["active_pointer_sha256"] = "changed"
    monkeypatch.setattr(cli, "preservation_receipt", lambda: changed)
    with pytest.raises(ValueError, match="protected"):
        run(harness)
    assert harness["fetches"] == harness["scrapes"] == harness["listings"] == []
    assert not (harness["run"] / "going_normalization.json").exists()


def test_unverified_discovery_is_not_permanently_cached_as_success(harness, monkeypatch):
    from scraping import jra_listing
    calls = []

    async def listing(day, **kwargs):
        calls.append(day)
        if len(calls) <= 2:
            raise RuntimeError("synthetic-temporary-unverified")
        return [], "db.sp.netkeiba.com/month-search:verified-empty"

    monkeypatch.setattr(jra_listing, "fetch_jra_race_ids", listing)
    run(harness)
    run(harness)
    assert len(calls) == 4


def test_partial_discovery_never_shrinks_existing_expected_race_set(harness, monkeypatch):
    from scraping import jra_listing
    day, first, second = "20261003", "202605040801", "202605040802"
    record_scoped_expectation(harness["db"], day, [first, second])

    async def listing(target, **kwargs):
        return ([first], "fixture-verified-date-list") if target == day else ([], "db.sp.netkeiba.com/month-search:verified-empty")

    monkeypatch.setattr(jra_listing, "fetch_jra_race_ids", listing)
    run(harness)
    with sqlite3.connect(harness["db"]) as connection:
        raw = connection.execute("SELECT expected_race_ids_json FROM scrape_scoped_date_completeness WHERE race_scope='jra' AND race_date=?", (day,)).fetchone()[0]
    assert set(json.loads(raw)) == {first, second}


def test_plan_file_tampering_is_rejected_before_network_or_writes(harness):
    path = harness["run"] / "plan.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["targets"][0]["race_date"] = "20260105"
    cli.write(path, data)
    with pytest.raises(ValueError, match="binding"):
        run(harness)
    assert harness["scrapes"] == harness["listings"] == []
    assert not (harness["run"] / "going_normalization.json").exists()


def test_original_audit_mutation_blocks_resume_before_network_or_writes(harness):
    (harness["audit"] / "acquisition_coverage.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="audit-changed"):
        run(harness)
    assert harness["scrapes"] == harness["listings"] == []


def test_resume_event_from_a_different_plan_is_not_accepted(harness):
    (harness["run"] / "events.jsonl").write_text(json.dumps({
        "race_id": RID, "status": "saved", "plan_sha256": "0" * 64}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="event-plan-mismatch"):
        run(harness)
    assert runner_count(harness) == 0
    assert harness["scrapes"] == harness["listings"] == []


def test_current_horse_result_url_is_equivalent_source_identity(harness, monkeypatch):
    current = harness["source"].replace("/horse/202", "/horse/result/202")

    async def transport(_session, url, **kwargs):
        return FetchResult(url, url, 200, current.encode(), "network", 1), current

    monkeypatch.setattr(race, "fetch_text", transport)
    run(harness)
    assert runner_count(harness) == 3
    assert events(harness)[-1]["status"] == "saved"


@pytest.mark.parametrize("old_aliases", ["both", "canonical_only"])
def test_source_verified_demotion_does_not_leave_an_unparsed_canonical_finish_alias(old_aliases):
    from scraping.safe_repair import merge_result
    from scraping.source_normalization import finish_column_evidence_from_html, normalize_race_payload

    old = result()
    old["horses"][1]["finish_position"] = "2(降)"
    old["horses"][1]["finish"] = "2(降)"
    if old_aliases == "canonical_only":
        old["horses"][1].pop("finish_position")
    original = deepcopy(old)
    source = result()
    source["horses"][1]["finish_position"] = "2(降)"
    html = html_for(source)
    proof = finish_column_evidence_from_html(html, race_id=RID, source_url=f"https://db.netkeiba.com/race/{RID}/")
    candidate = normalize_race_payload(source, finish_column_evidence=proof)
    url = f"https://db.netkeiba.com/race/{RID}/"
    response = FetchResult(url, url, 200, html.encode(), "network", 1)
    candidate["_source_evidence"] = race._result_source_evidence(response, html, RID, candidate["horses"])
    merged = merge_result(old, candidate)
    horse = merged["horses"][1]
    assert horse["finish_position"] == 2 and horse["finish"] == 2
    assert horse["finish_position_raw"] == "2(降)" and horse["finish_annotation"] == "降"
    assert old == original


def test_independent_source_declared_runner_count_conflict_is_pending(harness, monkeypatch):
    wrong = harness["source"].replace('</p>', ' / 2頭</p>')

    async def transport(_session, url, **kwargs):
        return FetchResult(url, url, 200, wrong.encode(), "network", 1), wrong

    monkeypatch.setattr(race, "fetch_text", transport)
    run(harness)
    assert runner_count(harness) == 0
    assert events(harness)[-1]["status"] == "pending"


def test_parser_evidence_runner_list_cannot_differ_from_candidate(harness, monkeypatch):
    async def scrape(session, rid, **kwargs):
        candidate = await harness["original_scrape"](session, rid, **kwargs)
        candidate["_source_evidence"]["runner_identity"][0]["horse_id"] = "2099999999"
        return candidate

    monkeypatch.setattr(race, "scrape_race_full", scrape)
    run(harness)
    assert runner_count(harness) == 0
    assert events(harness)[-1]["status"] == "pending"

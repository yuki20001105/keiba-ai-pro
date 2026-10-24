"""Synthetic-only V3 continuation registration/cache/write/stop safeguards."""
import asyncio
from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("_test_repair_last3f_v3", ROOT / "scripts/repair_last3f_gaps_v3.py")
repair = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = repair
SPEC.loader.exec_module(repair)

RID = "202605010101"
MOBILE = f"https://db.sp.netkeiba.com/race/{RID}/"
DESKTOP = f"https://db.netkeiba.com/race/{RID}/"
OBSERVED = "2026-10-17T10:00:00+00:00"
STAMP = datetime.fromisoformat(OBSERVED).timestamp()


def document(values=("35.1", "35.2"), date="2026年1月4日", canonical=None):
    tag = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    return f'''<html><head><meta charset="utf-8"><title>結果 {date} 東京1R</title>{tag}</head>
<div class="RaceData02">2頭</div><table class="ResultsByRaceDetail">
<tr><th>着順</th><th>馬番</th><th>馬名</th><th>後3F</th></tr>
<tr><td>1</td><td>1</td><td><a href="/horse/2020000001/">テスト1</a></td><td>{values[0]}</td></tr>
<tr><td>2</td><td>2</td><td><a href="/horse/2020000002/">テスト2</a></td><td>{values[1]}</td></tr>
</table></html>'''


@pytest.fixture
def dataset(tmp_path):
    db_path = tmp_path / "only-synthetic-race.db"
    cache_path = tmp_path / "only-synthetic-cache.db"
    meta = {"race_id": RID, "date": "20260104", "venue": "東京", "distance": 1600,
            "track_type": "芝", "num_horses": 2, "field_condition": "良"}
    runners = [{"horse_number": i, "horse_id": f"202000000{i}", "horse_name": f"テスト{i}",
        "finish_position": i, "finish_time": "1:34.5", "jockey_weight": 57,
        "odds": i + 1, "popularity": i, "weight_kg": 470, "last_3f": "**",
        "last_3f_rank": None, "sire": "父", "custom": {"keep": [True, None, "原本"]}}
        for i in (1, 2)]
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT)")
        db.execute("CREATE TABLE race_results_ultimate(id INTEGER PRIMARY KEY,race_id TEXT,data TEXT,created_at TEXT DEFAULT 'original')")
        db.execute("INSERT INTO races_ultimate VALUES (?,?)", (RID, json.dumps(meta, ensure_ascii=False)))
        for i, item in enumerate(runners, 1):
            db.execute("INSERT INTO race_results_ultimate(id,race_id,data) VALUES (?,?,?)", (i, RID, json.dumps(item, ensure_ascii=False)))
    with sqlite3.connect(cache_path) as cache:
        cache.execute("CREATE TABLE http_cache(normalized_url TEXT PRIMARY KEY,status INTEGER,body BLOB,fetched_at REAL)")
    target = {"race_id": RID, "race_date": "20260104", "metadata": repair.V2.metadata_projection(meta),
        "runner_count": 2, "expected_runners": [{key: row[key] for key in ("horse_number", "horse_id", "horse_name")} for row in runners]}
    return SimpleNamespace(db=db_path, cache=cache_path, target=target, runners=runners,
                           plan={"database": str(db_path), "cache_database": str(cache_path), "batch_id": "test-new-v3"})


def put_cache(path, url=MOBILE, body=None, stamp=STAMP, status=200):
    body = document().encode() if body is None else body
    with sqlite3.connect(path) as db:
        db.execute("INSERT OR REPLACE INTO http_cache VALUES(?,?,?,?)", (url, status, body, stamp))
    return body


def raw_rows(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT id,race_id,data,created_at FROM race_results_ultimate ORDER BY id").fetchall()


def valid_probe():
    return {"schema": "last3f-archive-route-probe-v3", "decision": "PASS",
        "source_policy": "archive-mobile", "http_status": 200, "protected_state_unchanged": True,
        "source_db_writes": False, "metrics": {"network_requests": 1}}


def test_cached_mobile_candidate_preserves_original_url_timestamp_hash_and_all_horses(dataset):
    body = put_cache(dataset.cache)
    before = dataset.cache.read_bytes()
    got = repair.cached_candidate(dataset.target, dataset.cache)
    source = got["evidence"]["source"]
    assert got["body"] == body
    assert source["source_url"] == MOBILE and source["observed_at_utc"] == OBSERVED
    assert source["identity_grade"] == "mobile_cached_transport_url_plus_visible_date_and_all_runners"
    assert source["raw_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert source["all_runner_identity_verified"] and source["visible_race_date_verified"]
    assert not source["historical_capture_time_proven"]
    assert got["transport"]["cache_receipt"]["fetched_at"] == STAMP
    assert dataset.cache.read_bytes() == before


@pytest.mark.parametrize("body", [document(date="2026年1月5日").encode(),
    document().replace("テスト2", "違う馬").encode(),
    document(canonical=MOBILE.replace(RID, "202605010102")).encode()])
def test_cached_candidate_rejects_whole_page_on_any_identity_or_date_mismatch(dataset, body):
    put_cache(dataset.cache, body=body)
    got = repair.cached_candidate(dataset.target, dataset.cache)
    assert "evidence" not in got and got["failures"]


@pytest.mark.parametrize("stamp", [None, -1, 0, float("inf"), "invalid"])
def test_invalid_cache_timestamp_is_an_unusable_source_not_an_uncaught_conversion(dataset, stamp):
    put_cache(dataset.cache, stamp=stamp)
    got = repair.cached_candidate(dataset.target, dataset.cache)
    assert "evidence" not in got and got["failures"]


def test_bounded_cache_choices_do_not_discard_a_more_complete_verified_source(dataset):
    put_cache(dataset.cache, body=document(values=("**", "-")).encode())
    put_cache(dataset.cache, url=DESKTOP, body=document(canonical=DESKTOP).encode())
    got = repair.cached_candidate(dataset.target, dataset.cache)
    assert sum(row["last3f_state"] == "valid" for row in got["evidence"]["rows"]) == 2


def test_disagreeing_verified_cache_clocks_are_not_arbitrarily_selected(dataset):
    put_cache(dataset.cache)
    put_cache(dataset.cache, url=DESKTOP, body=document(values=("35.1", "36.2"), canonical=DESKTOP).encode())
    got = repair.cached_candidate(dataset.target, dataset.cache)
    assert "evidence" not in got and got["reason"] == "verified-cache-clock-conflict"


def test_missing_only_apply_keeps_normal_values_and_unrelated_raw_fields(dataset, tmp_path):
    body = put_cache(dataset.cache)
    first = deepcopy(dataset.runners[0]); first["last_3f"] = "35.1"
    with sqlite3.connect(dataset.db) as db:
        db.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", (json.dumps(first, ensure_ascii=False),))
    before = raw_rows(dataset.db)
    run = tmp_path / "new-source-evidence"; run.mkdir()
    item = repair.cached_candidate(dataset.target, dataset.cache)
    result = repair.apply_verified(run, dataset.plan, dataset.target, item)
    after = raw_rows(dataset.db)
    assert result["status"] == "completed" and result["updated_runners"] == 1
    assert before[0] == after[0]  # Exact original JSON bytes and original timestamp.
    previous, fresh = json.loads(before[1][2]), json.loads(after[1][2])
    for key in previous.keys() - {"last_3f"}:
        assert previous[key] == fresh[key]
    assert fresh["last_3f_time"] == fresh["last_3f"] == 35.2
    assert after[1][3] == "original"
    assert (run / result["proof_file"]).is_file()
    assert hashlib.sha256((run / result["proof_file"]).read_bytes()).hexdigest() == result["proof_sha256"]
    assert result["source_body_sha256"] == hashlib.sha256(body).hexdigest()
    with sqlite3.connect(dataset.db) as db:
        journal = db.execute("SELECT before_data,after_data,before_sha256,after_sha256 FROM data_quality_repair_journal_v1").fetchall()
    assert len(journal) == 1
    assert all(hashlib.sha256(raw.encode()).hexdigest() == digest for row in journal for raw, digest in ((row[0], row[2]), (row[1], row[3])))


@pytest.mark.parametrize("values", [("**", "-"), ("0", "bad")])
def test_missing_masked_invalid_source_is_never_zero_filled(dataset, tmp_path, values):
    put_cache(dataset.cache, body=document(values=values).encode())
    before = raw_rows(dataset.db)
    run = tmp_path / "new-source-evidence"; run.mkdir()
    result = repair.apply_verified(run, dataset.plan, dataset.target, repair.cached_candidate(dataset.target, dataset.cache))
    assert result["updated_runners"] == 0 and raw_rows(dataset.db) == before
    assert result["status"] == "partial"


def test_conflicting_normal_clock_rolls_back_the_whole_race(dataset, tmp_path):
    put_cache(dataset.cache)
    first = deepcopy(dataset.runners[0]); first["last_3f"] = "39.9"
    with sqlite3.connect(dataset.db) as db:
        db.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", (json.dumps(first),))
    before = raw_rows(dataset.db)
    run = tmp_path / "new-source-evidence"; run.mkdir()
    result = repair.apply_verified(run, dataset.plan, dataset.target, repair.cached_candidate(dataset.target, dataset.cache))
    assert result["status"] == "pending" and "existing-clock-source-conflict" in result["reason"]
    assert result["updated_runners"] == 0 and raw_rows(dataset.db) == before


@pytest.mark.parametrize("field", ["body", "html", "evidence"])
def test_corrupted_item_source_lineage_is_rejected_before_any_db_update(dataset, tmp_path, field):
    put_cache(dataset.cache)
    item = repair.cached_candidate(dataset.target, dataset.cache)
    if field == "body": item["body"] += b"corrupted"
    elif field == "html": item["html"] += "corrupted"
    else: item["evidence"]["rows"][1]["last3f_seconds"] = 99.0
    before = raw_rows(dataset.db)
    run = tmp_path / "new-source-evidence"; run.mkdir()
    try:
        result = repair.apply_verified(run, dataset.plan, dataset.target, item)
        assert result["status"] == "pending" and result["updated_runners"] == 0
    except (ValueError, KeyError, TypeError):
        pass
    assert raw_rows(dataset.db) == before


def test_source_sealing_failure_cannot_apply_any_db_change(dataset, tmp_path, monkeypatch):
    put_cache(dataset.cache)
    before = raw_rows(dataset.db)
    def failing_seal(*args, **kwargs):
        raise OSError("synthetic immutable proof write failed")
    monkeypatch.setattr(repair.V2, "seal_source", failing_seal)
    with pytest.raises(OSError, match="immutable proof"):
        repair.apply_verified(tmp_path, dataset.plan, dataset.target, repair.cached_candidate(dataset.target, dataset.cache))
    assert raw_rows(dataset.db) == before


def test_register_creates_new_bound_plan_without_mutating_original_files(tmp_path, dataset, monkeypatch):
    parent = tmp_path / "feature-gap"; parent.mkdir()
    old = parent / "original"; old.mkdir()
    previous_output = parent / "original-output"; previous_output.mkdir()
    previous_sources = parent / "original-sources"; previous_sources.mkdir()
    for path in (old / "plan.json", old / "targets.jsonl", old / "events.jsonl", previous_output / "journal_audit.json", previous_sources / "source_integrity_receipt.json"):
        path.write_text("{}\n", encoding="utf-8")
    previous = {key: True for key in ("safety_verification_pass", "source_integrity_pass", "journal_audit_pass",
        "protected_state_unchanged", "verification_source_epoch_stable", "wrapper_and_verification_code_unchanged")}
    previous.update(execution_outcome="stopped", after_verification={"source_epoch": [1, 2, 3]},
                    source_verifier_command=["synthetic-audit", str(previous_sources)])
    (previous_output / "completion_receipt.json").write_text(json.dumps(previous), encoding="utf-8")
    original_files = {path: path.read_bytes() for path in [*old.iterdir(), *previous_output.iterdir(), *previous_sources.iterdir()]}
    old_plan = {"schema": repair.V2.SCHEMA, "batch_id": old.name, "counts": {}, "execution": {}, "source_epoch_at_plan": [0]}
    completed = deepcopy(dataset.target); completed["race_id"] = "202605010102"
    monkeypatch.setattr(repair, "PARENT", parent)
    monkeypatch.setattr(repair, "OLD_RUN", old)
    monkeypatch.setattr(repair, "OLD_OUTPUT", previous_output)
    monkeypatch.setattr(repair, "CODE", ())
    monkeypatch.setattr(repair, "guard", lambda *args: None)
    monkeypatch.setattr(repair, "services", lambda: [])
    monkeypatch.setattr(repair.V2, "load_plan", lambda *args: (old_plan, repair.OLD_PLAN_SHA, [dataset.target, completed]))
    monkeypatch.setattr(repair.V2, "Journal", lambda *args: SimpleNamespace(latest={completed["race_id"]: {"status": "completed"}}))
    new_run, new_output = parent / "new-v3", parent / "new-output-v3"
    envelope = repair.register(new_run, new_output)
    new_plan = json.loads((new_run / "plan.json").read_text(encoding="utf-8"))
    assert new_plan["batch_id"] == new_run.name
    assert new_plan["counts"] == {"target_races": 1, "original_target_races": 2}
    assert len((new_run / "targets.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert envelope["automatic_retries"] == 0 and envelope["normal_values_overwritten"] is False
    assert envelope["old_completion_sha256"] == hashlib.sha256(original_files[previous_output / "completion_receipt.json"]).hexdigest()
    assert all(path.read_bytes() == before for path, before in original_files.items())
    assert old_plan["batch_id"] == old.name and old_plan["source_epoch_at_plan"] == [0]
    assert not new_output.exists()
    with pytest.raises(ValueError, match="new-feature-gap-directory-required"):
        repair.register(new_run, new_output)


def test_existing_or_outside_run_directory_is_rejected_before_registration(tmp_path, monkeypatch):
    parent = tmp_path / "parent"; parent.mkdir()
    existing = parent / "exists"; existing.mkdir()
    monkeypatch.setattr(repair, "PARENT", parent)
    for path in (existing, tmp_path / "outside"):
        with pytest.raises(ValueError, match="new-feature-gap-directory-required"):
            repair.safe_new_path(path)


def test_same_run_and_output_directory_is_rejected_before_original_plan_loading(tmp_path, monkeypatch):
    parent = tmp_path / "parent"; parent.mkdir()
    monkeypatch.setattr(repair, "PARENT", parent)
    with pytest.raises(ValueError, match="separate-resume-and-output-directories-required"):
        repair.register(parent / "new", parent / "new")


def test_network_stop_file_prevents_even_first_request(dataset, tmp_path, monkeypatch):
    import last3f_http_transport_v3 as transport
    run = tmp_path / "new-run"; run.mkdir()
    (run / "stop-request.json").write_text("{}", encoding="utf-8")
    probe = tmp_path / "synthetic-probe.json"
    probe.write_text(json.dumps(valid_probe()), encoding="utf-8")
    envelope = {"network_probe": {"path": str(probe), "sha256": repair.sha(probe)}}
    calls = []
    class ForbiddenClient:
        def __init__(self, *args, **kwargs): pass
        async def fetch(self, *args, **kwargs):
            calls.append("network"); raise AssertionError("must not request")
    monkeypatch.setattr(transport, "Last3FTransportV3", ForbiddenClient)
    monkeypatch.setattr(repair, "guard", lambda *args: None)
    monkeypatch.setitem(sys.modules, "market_edge.compute", SimpleNamespace(CollectorMonitor=lambda *args: SimpleNamespace(snapshot=lambda: {"healthy": True})))
    outcome = asyncio.run(repair.network_pass(run, dataset.plan, envelope, [dataset.target], SimpleNamespace(latest={}), lambda *args, **kwargs: calls.append("record")))
    assert outcome == ("stopped", "operator-stop-request") and calls == []


def test_stop_request_has_priority_over_absent_probe_or_transport_import(dataset, tmp_path):
    (tmp_path / "stop-request.json").write_text("{}", encoding="utf-8")
    outcome = asyncio.run(repair.network_pass(tmp_path, dataset.plan, {}, [dataset.target],
        SimpleNamespace(latest={}), lambda *args, **kwargs: pytest.fail("stop must not journal a fake acquisition")))
    assert outcome == ("stopped", "operator-stop-request")


def test_absent_or_failed_network_probe_never_enters_transport(dataset, tmp_path):
    for envelope in ({}, {"network_probe": None}):
        result = asyncio.run(repair.network_pass(tmp_path, dataset.plan, envelope, [], SimpleNamespace(latest={}), lambda *args, **kwargs: None))
        assert result == ("partial_pending_sources", "archive-network-probe-not-validated")


def test_http400_circuit_stops_before_a_second_target_and_records_diagnostic(dataset, tmp_path, monkeypatch):
    import last3f_http_transport_v3 as transport
    probe = tmp_path / "synthetic-probe.json"
    probe.write_text(json.dumps(valid_probe()), encoding="utf-8")
    envelope = {"network_probe": {"path": str(probe), "sha256": repair.sha(probe)}}
    requests, records = [], []
    diagnostic = {"receipt_file": "synthetic-no-network.json", "receipt_sha256": "a" * 64}
    class CircuitClient:
        def __init__(self, *args, **kwargs):
            assert kwargs["source_policy"] == "archive-mobile"
        async def fetch(self, session, target):
            requests.append(target["race_id"])
            raise transport.TransportHalt("http400-circuit-open", diagnostic)
    monkeypatch.setattr(transport, "Last3FTransportV3", CircuitClient)
    monkeypatch.setattr(repair, "guard", lambda *args: None)
    monkeypatch.setitem(sys.modules, "market_edge.compute", SimpleNamespace(CollectorMonitor=lambda *args: SimpleNamespace(snapshot=lambda: {"healthy": True})))
    second = {**dataset.target, "race_id": "202605010102"}
    outcome = asyncio.run(repair.network_pass(tmp_path, dataset.plan, envelope, [dataset.target, second],
        SimpleNamespace(latest={}), lambda target, item, **kwargs: records.append(item)))
    assert requests == [RID]
    assert outcome == ("blocked", "http-transport-circuit-open-no-retry")
    assert len(records) == 1 and records[0]["status"] == "blocked" and records[0]["updated_runners"] == 0
    assert records[0]["diagnostic"] == diagnostic


@pytest.mark.parametrize("changes", [{"schema": "old-or-fabricated"}, {"http_status": 400},
    {"protected_state_unchanged": False}, {"source_db_writes": True}, {"metrics": {}},
    {"metrics": {"network_requests": 0}}, {"metrics": {"network_requests": 2}},
    {"decision": "FAIL"}, {"source_policy": "current-result"}, {"metrics": {"network_requests": True}}])
def test_invalid_probe_evidence_never_constructs_or_enters_network_client(dataset, tmp_path, monkeypatch, changes):
    import last3f_http_transport_v3 as transport
    receipt = valid_probe(); receipt.update(changes)
    probe = tmp_path / "synthetic-invalid-probe.json"
    probe.write_text(json.dumps(receipt), encoding="utf-8")
    envelope = {"network_probe": {"path": str(probe), "sha256": repair.sha(probe)}}
    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("invalid probe must not instantiate the HTTP client")
    monkeypatch.setattr(transport, "Last3FTransportV3", ForbiddenClient)
    result = asyncio.run(repair.network_pass(tmp_path, dataset.plan, envelope, [dataset.target],
        SimpleNamespace(latest={}), lambda *args, **kwargs: None))
    assert result == ("partial_pending_sources", "archive-network-probe-not-pass")


@pytest.mark.parametrize("member_page,expected_requests", [(False, 3), (True, 1)])
def test_masked_or_member_gated_sources_pause_without_zero_fill_or_more_requests(dataset, tmp_path, monkeypatch, member_page, expected_requests):
    import last3f_http_transport_v3 as transport
    probe = tmp_path / "synthetic-probe.json"
    probe.write_text(json.dumps(valid_probe()), encoding="utf-8")
    envelope = {"network_probe": {"path": str(probe), "sha256": repair.sha(probe)}}
    requests, records = [], []
    class MaskedClient:
        def __init__(self, *args, **kwargs): pass
        async def fetch(self, session, target):
            requests.append(target["race_id"])
            return SimpleNamespace(evidence={}, response=SimpleNamespace(body=b"masked"),
                html="有料会員限定" if member_page else "masked", receipt={}, receipt_file="synthetic.json", receipt_sha256="a" * 64)
    monkeypatch.setattr(transport, "Last3FTransportV3", MaskedClient)
    monkeypatch.setattr(repair, "guard", lambda *args: None)
    monkeypatch.setattr(repair, "apply_verified", lambda *args: {"status": "partial", "updated_runners": 0, "remaining_source_states": {"masked": 2}})
    monkeypatch.setitem(sys.modules, "market_edge.compute", SimpleNamespace(CollectorMonitor=lambda *args: SimpleNamespace(snapshot=lambda: {"healthy": True})))
    targets = [{**dataset.target, "race_id": "20260501010" + str(i)} for i in range(1, 5)]
    outcome = asyncio.run(repair.network_pass(tmp_path, dataset.plan, envelope, targets,
        SimpleNamespace(latest={}), lambda target, item, **kwargs: records.append(item)))
    assert outcome == ("paused", "source-still-masked-member-gate" if member_page else "source-still-masked")
    assert len(requests) == expected_requests == len(records)
    assert all(item["updated_runners"] == 0 and item["remaining_source_states"] == {"masked": 2} for item in records)


@pytest.mark.parametrize("changes", [{"remaining_source_states": {"masked": 1}},
    {"remaining_source_states": {"missing": -1}}, {"remaining_source_states": {"missing": True}},
    {"remaining_gap_runners": 2}, {"identity_grade": "unverified"}, {"proof_file": None},
    {"reason": "source-identity-conflict"}])
def test_unavailable_never_promotes_masked_unverified_or_mismatched_counts(changes):
    item = {"status": "partial", "remaining_source_states": {"missing": 1}, "remaining_gap_runners": 1,
        "proof_file": "evidence/proof.json", "identity_grade": "mobile_cached_transport_url_plus_visible_date_and_all_runners"}
    item.update(changes)
    assert not repair.unavailable(item)


def test_reused_pid_with_different_creation_time_is_not_a_valid_protected_service(monkeypatch):
    import psutil
    monkeypatch.setattr(repair.V2, "_check_protection", lambda *args: None)
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 43.0))
    envelope = {"service_processes": [{"pid": 123, "created_at_epoch": 42.0}], "code_sha256": {},
                "original_artifacts": {}, "original_fingerprints": {}}
    with pytest.raises(ValueError, match="protected-service-process-replaced"):
        repair.guard({}, envelope)

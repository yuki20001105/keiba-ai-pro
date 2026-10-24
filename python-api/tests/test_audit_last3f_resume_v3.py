"""Synthetic SQLite/source fixtures only; no application, network or real DB."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_last3f_resume_v3 as audit
from last3f_evidence_v3 import parse_last3f_evidence_v3
import run_last3f_gap_network_once as old_wrapper
from scraping.last3f_gap_repair import apply_evidence
from scraping.safe_repair import initialize_journal
from services.prediction_source_revision import initialize_source_revision

RIDS = ("202605010101", "202605010102")
DAY = "20260104"
STAMP = "2026-10-17T07:00:00+00:00"


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False), encoding="utf-8")


def html(rid, *, mobile=False, second_missing=False):
    url = f"https://race.netkeiba.com/race/result.html?race_id={rid}"
    canonical = "" if mobile else f'<link rel="canonical" href="{url}">'
    second = "-" if second_missing else "35.2"
    return (f'<html><head>{canonical}<title>2026年1月4日 東京</title></head><body>'
        '<table class="ResultsByRaceDetail"><tr><th>馬番</th><th>馬名</th><th>上り</th></tr>'
        '<tr><td>1</td><td><a href="/horse/2020000001/">テスト1</a></td><td>35.1</td></tr>'
        f'<tr><td>2</td><td><a href="/horse/2020000002/">テスト2</a></td><td>{second}</td></tr>'
        '</table></body></html>')


def make_evidence(target, *, mobile=False, second_missing=False):
    text = html(target["race_id"], mobile=mobile, second_missing=second_missing)
    body = text.encode()
    url = (f'https://db.sp.netkeiba.com/race/{target["race_id"]}/' if mobile
           else f'https://race.netkeiba.com/race/result.html?race_id={target["race_id"]}')
    kwargs = dict(race_id=target["race_id"], source_url=url, observed_at_utc=STAMP,
        raw_body_sha256=hashlib.sha256(body).hexdigest(), raw_body=body,
        expected_date=DAY, expected_runners=target["expected_runners"])
    if mobile:
        kwargs.update(allow_cached_transport_identity=True, cache_receipt={"normalized_url": url,
            "status": 200, "body_sha256": kwargs["raw_body_sha256"],
            "fetched_at": datetime.fromisoformat(STAMP).timestamp()})
    return parse_last3f_evidence_v3(text, **kwargs), body, text


def seal(run, target, *, mobile=False, second_missing=False):
    evidence, raw, text = make_evidence(target, mobile=mobile, second_missing=second_missing)
    raw_sha = hashlib.sha256(raw).hexdigest()
    html_sha = hashlib.sha256(text.encode()).hexdigest()
    raw_path = run / "sources/raw" / (raw_sha + ".bin")
    decoded_path = run / "sources/decoded" / (html_sha + ".html")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    decoded_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(raw)
    decoded_path.write_text(text, encoding="utf-8")
    proof = {"schema": "last3f-repair-source-proof-v1", "evidence": evidence,
        "transport": {"phase": "cache" if mobile else "network"},
        "raw_body_path": raw_path.relative_to(run).as_posix(),
        "decoded_html_path": decoded_path.relative_to(run).as_posix()}
    proof_bytes = audit.canonical(proof)
    proof_sha = hashlib.sha256(proof_bytes).hexdigest()
    path = run / "evidence" / (proof_sha + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(proof_bytes)
    event = {"phase": "cache" if mobile else "network", "race_id": target["race_id"],
        "status": "partial" if second_missing else "completed", "remaining_gap_runners": 1 if second_missing else 0,
        "remaining_source_states": {"missing": 1} if second_missing else {},
        "proof_file": path.relative_to(run).as_posix(), "proof_sha256": proof_sha,
        "identity_grade": evidence["source"]["identity_grade"]}
    return evidence, event


def write_plan(run, db, targets, root):
    run.mkdir(parents=True, exist_ok=True)
    with (run / "targets.jsonl").open("w", encoding="utf-8") as stream:
        for target in targets.values():
            stream.write(json.dumps(target, ensure_ascii=False) + "\n")
    code = root / "scripts/audit-fixture.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("# synthetic frozen source\n", encoding="utf-8")
    plan = {"schema": "last3f-gap-repair-plan-v2", "batch_id": run.name, "database": str(db),
        "targets_file": "targets.jsonl", "targets_sha256": audit.sha(run / "targets.jsonl"),
        "counts": {"target_races": len(targets)}, "code_sha256": {"scripts/audit-fixture.py": audit.sha(code)}}
    dump(run / "plan.json", plan)
    dump(run / "plan_receipt.json", {"batch_id": run.name, "plan_sha256": audit.sha(run / "plan.json")})


def write_events(run, events, *, state="completed"):
    tail = None
    plan_sha = audit.sha(run / "plan.json")
    with (run / "events.jsonl").open("wb") as stream:
        for original in events:
            item = {**deepcopy(original), "plan_sha256": plan_sha, "previous_event_sha256": tail,
                    "recorded_at_utc": STAMP}
            tail = hashlib.sha256(audit.canonical(item)).hexdigest()
            stream.write(audit.canonical({**item, "event_sha256": tail}) + b"\n")
    status = {"schema": "last3f-gap-status-v2", "status": state, "plan_sha256": plan_sha,
        "event_count": len(events), "last_event_sha256": tail,
        "stop_reason": "operator-stop-request" if state == "stopped" else None}
    dump(run / "status.json", status)
    return status


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    parent = root / "reports/generated/data-quality/feature-gap"
    old_run, old_output, new_run = parent / "original-v2", parent / "old-output", parent / "resume-v3"
    old_run.mkdir(parents=True); old_output.mkdir(); new_run.mkdir()
    path = root / "keiba/data/keiba_ultimate.db"
    path.parent.mkdir(parents=True)
    targets = {}
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT,created_at TEXT DEFAULT 'original')")
        db.execute("CREATE TABLE race_results_ultimate(id INTEGER PRIMARY KEY,race_id TEXT,data TEXT,created_at TEXT DEFAULT 'original')")
        for rid in RIDS:
            runners = [{"race_id": rid, "horse_number": i, "horse_id": f"202000000{i}", "horse_name": f"テスト{i}",
                "finish_position": i, "finish_time": "1:34.5", "odds": i + 1, "popularity": i,
                "last_3f": "**", "last_3f_rank": None} for i in (1, 2)]
            metadata = {"race_id": rid, "date": DAY, "venue": "東京", "distance": 1600,
                "track_type": "芝", "num_horses": 2, "field_condition": "良"}
            db.execute("INSERT INTO races_ultimate(race_id,data)VALUES(?,?)", (rid, json.dumps(metadata)))
            for runner in runners:
                db.execute("INSERT INTO race_results_ultimate(race_id,data)VALUES(?,?)", (rid, json.dumps(runner)))
            targets[rid] = {"race_id": rid, "race_date": DAY, "metadata": metadata, "runner_count": 2,
                "expected_runners": [{key: row[key] for key in ("horse_number", "horse_id", "horse_name")} for row in runners]}
    assert initialize_source_revision(path.resolve(), True)
    initialize_journal(path)
    write_plan(old_run, path, targets, root)
    monkeypatch.setattr(audit, "ORIGINAL_PLAN_SHA256", audit.sha(old_run / "plan.json"))
    monkeypatch.setattr(audit, "ORIGINAL_TARGET_RACES", 2)
    old_evidence, old_event = seal(old_run, targets[RIDS[0]], second_missing=True)
    apply_evidence(path, old_evidence, batch_id=old_run.name)
    old_status = write_events(old_run, [old_event, {"phase": "network", "race_id": RIDS[1], "status": "pending",
        "reason": "http-400", "http_status": 400, "updated_runners": 0}], state="stopped")
    source_output = parent / "old-sources"; source_output.mkdir()
    old_sources = audit.source_checker.verify([old_run], repository_root=root)
    assert old_sources["artifact_integrity_pass"], old_sources["issues"]
    dump(source_output / "source_integrity_receipt.json", old_sources)
    old_journal = old_wrapper.journal_audit(path, old_run.name, source_receipt=old_sources, run_dir=old_run)
    assert old_journal["audit_pass"]
    dump(old_output / "journal_audit.json", old_journal)
    receipt = {"schema": "last3f-single-network-run-wrapper-v1", "batch_id": old_run.name,
        "plan_sha256": audit.sha(old_run / "plan.json"), "execution_outcome": "stopped", "child_final_status": old_status,
        "child": {"confirmed_exited": True, "exit_code": 0}, "source_output_dir": str(source_output),
        "source_integrity_receipt_sha256": audit.sha(source_output / "source_integrity_receipt.json"),
        "journal_audit_sha256": audit.sha(old_output / "journal_audit.json")}
    receipt.update(dict.fromkeys(["safety_verification_pass", "source_integrity_pass", "journal_audit_pass",
        "protected_state_unchanged", "verification_source_epoch_stable", "wrapper_and_verification_code_unchanged"], True))
    dump(old_output / "completion_receipt.json", receipt)
    write_plan(new_run, path, targets, root)
    new_events = []
    for target in targets.values():
        evidence, event = seal(new_run, target, mobile=True)
        apply_evidence(path, evidence, batch_id=new_run.name)
        new_events.append(event)
    write_events(new_run, new_events)
    return dict(root=root, parent=parent, old_run=old_run, old_output=old_output, new_run=new_run,
                db=path, targets=targets, new_events=new_events)


def run(fixture, suffix="audit"):
    return audit.audit_resume(fixture["old_run"], fixture["old_output"], fixture["new_run"],
                              fixture["parent"] / suffix, repository_root=fixture["root"])


def test_two_batches_pass_without_relabelling_old_stop_or_writing_db(fixture):
    db_sha, old_sha = audit.sha(fixture["db"]), audit.sha(fixture["old_output"] / "completion_receipt.json")
    result = run(fixture)
    assert result["safety_verification_pass"] is True
    assert result["old_status_preserved"] == "stopped"
    assert audit.sha(fixture["db"]) == db_sha
    assert audit.sha(fixture["old_output"] / "completion_receipt.json") == old_sha
    coverage = audit.read(result["combined_coverage_audit"])
    assert coverage["original_target_races"] == 2 and coverage["race_status_counts"] == {"completed": 2}
    assert coverage["all_original_targets_accounted"] and coverage["pending"] == coverage["http400"] == 0
    journal = audit.read(result["journal_audit"])
    assert journal["counts"]["journal_entries"] == journal["counts"]["unique_repaired_rows"] == 4
    assert journal["entries_by_batch"] == {"original-v2": 1, "resume-v3": 3}
    assert journal["not_full_source_preservation_audit"] is True
    assert result["network_requests"] == 0


@pytest.mark.parametrize("state", ["pending", "blocked", "paused", "masked", "stopped"])
def test_unresolved_or_blocked_sources_never_pass(fixture, state):
    events = deepcopy(fixture["new_events"])
    events[1] = {"phase": "network", "race_id": RIDS[1], "status": state, "http_status": 400,
                 "reason": "http-400", "updated_runners": 0}
    write_events(fixture["new_run"], events, state="partial_pending_sources")
    result = run(fixture)
    assert result["safety_verification_pass"] is False
    assert result["coverage_pass"] is False
    assert audit.read(result["combined_coverage_audit"])["http400"] == 1


def test_wrong_before_hash_is_not_hidden_by_current_tail(fixture):
    with sqlite3.connect(fixture["db"]) as db:
        db.execute("UPDATE data_quality_repair_journal_v1 SET before_sha256='wrong' WHERE batch_id=?", (fixture["new_run"].name,))
    result = run(fixture)
    assert not result["journal_audit_pass"]
    assert audit.read(result["journal_audit"])["issues"]["before_hash_mismatch"] == 3


def test_cross_batch_discontinuity_is_detected(fixture):
    with sqlite3.connect(fixture["db"]) as db:
        db.execute("UPDATE data_quality_repair_journal_v1 SET row_key='1' WHERE batch_id=? AND row_key='2'", (fixture["new_run"].name,))
    result = run(fixture)
    assert not result["journal_audit_pass"]
    issues = audit.read(result["journal_audit"])["issues"]
    assert issues["global_cross_batch_journal_discontinuity"] == 1
    assert issues["journal_tail_differs_from_current_row_external_change_possible"] == 1


def test_even_consistent_hashes_cannot_authorize_unrelated_field_change(fixture):
    with sqlite3.connect(fixture["db"]) as db:
        row = db.execute("SELECT id,row_key,after_data FROM data_quality_repair_journal_v1 WHERE batch_id=? ORDER BY id LIMIT 1",
                         (fixture["new_run"].name,)).fetchone()
        value = json.loads(row[2]); value["odds"] = 777
        raw = json.dumps(value)
        db.execute("UPDATE data_quality_repair_journal_v1 SET after_data=?,after_sha256=? WHERE id=?",
                   (raw, hashlib.sha256(raw.encode()).hexdigest(), row[0]))
        db.execute("UPDATE race_results_ultimate SET data=? WHERE id=?", (raw, row[1]))
    result = run(fixture)
    assert not result["journal_audit_pass"]
    assert audit.read(result["journal_audit"])["issues"]["last3f_unrelated_or_no_clock_change"] == 1


def test_current_tail_changed_outside_journal_fails(fixture):
    with sqlite3.connect(fixture["db"]) as db:
        db.execute("UPDATE race_results_ultimate SET data=json_set(data,'$.weather','雨') WHERE id=1")
    result = run(fixture)
    assert not result["journal_audit_pass"]
    assert audit.read(result["journal_audit"])["issues"]["journal_tail_differs_from_current_row_external_change_possible"] == 1


def test_stopped_receipt_cannot_be_renamed_success(fixture):
    path = fixture["old_output"] / "completion_receipt.json"
    receipt = audit.read(path); receipt["execution_outcome"] = "completed"; dump(path, receipt)
    with pytest.raises(ValueError, match="original-stopped-receipt"):
        run(fixture)


def test_resumed_identity_metadata_must_match_original_exactly(fixture):
    changed = deepcopy(fixture["targets"])
    changed[RIDS[0]]["metadata"]["distance"] = 2000
    write_plan(fixture["new_run"], fixture["db"], changed, fixture["root"])
    write_events(fixture["new_run"], fixture["new_events"])
    with pytest.raises(ValueError, match="identity-or-metadata-changed"):
        run(fixture)


def test_preexisting_receipts_are_never_overwritten(fixture):
    first = run(fixture)
    receipt = Path(first["combined_coverage_audit"])
    before = receipt.read_bytes()
    with pytest.raises(ValueError, match="new-exclusive-output"):
        run(fixture)
    assert receipt.read_bytes() == before


def test_private_field_url_extension_leaves_production_validator_unchanged():
    import audit_feature_gap_updates as original
    previous = original._url_race_id
    private = audit.private_field_auditor()
    url = f"https://db.sp.netkeiba.com/race/{RIDS[0]}/"
    assert private._url_race_id(url) == RIDS[0]
    assert original._url_race_id is previous
    with pytest.raises(ValueError, match="not-allowlisted"):
        original._url_race_id(url)


def test_source_epoch_drift_blocks_audit_even_with_valid_named_row_tails(fixture, monkeypatch):
    real = audit.epoch
    calls = []
    def changed(path):
        state = real(path)
        calls.append(1)
        return state if len(calls) < 3 else [state[0], state[1] + 1, state[2]]
    monkeypatch.setattr(audit, "epoch", changed)
    result = run(fixture)
    assert result["verification_source_epoch_stable"] is False
    assert result["safety_verification_pass"] is False


def test_mobile_cached_time_and_canonical_conflicts_rejected(fixture):
    sources = audit.source_checker.verify([fixture["old_run"], fixture["new_run"]], repository_root=fixture["root"])
    event = fixture["new_events"][0]
    _, proof = audit.checked_proof(fixture["new_run"], event, sources["file_receipts"])
    evidence = deepcopy(proof["evidence"])
    evidence["source"]["cached_transport_receipt"]["fetched_at"] += 1
    with pytest.raises(ValueError, match="observation-time-mismatch"):
        audit._verified_source(evidence, fixture["targets"][RIDS[0]], fixture["new_run"], sources["file_receipts"])
    evidence = deepcopy(proof["evidence"])
    evidence["source"]["canonical_urls"] = [f"https://db.sp.netkeiba.com/race/{RIDS[1]}/"]
    with pytest.raises(ValueError, match="canonical-flag-or-id-conflict"):
        audit._verified_source(evidence, fixture["targets"][RIDS[0]], fixture["new_run"], sources["file_receipts"])

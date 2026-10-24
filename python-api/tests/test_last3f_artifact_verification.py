"""Independent source/event integrity fixtures; never read user DBs or HTTP."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_last3f_gap_artifacts as verify

RID = "202605010101"
IDENTITY = {"horse_number": 1, "horse_id": "2020000001", "horse_name": "テスト"}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(verify.canonical(value) + b"\n")


def sealed(run, relative_dir, body, suffix):
    digest = verify.sha_bytes(body)
    path = run / relative_dir / (digest + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path.relative_to(run).as_posix(), digest


def rewrite_event(run, change):
    event = json.loads((run / "events.jsonl").read_bytes())
    event.pop("event_sha256")
    change(event)
    event_sha = verify.sha_bytes(verify.canonical(event))
    write(run / "events.jsonl", {**event, "event_sha256": event_sha})
    return event_sha


@pytest.fixture
def fixture(tmp_path):
    run = tmp_path / "fixture-run"
    run.mkdir()
    code = tmp_path / "parser.py"
    code.write_bytes(b"# synthetic parser\n")
    target = {"race_id": RID, "race_date": "20260104", "runner_count": 1, "expected_runners": [IDENTITY]}
    write(run / "targets.jsonl", target)
    plan = {"schema": "last3f-gap-repair-plan-v2", "batch_id": run.name,
        "targets_file": "targets.jsonl", "targets_sha256": verify.sha_bytes((run / "targets.jsonl").read_bytes()),
        "counts": {"target_races": 1}, "code_sha256": {"parser.py": verify.sha_bytes(code.read_bytes())}}
    write(run / "plan.json", plan)
    plan_sha = verify.sha_bytes((run / "plan.json").read_bytes())
    write(run / "plan_receipt.json", {"batch_id": run.name, "plan_sha256": plan_sha})
    raw_path, raw_sha = sealed(run, "sources/raw", b"<html>original raw response</html>", ".bin")
    decoded_path, decoded_sha = sealed(run, "sources/decoded", "<html>decoded 日本語</html>".encode(), ".html")
    evidence = {"schema": "last3f-source-evidence-v1", "race_id": RID, "race_date": "20260104",
        "runner_count": 1, "rows": [{**IDENTITY, "last3f_state": "valid", "last3f_seconds": 35.1}],
        "source": {"raw_body_sha256": raw_sha, "decoded_html_sha256": decoded_sha,
                   "raw_digest_verification": "verified_against_bytes", "historical_capture_time_proven": False}}
    proof = {"schema": "last3f-repair-source-proof-v1", "evidence": evidence,
             "raw_body_path": raw_path, "decoded_html_path": decoded_path, "transport": {"fixture": True}}
    proof_path, proof_sha = sealed(run, "evidence", verify.canonical(proof), ".json")
    event = {"plan_sha256": plan_sha, "previous_event_sha256": None, "race_id": RID,
             "status": "completed", "proof_file": proof_path, "proof_sha256": proof_sha}
    event_sha = verify.sha_bytes(verify.canonical(event))
    write(run / "events.jsonl", {**event, "event_sha256": event_sha})
    write(run / "status.json", {"plan_sha256": plan_sha, "event_count": 1, "last_event_sha256": event_sha})
    return run, tmp_path, proof, proof_path


def result(fixture):
    return verify.verify([fixture[0]], repository_root=fixture[1])


def test_independent_raw_decoded_proof_plan_and_event_chain_verify(fixture):
    receipt = result(fixture)
    assert receipt["artifact_integrity_pass"] is True
    assert receipt["database_opened"] is False and receipt["network_requests"] == 0
    assert receipt["historical_prestart_availability_proven"] is False
    assert receipt["runs"][0]["unique_raw_bodies"] == receipt["runs"][0]["unique_decoded_html"] == 1
    assert receipt["runs"][0]["referenced_proof_count"] == 1


@pytest.mark.parametrize("kind", ["raw", "decoded"])
def test_actual_body_tampering_is_found_even_with_untouched_proof_json(fixture, kind):
    run, _, proof, _ = fixture
    key = "raw_body_path" if kind == "raw" else "decoded_html_path"
    (run / proof[key]).write_bytes(b"tampered source bytes")
    receipt = result(fixture)
    assert receipt["artifact_integrity_pass"] is False
    assert receipt["issues"]["artifact_sha256_mismatch"] >= 1


def test_missing_body_is_reported(fixture):
    run, _, proof, _ = fixture
    (run / proof["raw_body_path"]).unlink()
    receipt = result(fixture)
    assert receipt["artifact_integrity_pass"] is False
    assert receipt["issues"]["source_proof_or_body_invalid"] == 1


def test_modified_proof_json_is_not_accepted_by_event_digest(fixture):
    run, _, proof, proof_path = fixture
    proof["evidence"]["rows"][0]["last3f_seconds"] = 99.0
    (run / proof_path).write_bytes(verify.canonical(proof))
    receipt = result(fixture)
    assert receipt["artifact_integrity_pass"] is False
    assert receipt["issues"]["content_address_filename_mismatch"] >= 1


def test_event_mutation_and_missing_last_event_are_detected(fixture):
    run = fixture[0]
    body = (run / "events.jsonl").read_bytes()
    (run / "events.jsonl").write_bytes(body.replace(b'"completed"', b'"pending"'))
    assert result(fixture)["issues"]["event_sha_chain_invalid"] == 1
    (run / "events.jsonl").write_bytes(b"")
    assert result(fixture)["issues"]["event_tail_status_anchor_mismatch"] == 1


def test_valid_individual_event_hash_cannot_hide_broken_previous_link(fixture):
    run = fixture[0]
    first = json.loads((run / "events.jsonl").read_bytes())
    second = {key: value for key, value in first.items() if key != "event_sha256"}
    second["previous_event_sha256"] = "0" * 64
    second["status"] = "completed-again"
    tail = verify.sha_bytes(verify.canonical(second))
    with (run / "events.jsonl").open("ab") as stream:
        stream.write(verify.canonical({**second, "event_sha256": tail}) + b"\n")
    status = json.loads((run / "status.json").read_bytes())
    write(run / "status.json", {**status, "event_count": 2, "last_event_sha256": tail})
    receipt = result(fixture)
    assert receipt["issues"]["event_sha_chain_invalid"] == 1


def test_events_cannot_reference_a_different_plan_even_if_their_hash_is_resealed(fixture):
    run = fixture[0]
    tail = rewrite_event(run, lambda event: event.update(plan_sha256="0" * 64))
    status = json.loads((run / "status.json").read_bytes())
    write(run / "status.json", {**status, "last_event_sha256": tail})
    assert result(fixture)["issues"]["event_sha_chain_invalid"] == 1


def test_different_plan_receipt_and_targets_tampering_are_detected(fixture):
    run = fixture[0]
    receipt = json.loads((run / "plan_receipt.json").read_bytes())
    receipt["plan_sha256"] = "0" * 64
    write(run / "plan_receipt.json", receipt)
    with (run / "targets.jsonl").open("ab") as stream:
        stream.write(b" \n")
    assert result(fixture)["artifact_integrity_pass"] is False


@pytest.mark.parametrize("reference", ["../outside.html", "/outside.html", "sources/raw/../../plan.json"])
def test_source_path_cannot_escape_run_even_when_proof_and_event_are_resealed(fixture, reference):
    run, _, proof, _ = fixture
    proof["raw_body_path"] = reference
    proof_path, proof_sha = sealed(run, "evidence", verify.canonical(proof), ".json")
    tail = rewrite_event(run, lambda event: event.update(proof_file=proof_path, proof_sha256=proof_sha))
    status = json.loads((run / "status.json").read_bytes()); status["last_event_sha256"] = tail
    write(run / "status.json", status)
    assert result(fixture)["issues"]["source_proof_or_body_invalid"] >= 1


def test_all_unreferenced_proofs_are_checked(fixture):
    run, _, proof, _ = fixture
    proof["evidence"]["rows"][0]["last3f_seconds"] = 36.0
    sealed(run, "evidence", verify.canonical(proof), ".json")
    receipt = result(fixture)
    assert receipt["artifact_integrity_pass"] is True
    assert receipt["runs"][0]["unreferenced_proof_count"] == 1


def test_orphan_body_file_and_wrong_content_address_are_detected(fixture):
    run = fixture[0]
    sealed(run, "sources/raw", b"source with no proof", ".bin")
    (run / "sources/raw" / ("0" * 64 + ".bin")).write_bytes(b"not that digest")
    receipt = result(fixture)
    assert receipt["issues"]["source_body_without_proof"] == 2
    assert receipt["issues"]["content_address_filename_mismatch"] >= 1


def test_proof_must_match_fixed_all_runner_identity(fixture):
    run, _, proof, _ = fixture
    proof["evidence"]["rows"][0]["horse_id"] = "wrong"
    sealed(run, "evidence", verify.canonical(proof), ".json")
    receipt = result(fixture)
    assert receipt["issues"]["proof_target_identity_or_date_mismatch"] == 1


def test_code_changes_are_reported_without_reading_arbitrary_non_python_files(fixture):
    (fixture[1] / "parser.py").write_bytes(b"changed")
    assert result(fixture)["issues"]["artifact_sha256_mismatch"] == 1


def test_body_file_change_after_first_read_is_detected_before_final_receipt(fixture, monkeypatch):
    original = verify.Checker.final_stability
    path = fixture[0] / fixture[2]["raw_body_path"]
    def change_then_check(checker):
        path.write_bytes(b"late mutation")
        original(checker)
    monkeypatch.setattr(verify.Checker, "final_stability", change_then_check)
    assert result(fixture)["issues"]["artifact_changed_before_receipt"] == 1


def test_rejected_parser_source_is_integrity_checked_without_becoming_write_authority(fixture):
    run, _, proof, _ = fixture
    rejected = {"schema": "last3f-rejected-source-proof-v1", "race_id": RID,
        "source_verified": False, "database_write_authority": False,
        "raw_body_sha256": proof["evidence"]["source"]["raw_body_sha256"],
        "decoded_html_sha256": proof["evidence"]["source"]["decoded_html_sha256"],
        "raw_body_path": proof["raw_body_path"], "decoded_html_path": proof["decoded_html_path"]}
    sealed(run, "evidence", verify.canonical(rejected), ".json")
    receipt = result(fixture)
    assert receipt["artifact_integrity_pass"] is True
    assert receipt["runs"][0]["proof_schema_counts"]["last3f-rejected-source-proof-v1"] == 1


def test_separate_pilot_raw_and_replay_receipt_binding(tmp_path):
    source, replay = tmp_path / "probe", tmp_path / "pilot"
    source.mkdir(); replay.mkdir()
    body = source / (RID + ".html"); body.write_bytes(b"original public response")
    digest = verify.sha_bytes(body.read_bytes())
    write(source / "probe.json", {"schema": "public-last3f-three-probes-v1", "records": [
        {"race_id": RID, "body_path": str(body), "body_sha256": digest}]})
    write(replay / "receipt.json", {"schema": "last3f-public-probe-replay-v1", "results": [
        {"race_id": RID, "source_body_sha256": digest}]})
    receipt = verify.verify([], pilot_source_dir=source, pilot_receipt_dir=replay)
    assert receipt["artifact_integrity_pass"] is True
    assert receipt["pilot"]["decoded_html_not_separately_preserved"] is True
    body.write_bytes(b"tampered")
    assert verify.verify([], pilot_source_dir=source, pilot_receipt_dir=replay)["artifact_integrity_pass"] is False


def test_pilot_body_reference_must_stay_inside_its_explicit_source_dir(tmp_path):
    source, replay = tmp_path / "probe", tmp_path / "pilot"
    source.mkdir(); replay.mkdir()
    outside = tmp_path / "outside.html"; outside.write_bytes(b"outside")
    write(source / "probe.json", {"schema": "public-last3f-three-probes-v1", "records": [
        {"race_id": RID, "body_path": str(outside), "body_sha256": verify.sha_bytes(outside.read_bytes())}]})
    write(replay / "receipt.json", {"schema": "last3f-public-probe-replay-v1", "results": []})
    assert verify.verify([], pilot_source_dir=source, pilot_receipt_dir=replay)["issues"]["pilot_verification_failed"] == 1

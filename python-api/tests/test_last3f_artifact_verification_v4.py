"""Small synthetic equivalence/damage fixtures; no user DB, network or full audit."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_last3f_gap_artifacts as baseline
import verify_last3f_gap_artifacts_v4 as fast
from test_last3f_artifact_verification import fixture, sealed, write, rewrite_event


def compared(fixture):
    old = baseline.verify([fixture[0]], repository_root=fixture[1])
    new = fast.verify([fixture[0]], repository_root=fixture[1])
    for field in ("artifact_integrity_pass", "issues", "issues_examples", "runs", "pilot",
                  "unique_files_verified", "file_receipts", "historical_prestart_availability_proven",
                  "database_opened", "network_requests", "source_files_modified"):
        assert new[field] == old[field], field
    return old, new


def reseal(fixture):
    run, _, proof, _ = fixture
    path, digest = sealed(run, "evidence", baseline.canonical(proof), ".json")
    tail = rewrite_event(run, lambda event: event.update(proof_file=path, proof_sha256=digest))
    status = json.loads((run / "status.json").read_bytes())
    write(run / "status.json", {**status, "last_event_sha256": tail})


def test_equivalent_receipt_with_lower_physical_reads_and_identical_file_hashes(fixture):
    old, new = compared(fixture)
    assert new["artifact_integrity_pass"] is True
    assert new["counts"]["bytes_read"] < old["counts"]["bytes_read"]
    assert new["counts"]["file_reads"] < old["counts"]["file_reads"]
    assert new["optimization"]["duplicate_content_reads_avoided"] >= 2
    assert new["optimization"]["digest_cache_persistent"] is False


@pytest.mark.parametrize("damage", ["raw", "decoded", "missing_raw", "proof", "event",
    "event_tail", "code", "wrong_identity", "wrong_date", "wrong_runners", "plan_receipt",
    "outside_relative", "outside_absolute", "orphan", "wrong_filename", "rejected_authority",
    "unreferenced_valid", "broken_chain", "duplicate_target"])
def test_damage_and_unreferenced_evidence_have_same_outcome_and_issues(fixture, damage):
    run, repo, proof, proof_path = fixture
    if damage in {"raw", "decoded"}:
        key = "raw_body_path" if damage == "raw" else "decoded_html_path"
        (run / proof[key]).write_bytes(b"changed source")
    elif damage == "missing_raw":
        (run / proof["raw_body_path"]).unlink()
    elif damage == "proof":
        proof["evidence"]["rows"][0]["last3f_seconds"] = 91
        (run / proof_path).write_bytes(baseline.canonical(proof))
    elif damage == "event":
        path = run / "events.jsonl"
        path.write_bytes(path.read_bytes().replace(b'"completed"', b'"pending"'))
    elif damage == "event_tail":
        (run / "events.jsonl").write_bytes(b"")
    elif damage == "code":
        (repo / "parser.py").write_bytes(b"changed parser")
    elif damage in {"wrong_identity", "wrong_date", "wrong_runners"}:
        if damage == "wrong_identity":
            proof["evidence"]["rows"][0]["horse_id"] = "wrong"
        elif damage == "wrong_date":
            proof["evidence"]["race_date"] = "20260105"
        else:
            proof["evidence"]["runner_count"] = 2
        reseal(fixture)
    elif damage == "plan_receipt":
        value = json.loads((run / "plan_receipt.json").read_bytes())
        write(run / "plan_receipt.json", {**value, "plan_sha256": "0" * 64})
    elif damage.startswith("outside_"):
        proof["raw_body_path"] = "../outside.html" if damage.endswith("relative") else str(repo / "outside.html")
        reseal(fixture)
    elif damage == "orphan":
        sealed(run, "sources/raw", b"unreferenced", ".bin")
    elif damage == "wrong_filename":
        (run / "sources/raw" / ("0" * 64 + ".bin")).write_bytes(b"not that hash")
    elif damage == "rejected_authority":
        rejected = {"schema": "last3f-rejected-source-proof-v1", "race_id": proof["evidence"]["race_id"],
            "source_verified": False, "database_write_authority": True,
            "raw_body_sha256": proof["evidence"]["source"]["raw_body_sha256"],
            "decoded_html_sha256": proof["evidence"]["source"]["decoded_html_sha256"],
            "raw_body_path": proof["raw_body_path"], "decoded_html_path": proof["decoded_html_path"]}
        sealed(run, "evidence", baseline.canonical(rejected), ".json")
    elif damage == "unreferenced_valid":
        proof["evidence"]["rows"][0]["last3f_seconds"] = 35.2
        sealed(run, "evidence", baseline.canonical(proof), ".json")
    elif damage == "broken_chain":
        tail = rewrite_event(run, lambda event: event.update(previous_event_sha256="0" * 64))
        value = json.loads((run / "status.json").read_bytes())
        write(run / "status.json", {**value, "last_event_sha256": tail})
    elif damage == "duplicate_target":
        path = run / "targets.jsonl"
        path.write_bytes(path.read_bytes() * 2)
    old, new = compared(fixture)
    assert new["artifact_integrity_pass"] is (damage == "unreferenced_valid")


def test_repeated_reference_still_checks_each_expected_hash(tmp_path):
    path = tmp_path / "source.html"
    body = b"source bytes"
    path.write_bytes(body)
    checker = fast.FastChecker()
    digest = baseline.sha_bytes(body)
    checker.verify_blob(tmp_path, path.name, expected=digest)
    checker.verify_blob(tmp_path, path.name, expected="0" * 64)
    checker.verify_blob(tmp_path, path.name, expected="not-a-hash", content_addressed=True)
    assert checker.counts["file_reads"] == 1
    assert checker.issues["artifact_sha256_mismatch"] == 2
    assert checker.issues["content_address_filename_mismatch"] == 1
    assert checker.optimization["duplicate_content_reads_avoided"] == 2


def test_all_unique_raw_blobs_are_read_once_but_never_retained(tmp_path):
    checker = fast.FastChecker(body_cache_bytes=16, body_cache_item_bytes=8)
    for index in range(4):
        body = bytes([index]) * 4096
        path = tmp_path / (baseline.sha_bytes(body) + ".bin")
        path.write_bytes(body)
        checker.verify_blob(tmp_path, path.name, content_addressed=True)
        checker.verify_blob(tmp_path, path.name, content_addressed=True)
    assert checker.counts == {"file_reads": 4, "bytes_read": 16384}
    assert len(checker.files) == 4
    assert not checker._bodies and checker._body_bytes == 0
    assert not checker.issues


def test_json_body_cache_has_hard_total_and_item_limits(tmp_path):
    checker = fast.FastChecker(body_cache_bytes=16, body_cache_item_bytes=8)
    for index in range(5):
        name = str(index) + ".json"
        (tmp_path / name).write_bytes(b'{"a":1}')
        assert checker.json(tmp_path, name)[0] == {"a": 1}
        assert checker._body_bytes <= 16
    before = checker.counts["file_reads"]
    checker.json(tmp_path, "4.json")
    assert checker.counts["file_reads"] == before
    checker.json(tmp_path, "0.json")  # Evicted bytes must really be read again.
    assert checker.counts["file_reads"] == before + 1
    (tmp_path / "large.json").write_bytes(b'{"long":123456789}')
    checker.json(tmp_path, "large.json")
    assert str((tmp_path / "large.json").resolve()) not in checker._bodies
    assert checker.optimization["peak_cached_body_bytes"] <= 16


def test_changed_fingerprint_invalidates_digest_reuse(tmp_path):
    path = tmp_path / "body.html"
    path.write_bytes(b"first")
    checker = fast.FastChecker()
    checker.verify_blob(tmp_path, path.name)
    path.write_bytes(b"second value")
    checker.verify_blob(tmp_path, path.name, expected=baseline.sha_bytes(b"first"))
    assert checker.counts["file_reads"] == 2
    assert checker.issues["artifact_changed_between_references"] == 1
    assert checker.issues["artifact_sha256_mismatch"] == 1


def test_content_hash_is_not_substituted_by_metadata(tmp_path):
    path = tmp_path / "body.html"
    path.write_bytes(b"first")
    timestamp = path.stat().st_mtime_ns
    path.write_bytes(b"other")
    os.utime(path, ns=(timestamp, timestamp))
    checker = fast.FastChecker()
    checker.verify_blob(tmp_path, path.name, expected=baseline.sha_bytes(b"first"))
    assert checker.issues["artifact_sha256_mismatch"] == 1
    assert checker.counts["bytes_read"] == 5


def test_final_mutation_is_detected(fixture, monkeypatch):
    method = fast.FastChecker.final_stability
    def changed(checker):
        (fixture[0] / fixture[2]["raw_body_path"]).write_bytes(b"late mutation")
        method(checker)
    monkeypatch.setattr(fast.FastChecker, "final_stability", changed)
    receipt = fast.verify([fixture[0]], repository_root=fixture[1])
    assert not receipt["artifact_integrity_pass"]
    assert receipt["issues"]["artifact_changed_before_receipt"] == 1


def test_added_inventory_entry_is_detected(fixture, monkeypatch):
    original = fast.inventory
    calls = 0
    def inventory(run):
        nonlocal calls
        calls += 1
        if calls == 2:
            sealed(run, "sources/raw", b"late extra body", ".bin")
        return original(run)
    monkeypatch.setattr(fast, "inventory", inventory)
    receipt = fast.verify([fixture[0]], repository_root=fixture[1])
    assert not receipt["artifact_integrity_pass"]
    assert receipt["issues"]["run_source_inventory_changed_during_verification"] == 1


def test_symlink_cannot_be_used_on_a_repeated_reference(tmp_path):
    path = tmp_path / "body.html"
    path.write_bytes(b"first")
    checker = fast.FastChecker()
    checker.verify_blob(tmp_path, path.name)
    moved = tmp_path / "other.html"
    path.rename(moved)
    try:
        path.symlink_to(moved)
    except OSError:
        pytest.skip("This Windows account cannot create symlinks")
    with pytest.raises(ValueError, match="linked-reference"):
        checker.verify_blob(tmp_path, path.name)


def test_verifier_does_not_mutate_baseline_driver(fixture):
    before = {key: baseline.__dict__[key] for key in ("Checker", "verify", "verify_run", "inside", "inventory")}
    fast.verify([fixture[0]], repository_root=fixture[1])
    assert before == {key: baseline.__dict__[key] for key in before}


def test_cached_digest_does_not_skip_path_safety_even_without_symlink_privilege(tmp_path, monkeypatch):
    path = tmp_path / "body.html"
    path.write_bytes(b"source")
    checker = fast.FastChecker()
    checker.verify_blob(tmp_path, path.name)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda candidate: candidate == path or original(candidate))
    with pytest.raises(ValueError, match="linked-reference"):
        checker.verify_blob(tmp_path, path.name)
    assert checker.counts["file_reads"] == 1


def test_same_size_replacement_has_different_identity_and_is_not_reused(tmp_path):
    path = tmp_path / "body.html"
    path.write_bytes(b"first")
    stat = path.stat()
    checker = fast.FastChecker()
    checker.verify_blob(tmp_path, path.name)
    replacement = tmp_path / "replacement.html"
    replacement.write_bytes(b"other")
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    os.replace(replacement, path)
    checker.verify_blob(tmp_path, path.name, expected=baseline.sha_bytes(b"first"))
    assert checker.counts["file_reads"] == 2
    assert checker.issues["artifact_changed_between_references"] == 1
    assert checker.issues["artifact_sha256_mismatch"] == 1


def test_pilot_receipt_matches_baseline_with_tamper_check(tmp_path):
    source, replay = tmp_path / "source", tmp_path / "replay"
    source.mkdir(); replay.mkdir()
    body = source / "202605010101.html"
    body.write_bytes(b"original response")
    digest = baseline.sha_bytes(body.read_bytes())
    write(source / "probe.json", {"schema": "public-last3f-three-probes-v1", "records": [
        {"race_id": "202605010101", "body_path": str(body), "body_sha256": digest}]})
    write(replay / "receipt.json", {"schema": "last3f-public-probe-replay-v1", "results": [
        {"race_id": "202605010101", "source_body_sha256": digest}]})
    for damaged in (False, True):
        if damaged:
            body.write_bytes(b"tampered")
        old = baseline.verify([], pilot_source_dir=source, pilot_receipt_dir=replay)
        new = fast.verify([], pilot_source_dir=source, pilot_receipt_dir=replay)
        assert new["artifact_integrity_pass"] is not damaged
        for field in ("issues", "file_receipts", "pilot"):
            assert new[field] == old[field]


def test_invalid_cache_configuration_fails_closed():
    for value in (True, -1, "1024", 1.5):
        with pytest.raises(ValueError):
            fast.FastChecker(body_cache_bytes=value)
